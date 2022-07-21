import appdirs
import errno
import io
import logging
import os
import posixpath
import threading
from collections import defaultdict
from contextlib import contextmanager

from fsspec.spec import AbstractFileSystem
from funcy import cached_property, retry, wrap_prop, wrap_with
from funcy.py3 import cat
from tqdm.utils import CallbackIOWrapper

from pydrive2.drive import GoogleDrive
from pydrive2.fs.utils import IterStream
from pydrive2.auth import GoogleAuth

logger = logging.getLogger(__name__)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"

COMMON_SETTINGS = {
    "get_refresh_token": True,
    "oauth_scope": [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.appdata",
    ],
}


class GDriveAuthError(Exception):
    pass


def _gdrive_retry(func):
    def should_retry(exc):
        from pydrive2.files import ApiRequestError

        if not isinstance(exc, ApiRequestError):
            return False

        error_code = exc.error.get("code", 0)
        result = False
        if 500 <= error_code < 600:
            result = True

        if error_code == 403:
            result = exc.GetField("reason") in [
                "userRateLimitExceeded",
                "rateLimitExceeded",
            ]
        if result:
            logger.debug(f"Retrying GDrive API call, error: {exc}.")

        return result

    # 16 tries, start at 0.5s, multiply by golden ratio, cap at 20s
    return retry(
        16,
        timeout=lambda a: min(0.5 * 1.618**a, 20),
        filter_errors=should_retry,
    )(func)


@contextmanager
def _wrap_errors():
    try:
        yield
    except Exception as exc:
        # Handle AuthenticationError, RefreshError and other auth failures
        # It's hard to come up with a narrow exception, since PyDrive throws
        # a lot of different errors - broken credentials file, refresh token
        # expired, flow failed, etc.
        raise GDriveAuthError("Failed to authenticate GDrive") from exc


def _client_auth(
    client_id=None,
    client_secret=None,
    client_json=None,
    client_json_file_path=None,
    profile=None,
):
    if client_json:
        save_settings = {
            "save_credentials_backend": "dictionary",
            "save_credentials_dict": {"creds": client_json},
            "save_credentials_key": "creds",
        }
    else:
        creds_file = client_json_file_path
        if not creds_file:
            cache_dir = os.path.join(
                appdirs.user_cache_dir("pydrive2fs", appauthor=False),
                client_id,
            )
            os.makedirs(cache_dir, exist_ok=True)

            profile = profile or "default"
            creds_file = os.path.join(cache_dir, f"{profile}.json")

        save_settings = {
            "save_credentials_backend": "file",
            "save_credentials_file": creds_file,
        }

    settings = {
        **COMMON_SETTINGS,
        "save_credentials": True,
        **save_settings,
        "client_config_backend": "settings",
        "client_config": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "revoke_uri": "https://oauth2.googleapis.com/revoke",
            "redirect_uri": "",
        },
    }

    auth = GoogleAuth(settings=settings)

    with _wrap_errors():
        auth.LocalWebserverAuth()

    return auth


def _service_auth(
    client_user_email=None,
    client_json=None,
    client_json_file_path=None,
):
    settings = {
        **COMMON_SETTINGS,
        "client_config_backend": "service",
        "service_config": {
            "client_user_email": client_user_email,
            "client_json": client_json,
            "client_json_file_path": client_json_file_path,
        },
    }

    auth = GoogleAuth(settings=settings)

    with _wrap_errors():
        auth.ServiceAuth()

    return auth


class GDriveFileSystem(AbstractFileSystem):
    def __init__(
        self,
        path,
        google_auth=None,
        trash_only=True,
        client_id=None,
        client_secret=None,
        client_user_email=None,
        client_json=None,
        client_json_file_path=None,
        use_service_account=False,
        profile=None,
        **kwargs,
    ):
        """Access to gdrive as a file-system

        :param path: gdrive path.
        :type path: str.
        :param google_auth: Authenticated GoogleAuth instance.
        :type google_auth: GoogleAuth.
        :param trash_only: Move files to trash instead of deleting.
        :type trash_only: bool.
        :param client_id: Client ID of the application.
        :type client_id: str
        :param client_secret: Client secret of the application.
        :type client_secret: str.
        :param client_user_email: User email that authority was delegated to
            (only for service account).
        :type client_user_email: str.
        :param client_json: JSON keyfile loaded into a string.
        :type client_json: str.
        :param client_json_file_path: Path to JSON keyfile.
        :type client_json_file_path: str.
        :param use_service_account: Use service account.
        :type use_service_account: bool.
        :param profile: Profile name for caching credentials
            (ignored for service account).
        :type profile: str.
        :raises: GDriveAuthError
        """
        super().__init__(**kwargs)
        self.path = path
        self.root, self.base = self.split_path(self.path)

        if not google_auth:
            if (
                not client_json
                and not client_json_file_path
                and not (client_id and client_secret)
            ):
                raise ValueError(
                    "Specify credentials using one of these methods: "
                    "client_id/client_secret or "
                    "client_json or "
                    "client_json_file_path"
                )

            if use_service_account:
                google_auth = _service_auth(
                    client_json=client_json,
                    client_json_file_path=client_json_file_path,
                    client_user_email=client_user_email,
                )
            else:
                google_auth = _client_auth(
                    client_id=client_id,
                    client_secret=client_secret,
                    client_json=client_json,
                    client_json_file_path=client_json_file_path,
                    profile=profile,
                )

        self.client = GoogleDrive(google_auth)
        self._trash_only = trash_only

    def split_path(self, path):
        parts = path.replace("//", "/").rstrip("/").split("/", 1)
        if len(parts) == 2:
            return parts
        else:
            return parts[0], ""

    @wrap_prop(threading.RLock())
    @cached_property
    def _ids_cache(self):
        cache = {
            "dirs": defaultdict(list),
            "ids": {},
            "root_id": self._get_item_id(
                self.path,
                use_cache=False,
                hint="Confirm the directory exists and you can access it.",
            ),
        }

        self._cache_path_id(self.base, cache["root_id"], cache=cache)

        for item in self._gdrive_list(
            "'{}' in parents and trashed=false".format(cache["root_id"])
        ):
            item_path = posixpath.join(self.base, item["title"])
            self._cache_path_id(item_path, item["id"], cache=cache)

        return cache

    def _cache_path_id(self, path, *item_ids, cache=None):
        cache = cache or self._ids_cache
        for item_id in item_ids:
            cache["dirs"][path].append(item_id)
            cache["ids"][item_id] = path

    @cached_property
    def _list_params(self):
        params = {"corpora": "default"}
        if self.root != "root" and self.root != "appDataFolder":
            drive_id = self._gdrive_shared_drive_id(self.root)
            if drive_id:
                logger.debug(
                    "GDrive remote '{}' is using shared drive id '{}'.".format(
                        self.path, drive_id
                    )
                )
                params["driveId"] = drive_id
                params["corpora"] = "drive"
        return params

    @_gdrive_retry
    def _gdrive_shared_drive_id(self, item_id):
        from pydrive2.files import ApiRequestError

        param = {"id": item_id}
        # it does not create a file on the remote
        item = self.client.CreateFile(param)
        # ID of the shared drive the item resides in.
        # Only populated for items in shared drives.
        try:
            item.FetchMetadata("driveId")
        except ApiRequestError as exc:
            error_code = exc.error.get("code", 0)
            if error_code == 404:
                raise PermissionError from exc
            raise

        return item.get("driveId", None)

    def _gdrive_list(self, query):
        param = {"q": query, "maxResults": 1000}
        param.update(self._list_params)
        file_list = self.client.ListFile(param)

        # Isolate and decorate fetching of remote drive items in pages.
        get_list = _gdrive_retry(lambda: next(file_list, None))

        # Fetch pages until None is received, lazily flatten the thing.
        return cat(iter(get_list, None))

    def _gdrive_list_ids(self, query_ids):
        query = " or ".join(
            f"'{query_id}' in parents" for query_id in query_ids
        )
        query = f"({query}) and trashed=false"
        return self._gdrive_list(query)

    def _get_remote_item_ids(self, parent_ids, title):
        if not parent_ids:
            return None
        query = "trashed=false and ({})".format(
            " or ".join(
                f"'{parent_id}' in parents" for parent_id in parent_ids
            )
        )
        query += " and title='{}'".format(title.replace("'", "\\'"))

        # GDrive list API is case insensitive, we need to compare
        # all results and pick the ones with the right title
        return [
            item["id"]
            for item in self._gdrive_list(query)
            if item["title"] == title
        ]

    def _get_cached_item_ids(self, path, use_cache):
        if not path:
            return [self.root]
        if use_cache:
            return self._ids_cache["dirs"].get(path, [])
        return []

    def _path_to_item_ids(self, path, create=False, use_cache=True):
        item_ids = self._get_cached_item_ids(path, use_cache)
        if item_ids:
            return item_ids

        parent_path, title = posixpath.split(path)
        parent_ids = self._path_to_item_ids(parent_path, create, use_cache)
        item_ids = self._get_remote_item_ids(parent_ids, title)
        if item_ids:
            return item_ids

        return (
            [self._create_dir(min(parent_ids), title, path)] if create else []
        )

    def _get_item_id(self, path, create=False, use_cache=True, hint=None):
        bucket, base = self.split_path(path)
        assert bucket == self.root

        item_ids = self._path_to_item_ids(base, create, use_cache)
        if item_ids:
            return min(item_ids)

        assert not create
        raise FileNotFoundError(
            errno.ENOENT, os.strerror(errno.ENOENT), hint or path
        )

    @_gdrive_retry
    def _gdrive_create_dir(self, parent_id, title):
        parent = {"id": parent_id}
        item = self.client.CreateFile(
            {"title": title, "parents": [parent], "mimeType": FOLDER_MIME_TYPE}
        )
        item.Upload()
        return item

    @wrap_with(threading.RLock())
    def _create_dir(self, parent_id, title, remote_path):
        cached = self._ids_cache["dirs"].get(remote_path)
        if cached:
            return cached[0]

        item = self._gdrive_create_dir(parent_id, title)

        if parent_id == self._ids_cache["root_id"]:
            self._cache_path_id(remote_path, item["id"])

        return item["id"]

    def exists(self, path):
        try:
            self._get_item_id(path)
        except FileNotFoundError:
            return False
        else:
            return True

    @_gdrive_retry
    def info(self, path):
        bucket, base = self.split_path(path)
        item_id = self._get_item_id(path)
        gdrive_file = self.client.CreateFile({"id": item_id})
        gdrive_file.FetchMetadata()

        metadata = {"name": posixpath.join(bucket, base.rstrip("/"))}
        if gdrive_file["mimeType"] == FOLDER_MIME_TYPE:
            metadata["type"] = "directory"
            metadata["size"] = 0
            metadata["name"] += "/"
        else:
            metadata["type"] = "file"
            metadata["size"] = int(gdrive_file.get("fileSize"))
            metadata["checksum"] = gdrive_file.get("md5Checksum")
        return metadata

    def ls(self, path, detail=False):
        bucket, base = self.split_path(path)

        cached = base in self._ids_cache["dirs"]
        if cached:
            dir_ids = self._ids_cache["dirs"][base]
        else:
            dir_ids = self._path_to_item_ids(base)

        if not dir_ids:
            return None

        root_path = posixpath.join(bucket, base)
        contents = []
        for item in self._gdrive_list_ids(dir_ids):
            item_path = posixpath.join(root_path, item["title"])
            if item["mimeType"] == FOLDER_MIME_TYPE:
                contents.append(
                    {
                        "type": "directory",
                        "name": item_path.rstrip("/") + "/",
                        "size": 0,
                    }
                )
            else:
                contents.append(
                    {
                        "type": "file",
                        "name": item_path,
                        "size": int(item["fileSize"]),
                        "checksum": item.get("md5Checksum"),
                    }
                )

        if not cached:
            self._cache_path_id(root_path, *dir_ids)

        if detail:
            return contents
        else:
            return [content["name"] for content in contents]

    def find(self, path, detail=False, **kwargs):
        bucket, base = self.split_path(path)

        seen_paths = set()
        dir_ids = [self._ids_cache["ids"].copy()]
        contents = []
        while dir_ids:
            query_ids = {
                dir_id: dir_name
                for dir_id, dir_name in dir_ids.pop().items()
                if posixpath.commonpath([base, dir_name]) == base
                if dir_id not in seen_paths
            }
            if not query_ids:
                continue

            seen_paths |= query_ids.keys()

            new_query_ids = {}
            dir_ids.append(new_query_ids)
            for item in self._gdrive_list_ids(query_ids):
                parent_id = item["parents"][0]["id"]
                item_path = posixpath.join(query_ids[parent_id], item["title"])
                if item["mimeType"] == FOLDER_MIME_TYPE:
                    new_query_ids[item["id"]] = item_path
                    self._cache_path_id(item_path, item["id"])
                    continue

                contents.append(
                    {
                        "name": posixpath.join(bucket, item_path),
                        "type": "file",
                        "size": int(item["fileSize"]),
                        "checksum": item.get("md5Checksum"),
                    }
                )

        if detail:
            return {content["name"]: content for content in contents}
        else:
            return [content["name"] for content in contents]

    def upload_fobj(self, stream, rpath, callback=None, **kwargs):
        parent_id = self._get_item_id(self._parent(rpath), create=True)
        if callback:
            stream = CallbackIOWrapper(
                callback.relative_update, stream, "read"
            )
        return self._gdrive_upload_fobj(
            posixpath.basename(rpath.rstrip("/")), parent_id, stream
        )

    def put_file(self, lpath, rpath, callback=None, **kwargs):
        if callback:
            callback.set_size(os.path.getsize(lpath))
        with open(lpath, "rb") as stream:
            self.upload_fobj(stream, rpath, callback=callback)

    @_gdrive_retry
    def _gdrive_upload_fobj(self, title, parent_id, stream, callback=None):
        item = self.client.CreateFile(
            {"title": title, "parents": [{"id": parent_id}]}
        )
        item.content = stream
        item.Upload()
        return item

    def cp_file(self, lpath, rpath, **kwargs):
        """In-memory streamed copy"""
        with self.open(lpath) as stream:
            # IterStream objects doesn't support full-length
            # seek() calls, so we have to wrap the data with
            # an external buffer.
            buffer = io.BytesIO(stream.read())
            self.upload_fobj(buffer, rpath)

    def get_file(self, lpath, rpath, callback=None, block_size=None, **kwargs):
        item_id = self._get_item_id(lpath)
        return self._gdrive_get_file(
            item_id, rpath, callback=callback, block_size=block_size
        )

    @_gdrive_retry
    def _gdrive_get_file(self, item_id, rpath, callback=None, block_size=None):
        param = {"id": item_id}
        # it does not create a file on the remote
        gdrive_file = self.client.CreateFile(param)

        extra_args = {}
        if block_size:
            extra_args["chunksize"] = block_size

        if callback:

            def cb(value, _):
                callback.absolute_update(value)

            gdrive_file.FetchMetadata(fields="fileSize")
            callback.set_size(int(gdrive_file.get("fileSize")))
            extra_args["callback"] = cb

        gdrive_file.GetContentFile(rpath, **extra_args)

    def _open(self, path, mode, **kwargs):
        assert mode in {"rb", "wb"}
        if mode == "wb":
            return GDriveBufferedWriter(self, path)
        else:
            item_id = self._get_item_id(path)
            return self._gdrive_open_file(item_id)

    @_gdrive_retry
    def _gdrive_open_file(self, item_id):
        param = {"id": item_id}
        # it does not create a file on the remote
        gdrive_file = self.client.CreateFile(param)
        fd = gdrive_file.GetContentIOBuffer()
        return IterStream(iter(fd))

    def rm_file(self, path):
        item_id = self._get_item_id(path)
        self._gdrive_delete_file(item_id)

    @_gdrive_retry
    def _gdrive_delete_file(self, item_id):
        from pydrive2.files import ApiRequestError

        param = {"id": item_id}
        # it does not create a file on the remote
        item = self.client.CreateFile(param)

        try:
            item.Trash() if self._trash_only else item.Delete()
        except ApiRequestError as exc:
            http_error_code = exc.error.get("code", 0)
            if (
                http_error_code == 403
                and self._list_params["corpora"] == "drive"
                and exc.GetField("location") == "file.permissions"
            ):
                raise PermissionError(
                    "Insufficient permissions to {}. You should have {} "
                    "access level for the used shared drive. More details "
                    "at {}.".format(
                        "move the file into Trash"
                        if self._trash_only
                        else "permanently delete the file",
                        "Manager or Content Manager"
                        if self._trash_only
                        else "Manager",
                        "https://support.google.com/a/answer/7337554",
                    )
                ) from exc
            raise


class GDriveBufferedWriter(io.IOBase):
    def __init__(self, fs, path):
        self.fs = fs
        self.path = path
        self.buffer = io.BytesIO()
        self._closed = False

    def write(self, *args, **kwargs):
        self.buffer.write(*args, **kwargs)

    def readable(self):
        return False

    def writable(self):
        return not self.readable()

    def flush(self):
        self.buffer.flush()
        try:
            self.fs.upload_fobj(self.buffer, self.path)
        finally:
            self._closed = True

    def close(self):
        if self._closed:
            return None

        self.flush()
        self.buffer.close()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()

    @property
    def closed(self):
        return self._closed
