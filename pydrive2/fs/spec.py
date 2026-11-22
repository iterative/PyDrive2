import errno
import io
import logging
import os
import posixpath
import tempfile
import threading
from contextlib import contextmanager
from itertools import chain
from typing import TypedDict

import appdirs
from fsspec.spec import AbstractFileSystem
from funcy import (
    chunks,
    distinct,
    retry,
    wrap_with,
)
from tqdm.utils import CallbackIOWrapper

from pydrive2.auth import GoogleAuth, InvalidCredentialsError
from pydrive2.drive import GoogleDrive
from pydrive2.fs.utils import IterStream

logger = logging.getLogger(__name__)

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
_INFO_FIELDS = "mimeType,size,md5Checksum"
_LIST_FIELDS = f"nextPageToken,files(id,name,parents,{_INFO_FIELDS})"
_PARENT_QUERY_BATCH_SIZE = 50

COMMON_SETTINGS = {
    "get_refresh_token": True,
    "oauth_scope": [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.appdata",
    ],
}


class _IdsCache(TypedDict):
    dirs: dict[str, list[str]]
    ids: dict[str, str]


def _in_subtrees(path: str, roots: tuple[str, ...]) -> bool:
    return any(
        not root or path == root or path.startswith(root + "/")
        for root in roots
    )


def _without_subtrees(cache: _IdsCache, roots: tuple[str, ...]) -> _IdsCache:
    # In-flight lookups can still append to the old lists.
    dirs = {
        path: ids.copy()
        for path, ids in cache["dirs"].items()
        if not _in_subtrees(path, roots)
    }
    paths_by_id = {
        item_id: path for path, ids in dirs.items() for item_id in ids
    }
    return {"dirs": dirs, "ids": paths_by_id}


class GDriveAuthError(Exception):
    pass


def _directory_entry(name):
    return {"name": name, "type": "directory", "size": 0}


def _file_entry(name, item):
    if item["mimeType"] == FOLDER_MIME_TYPE:
        return _directory_entry(name.rstrip("/") + "/")
    size = item.get("size")
    return {
        "name": name,
        "type": "file",
        "size": int(size) if size is not None else None,
        "checksum": item.get("md5Checksum"),
    }


def _quote_query_value(value):
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


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
def _translate_not_found(path):
    from pydrive2.files import ApiRequestError

    try:
        yield
    except ApiRequestError as exc:
        if exc.error.get("code") != 404:
            raise
        raise FileNotFoundError(
            errno.ENOENT, os.strerror(errno.ENOENT), path
        ) from exc


@contextmanager
def _wrap_errors():
    try:
        yield
    except InvalidCredentialsError as exc:
        raise GDriveAuthError(str(exc)) from exc
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
                "v2",
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
    """Access to gdrive as an fsspec filesystem"""

    protocol = "gdrive"

    def __init__(
        self,
        path="root",
        google_auth=None,
        client_id=None,
        client_secret=None,
        client_user_email=None,
        client_json=None,
        client_json_file_path=None,
        use_service_account=False,
        profile=None,
        trash_only=True,
        acknowledge_abuse=False,
        **kwargs,
    ):
        """Create an instance of GDriveFileSystem.

        :param path: gdrive path. Defaults to ``"root"`` (the Drive
            root), so ``fsspec`` can build the filesystem without one.
        :type path: str.
        :param google_auth: Authenticated GoogleAuth instance.
        :type google_auth: GoogleAuth.
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
        :param trash_only: Move files to trash instead of deleting.
        :type trash_only: bool.
        :param acknowledge_abuse: Acknowledging the risk and download file
            identified as abusive.
        :type acknowledge_abuse: bool
        :type profile: str.
        :raises: GDriveAuthError
        """
        super().__init__(**kwargs)
        self._cache_lock = threading.RLock()
        self._directory_ids: _IdsCache | None = None
        self._root_metadata: dict = {}
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
        self._acknowledge_abuse = acknowledge_abuse

    @classmethod
    def _get_kwargs_from_urls(cls, path):
        # A filesystem caches names relative to a single Drive root.
        root, _, _ = cls._strip_protocol(path).partition("/")
        return {} if root in {"", "root"} else {"path": root}

    def split_path(self, path):
        path = self._strip_protocol(path).replace("//", "/")
        root, _, base = path.partition("/")
        return root or "root", base

    def _split_mounted_path(self, path):
        root, base = self.split_path(path)
        if root != self.root:
            raise ValueError(
                f"Path {path!r} is outside Drive root {self.root!r}"
            )
        return root, base

    def _split_file_path(self, path):
        root, base = self._split_mounted_path(path)
        if not base:
            raise ValueError(
                "To create a Drive file, specify a folder ID and name"
            )
        parent, name = posixpath.split(base)
        return posixpath.join(root, parent), name

    @property
    def _ids_cache(self) -> _IdsCache:
        with self._cache_lock:
            if self._directory_ids is None:
                self._directory_ids = self._load_directory_ids()
            return self._directory_ids

    def _load_directory_ids(self) -> _IdsCache:
        cache: _IdsCache = {"dirs": {}, "ids": {}}

        self._cache_path_id(
            self.base, *self._mount_directory_ids(), cache=cache
        )
        return cache

    def _mount_directory_ids(self):
        if not self.base:
            return [self.root]
        parent, name = posixpath.split(self.base)
        parent_ids = self._path_to_item_ids(parent, use_cache=False)
        items = list(self._named_items(parent_ids, name))
        if not items:
            raise FileNotFoundError(
                errno.ENOENT,
                os.strerror(errno.ENOENT),
                f"Confirm {self.path} exists and you can access it",
            )
        return [
            item["id"]
            for item in items
            if item["mimeType"] == FOLDER_MIME_TYPE
        ]

    def _named_items(self, parent_ids, name):
        # Drive's name query is case-insensitive.
        for item in self._gdrive_list_ids(parent_ids, name=name):
            if item["name"] == name:
                yield item

    def _cache_path_id(self, path, *item_ids, cache=None):
        with self._cache_lock:
            cache = self._ids_cache if cache is None else cache
            for item_id in item_ids:
                old_path = cache["ids"].get(item_id)
                if old_path == path:
                    continue
                if old_path is not None:
                    cache["dirs"][old_path].remove(item_id)
                    if not cache["dirs"][old_path]:
                        del cache["dirs"][old_path]
                cache["ids"][item_id] = path
                cache["dirs"].setdefault(path, []).append(item_id)

    def invalidate_cache(self, path=None):
        self._invalidate_paths(path)

    def _invalidate_paths(self, *paths):
        subtrees = tuple(
            self._split_mounted_path(p)[1] if p else "" for p in paths
        )
        for path in paths:
            super().invalidate_cache(path)
        with self._cache_lock:
            if "" in subtrees:
                self._root_metadata = {}
            if _in_subtrees(self.base, subtrees):
                self._directory_ids = None
            elif self._directory_ids is not None:
                self._directory_ids = _without_subtrees(
                    self._directory_ids, subtrees
                )

    def _is_cached_directory(self, item_id):
        with self._cache_lock:
            cache = self._directory_ids
            return cache is not None and item_id in cache["ids"]

    @property
    def _list_params(self):
        params = {"corpora": "user"}
        if self.root == "appDataFolder":
            params["spaces"] = "appDataFolder"
        elif self.root != "root":
            try:
                drive_id = self._get_root_metadata().get("driveId")
            except FileNotFoundError as exc:
                raise PermissionError from exc
            if drive_id:
                params.update(driveId=drive_id, corpora="drive")
        return params

    @_gdrive_retry
    def _get_root_metadata(self):
        with self._cache_lock:
            # A root invalidation during the request replaces this dictionary.
            metadata = self._root_metadata
            if not metadata:
                item = self.client.CreateFile({"id": self.root})
                with _translate_not_found(self.root):
                    item.FetchMetadata("driveId,mimeType,trashed")
                if item.get("trashed"):
                    return item.metadata
                metadata.update(item.metadata)
                if drive_id := metadata.get("driveId"):
                    logger.debug(
                        "GDrive remote %r is using shared drive id %r.",
                        self.path,
                        drive_id,
                    )
            return metadata

    def _gdrive_list(self, query):
        param = {
            "q": query,
            "pageSize": 1000,
            "fields": _LIST_FIELDS,
        }
        param.update(self._list_params)
        file_list = self.client.ListFile(param)

        # Isolate and decorate fetching of remote drive items in pages.
        get_list = _gdrive_retry(lambda: next(file_list, None))

        # Fetch pages until None is received, lazily flatten the thing.
        return chain.from_iterable(iter(get_list, None))

    def _gdrive_list_ids(self, query_ids, *, name=None):
        filters = "trashed=false"
        if name is not None:
            filters += f" and name={_quote_query_value(name)}"
        # Drive can reject large parent queries. Query each parent only once.
        seen_ids = set()
        for batch in chunks(_PARENT_QUERY_BATCH_SIZE, distinct(query_ids)):
            parents = " or ".join(
                f"{_quote_query_value(parent)} in parents" for parent in batch
            )
            for item in self._gdrive_list(f"({parents}) and {filters}"):
                file_id = item["id"]
                if file_id in seen_ids:
                    continue
                # Only a full batch can be followed by another parent query.
                if len(batch) == _PARENT_QUERY_BATCH_SIZE:
                    seen_ids.add(file_id)
                yield item

    def _get_remote_item_ids(
        self, parent_ids, parent_path, name, use_cache=True, cache=None
    ):
        if not parent_ids:
            return None
        if use_cache and cache is None:
            cache = self._ids_cache
        res = []
        for item in self._named_items(parent_ids, name):
            res.append(item["id"])
            if item["mimeType"] == FOLDER_MIME_TYPE and use_cache:
                self._cache_path_id(
                    posixpath.join(parent_path, name), item["id"], cache=cache
                )

        return res

    def _get_cached_item_ids(self, path, use_cache, cache=None):
        if not path:
            return [self.root]
        if use_cache:
            # Another caller can add parents while a batched lookup is running.
            with self._cache_lock:
                cache = self._ids_cache if cache is None else cache
                return cache["dirs"].get(path, []).copy()
        return []

    def _path_to_item_ids(
        self, path, create=False, use_cache=True, cache=None
    ):
        if not path:
            return [self.root]
        if use_cache and cache is None:
            cache = self._ids_cache
        item_ids = self._get_cached_item_ids(path, use_cache, cache)
        if item_ids:
            return item_ids

        parent_path, name = posixpath.split(path)
        parent_ids = self._path_to_item_ids(
            parent_path, create, use_cache, cache
        )
        item_ids = self._get_remote_item_ids(
            parent_ids, parent_path, name, use_cache, cache
        )
        if item_ids:
            return item_ids

        return (
            [self._create_dir(min(parent_ids), name, path, cache=cache)]
            if create
            else []
        )

    def _get_item_id(self, path, create=False, use_cache=True):
        _, base = self._split_mounted_path(path)

        item_ids = self._path_to_item_ids(base, create, use_cache)
        if item_ids:
            return min(item_ids)

        assert not create
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)

    @_gdrive_retry
    def _gdrive_create_dir(self, parent_id, name):
        item = self.client.CreateFile(
            {
                "name": name,
                "parents": [parent_id],
                "mimeType": FOLDER_MIME_TYPE,
            }
        )
        item.Upload()
        return item

    @wrap_with(threading.RLock())
    def _create_dir(
        self, parent_id, name, remote_path, *, cache=None, exist_ok=True
    ):
        cache = self._ids_cache if cache is None else cache
        cached = self._get_cached_item_ids(remote_path, True, cache)
        if cached:
            if not exist_ok:
                raise FileExistsError(posixpath.join(self.root, remote_path))
            return cached[0]

        item = self._gdrive_create_dir(parent_id, name)
        self._cache_path_id(remote_path, item["id"], cache=cache)

        return item["id"]

    def mkdir(self, path, create_parents=True, **kwargs):
        self._mkdir(path, create_parents, exist_ok=False)

    def makedirs(self, path, exist_ok=False):
        self._mkdir(path, create_parents=True, exist_ok=exist_ok)

    def _mkdir(self, path, create_parents, exist_ok):
        root, base = self._split_mounted_path(path)
        if not base:
            if root not in {"root", "appDataFolder"}:
                metadata = self._get_root_metadata()
                if metadata.get("trashed"):
                    raise FileNotFoundError(
                        errno.ENOENT, os.strerror(errno.ENOENT), path
                    )
                if metadata.get("mimeType") != FOLDER_MIME_TYPE:
                    raise FileExistsError(path)
            if not exist_ok:
                raise FileExistsError(path)
            return

        cache = self._ids_cache
        parent = base
        missing = []
        while not (
            parent_ids := self._get_cached_item_ids(parent, True, cache)
        ):
            parent, name = posixpath.split(parent)
            missing.append(name)
        if not missing and not exist_ok:
            raise FileExistsError(path)

        for name in reversed(missing):
            child = posixpath.join(parent, name)
            leaf = child == base
            matches = list(self._named_items(parent_ids, name))
            dirs = [
                item["id"]
                for item in matches
                if item["mimeType"] == FOLDER_MIME_TYPE
            ]
            if dirs:
                self._cache_path_id(child, *dirs, cache=cache)
                if leaf and not exist_ok:
                    raise FileExistsError(path)
            elif matches:
                if leaf:
                    raise FileExistsError(path)
                raise NotADirectoryError(posixpath.join(root, child))
            else:
                if not leaf and not create_parents:
                    raise FileNotFoundError(posixpath.join(root, child))
                item_id = self._create_dir(
                    min(parent_ids),
                    name,
                    child,
                    cache=cache,
                    exist_ok=not leaf or exist_ok,
                )
                dirs = [item_id]
            parent, parent_ids = child, dirs

    def exists(self, path):
        root, base = self._split_mounted_path(path)
        try:
            if not base and root not in {"root", "appDataFolder"}:
                self.info(path)
            else:
                with _translate_not_found(path):
                    self._get_item_id(path)
        except FileNotFoundError:
            return False
        else:
            return True

    def info(self, path):
        with _translate_not_found(path):
            item_id = self._get_item_id(path)
        return self._info_by_id(path, item_id)

    @_gdrive_retry
    def _info_by_id(self, path, item_id):
        bucket, base = self.split_path(path)
        with _translate_not_found(path):
            gdrive_file = self.client.CreateFile({"id": item_id})
            fields = _INFO_FIELDS if base else f"{_INFO_FIELDS},trashed"
            gdrive_file.FetchMetadata(fields=fields)
        if not base and gdrive_file.get("trashed"):
            raise FileNotFoundError(
                errno.ENOENT, os.strerror(errno.ENOENT), path
            )

        return _file_entry(
            posixpath.join(bucket, base).rstrip("/"), gdrive_file
        )

    def ls(self, path, detail=False):
        bucket, base = self._split_mounted_path(path)

        dir_ids = self._path_to_item_ids(base)
        if not dir_ids:
            raise FileNotFoundError(
                errno.ENOENT, os.strerror(errno.ENOENT), path
            )

        root_path = posixpath.join(bucket, base)
        contents = []
        for item in self._gdrive_list_ids(dir_ids):
            item_path = posixpath.join(root_path, item["name"])
            contents.append(_file_entry(item_path, item))

        if detail:
            return contents
        else:
            return [content["name"] for content in contents]

    def find(
        self, path, maxdepth=None, withdirs=False, detail=False, **kwargs
    ):
        # PyDrive2 historically accepted a boolean detail as its second argument.
        if isinstance(maxdepth, bool):
            detail = detail or maxdepth
            maxdepth = None
        root, base = self._split_mounted_path(path)
        if maxdepth is not None and maxdepth < 0:
            raise ValueError("maxdepth must be nonnegative")
        # Older fsspec expand_path passes zero for a single-level listing.
        if maxdepth == 0:
            maxdepth = 1
        cache = self._ids_cache
        start_ids = self._path_to_item_ids(base, cache=cache)
        if not start_ids:
            return {} if detail else []

        # Warm directories at different depths can share a single batched
        # query. Measure depth from the requested path, not the batch number.
        prefix = base + "/" if base else ""

        def descend(name):
            if name == base:
                return True
            if not name.startswith(prefix):
                return False
            return (
                maxdepth is None
                or name[len(prefix) :].count("/") + 1 < maxdepth
            )

        with self._cache_lock:
            known = cache["ids"].copy()
        if not base:
            known[root] = ""
        pending = {
            item_id: name for item_id, name in known.items() if descend(name)
        }
        start = posixpath.join(root, base).rstrip("/")
        known_dir = (
            any(known.get(item_id) == base for item_id in start_ids)
            if base
            else root in {"root", "appDataFolder"}
        )
        root_type = None
        if not base and not known_dir:
            try:
                metadata = self._get_root_metadata()
            except FileNotFoundError:
                return {} if detail else []
            if metadata.get("trashed"):
                return {} if detail else []
            root_type = metadata.get("mimeType")
            if root_type != FOLDER_MIME_TYPE:
                pending = {}

        contents = []
        seen: set[str] = set()
        while pending:
            query_ids = pending
            seen.update(query_ids)
            pending = {}
            for item in self._gdrive_list_ids(query_ids):
                # Drive returns the canonical ID when the query uses a root alias.
                parent_id = next(
                    (p for p in item.get("parents", ()) if p in query_ids),
                    None,
                )
                if parent_id is None:
                    if (
                        root not in {"root", "appDataFolder"}
                        or root not in query_ids
                    ):
                        continue
                    parent_id = root
                known_dir = True
                item_path = posixpath.join(query_ids[parent_id], item["name"])
                name = posixpath.join(root, item_path)
                if item["mimeType"] == FOLDER_MIME_TYPE:
                    item_id = item["id"]
                    self._cache_path_id(item_path, item_id, cache=cache)
                    if item_id not in seen and descend(item_path):
                        pending[item_id] = item_path
                    if withdirs:
                        contents.append(_directory_entry(name))
                else:
                    contents.append(_file_entry(name, item))

        if not known_dir and (withdirs or root_type != FOLDER_MIME_TYPE):
            # Without children, an untyped starting path can be a file or
            # an empty directory. Check current metadata (including trash).
            try:
                entry = self._info_by_id(path, min(start_ids))
            except FileNotFoundError:
                return {} if detail else []
            if entry["type"] == "file":
                return {start: entry} if detail else [start]

        if withdirs:
            contents.insert(0, _directory_entry(start))
        if detail:
            return {entry["name"]: entry for entry in contents}
        return [entry["name"] for entry in contents]

    def upload_fobj(self, stream, rpath, callback=None, **kwargs):
        parent, name = self._split_file_path(rpath)
        parent_id = self._get_item_id(parent, create=True)
        if callback:
            stream = CallbackIOWrapper(
                callback.relative_update, stream, "read"
            )
        return self._gdrive_upload_fobj(name, parent_id, stream)

    def put_file(self, lpath, rpath, callback=None, **kwargs):
        if callback:
            callback.set_size(os.path.getsize(lpath))
        with open(lpath, "rb") as stream:
            self.upload_fobj(stream, rpath, callback=callback)

    @_gdrive_retry
    def _gdrive_upload_fobj(self, name, parent_id, stream, callback=None):
        item = self.client.CreateFile({"name": name, "parents": [parent_id]})
        item.content = stream
        item.Upload()
        return item

    def cp_file(self, lpath, rpath, **kwargs):
        """Ask Drive to copy the file directly into the destination folder."""
        parent, name = self._split_file_path(rpath)
        source_id = self._get_item_id(lpath)
        parent_id = self._get_item_id(parent, create=True)
        self._gdrive_copy_file(source_id, parent_id, name)

    @_gdrive_retry
    def _gdrive_copy_file(self, source_id, parent_id, name):
        source = self.client.CreateFile({"id": source_id})
        source.Copy(
            target_folder={"id": parent_id},
            new_title=name,
            param={"fields": "id"},
        )

    @_gdrive_retry
    def mv(self, path1, path2, maxdepth=None, **kwargs):
        if maxdepth is not None:
            raise NotImplementedError("Max depth move is not supported")

        path1 = posixpath.join(*self._split_mounted_path(path1)).rstrip("/")
        path2 = posixpath.join(*self._split_mounted_path(path2)).rstrip("/")
        src_name = posixpath.basename(path1)
        src_parent = self._parent(path1)

        if self.exists(path2):
            dst_name = src_name
            dst_parent = path2
        else:
            dst_name = posixpath.basename(path2)
            dst_parent = self._parent(path2)

        file1_id = self._get_item_id(path1)

        file1 = self.client.CreateFile({"id": file1_id})

        if src_name != dst_name:
            file1["name"] = dst_name

        if src_parent != dst_parent:
            file2_parent_id = self._get_item_id(dst_parent)
            file1["parents"] = [file2_parent_id]

        file1.Upload()
        if file1.get("mimeType") == FOLDER_MIME_TYPE:
            self._invalidate_paths(path1, posixpath.join(dst_parent, dst_name))

    def get_file(self, rpath, lpath, callback=None, block_size=None, **kwargs):
        with _translate_not_found(rpath):
            item_id = self._get_item_id(rpath)
            return self._gdrive_get_file(
                item_id, lpath, callback=callback, block_size=block_size
            )

    @_gdrive_retry
    def _gdrive_get_file(self, item_id, rpath, callback=None, block_size=None):
        param = {"id": item_id}
        # it does not create a file on the remote
        gdrive_file = self.client.CreateFile(param)

        extra_args = {"acknowledge_abuse": self._acknowledge_abuse}
        if block_size:
            extra_args["chunksize"] = block_size

        if callback:
            reported_size = None

            def cb(value, total):
                nonlocal reported_size
                if total is not None and total != reported_size:
                    callback.set_size(total)
                    reported_size = total
                callback.absolute_update(value)

            extra_args["callback"] = cb

        gdrive_file.GetContentFile(rpath, **extra_args)

    def _open(self, path, mode, block_size=None, **kwargs):
        assert mode in {"rb", "wb"}
        if mode == "wb":
            self._split_file_path(path)
            return GDriveBufferedWriter(self, path)
        with _translate_not_found(path):
            item_id = self._get_item_id(path)
            return self._gdrive_open_file(item_id, block_size)

    @_gdrive_retry
    def _gdrive_open_file(self, item_id, block_size=None):
        param = {"id": item_id}
        # it does not create a file on the remote
        gdrive_file = self.client.CreateFile(param)
        options = {}
        if block_size not in (None, "default") and block_size > 0:
            options["chunksize"] = block_size
        fd = gdrive_file.GetContentIOBuffer(
            acknowledge_abuse=self._acknowledge_abuse, **options
        )
        return IterStream(iter(fd))

    def rm_file(self, path):
        item_id = self._get_item_id(path)
        self._gdrive_delete_file(item_id)
        if item_id == self.root or self._is_cached_directory(item_id):
            self.invalidate_cache(path)

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
                and (
                    exc.GetField("reason") == "insufficientFilePermissions"
                    or exc.GetField("location") == "file.permissions"
                )
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


class GDriveBufferedWriter(io.BufferedIOBase):
    """Stage a seekable body for Drive's resumable upload and send it on close."""

    def __init__(self, fs, path):
        super().__init__()
        self.fs = fs
        self.path = path
        self._memory_limit: int | None = fs.blocksize
        # The writer owns the spool until close(), including failed uploads.
        self.buffer = tempfile.SpooledTemporaryFile(  # noqa: SIM115
            max_size=fs.blocksize
        )

    def writable(self):
        return self.buffer.writable()

    def write(self, data):
        self._checkClosed()
        # Roll before copying data into BytesIO. Once on disk, skip the size check.
        if self._memory_limit is not None and (
            self.buffer.tell() + memoryview(data).nbytes > self._memory_limit
        ):
            self.buffer.rollover()
            self._memory_limit = None
        return self.buffer.write(data)

    def flush(self):
        self.buffer.flush()

    def close(self):
        if self.closed:
            return
        with self.buffer:
            try:
                self.buffer.seek(0)
                self.fs.upload_fobj(self.buffer, self.path)
            finally:
                super().close()
