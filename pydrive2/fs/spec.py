import errno
import io
import logging
import os
import posixpath
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from itertools import chain
from weakref import WeakValueDictionary

import appdirs
from fsspec.spec import AbstractFileSystem
from funcy import (
    chunks,
    distinct,
    retry,
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


@dataclass
class _DirectoryCache:
    by_path: dict[str, list[str]] = field(default_factory=dict)
    by_id: dict[str, str] = field(default_factory=dict)
    mount_checked: bool = False

    def without_subtrees(self, roots, *, keep_roots=False):
        kept = _DirectoryCache(mount_checked=self.mount_checked)
        for path, ids in self.by_path.items():
            if (keep_roots and path in roots) or not _in_subtrees(path, roots):
                kept.by_path[path] = ids.copy()
                kept.by_id.update(dict.fromkeys(ids, path))
        return kept


@dataclass
class _PathMatch:
    folders: list[str]
    files: list[dict] = field(default_factory=list)

    @property
    def ids(self):
        return self.folders + [item["id"] for item in self.files]


def _in_subtrees(path: str, roots: tuple[str, ...]) -> bool:
    return any(
        not root or path == root or path.startswith(root + "/")
        for root in roots
    )


class GDriveAuthError(Exception):
    pass


def _file_entry(name, item):
    if item["mimeType"] == FOLDER_MIME_TYPE:
        return {"name": name.rstrip("/") + "/", "type": "directory", "size": 0}
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
        self._directory_ids = _DirectoryCache()
        self._mount_lock = threading.RLock()
        self._root_lock = threading.RLock()
        self._root_metadata: dict = {}
        self._creation_locks: WeakValueDictionary = WeakValueDictionary()
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

    def _directory_cache(self):
        with self._cache_lock:
            cache = self._directory_ids
            if cache.mount_checked:
                return cache, None
        with self._mount_lock:
            with self._cache_lock:
                cache = self._directory_ids
                if cache.mount_checked:
                    return cache, None
            mount = self._resolve_path(self.base, cache)
            if not mount.ids:
                raise FileNotFoundError(
                    errno.ENOENT,
                    os.strerror(errno.ENOENT),
                    f"Confirm {self.path} exists and you can access it",
                )
            with self._cache_lock:
                cache.mount_checked = True
            return cache, mount

    def _cache_path_id(self, cache, path, *item_ids):
        with self._cache_lock:
            for item_id in item_ids:
                old_path = cache.by_id.get(item_id)
                if old_path == path:
                    continue
                if old_path is not None:
                    # Either name can also belong to a different folder.
                    # Recheck their descendants against the remaining IDs.
                    kept = cache.without_subtrees(
                        (old_path, path), keep_roots=True
                    )
                    if cache is self._directory_ids:
                        self._directory_ids = kept
                        if _in_subtrees(self.base, (old_path,)):
                            kept.mount_checked = False
                    cache = kept
                    cache.by_path[old_path].remove(item_id)
                    if not cache.by_path[old_path]:
                        del cache.by_path[old_path]
                cache.by_id[item_id] = path
                cache.by_path.setdefault(path, []).append(item_id)
            return cache

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
                self._directory_ids = _DirectoryCache()
            else:
                self._directory_ids = self._directory_ids.without_subtrees(
                    subtrees
                )

    def _is_cached_directory(self, item_id):
        with self._cache_lock:
            cache = self._directory_ids
            return item_id == self.root or item_id in cache.by_id

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
    def _get_root_metadata(self, *, fresh=False):
        cached = self._root_metadata
        if cached and not fresh:
            return cached
        with self._root_lock:
            cached = self._root_metadata
            if cached and not fresh:
                return cached
            item = self.client.CreateFile({"id": self.root})
            try:
                with _translate_not_found(self.root):
                    item.FetchMetadata("driveId,mimeType,trashed")
            except FileNotFoundError:
                with self._cache_lock:
                    if self._root_metadata is cached:
                        self._root_metadata = {}
                raise
            with self._cache_lock:
                if self._root_metadata is cached:
                    # Trash status is only valid for the request that observed it.
                    self._root_metadata = (
                        {}
                        if item.get("trashed")
                        else {
                            "driveId": item.get("driveId"),
                            "mimeType": item.get("mimeType"),
                        }
                    )
            return item.metadata

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

    def _match_child(self, cache, parent_ids, path, name, mount=None):
        if path == self.base and mount is not None:
            return mount, cache
        match = _PathMatch([])
        for item in self._gdrive_list_ids(parent_ids, name=name):
            if item["name"] != name:  # Drive matches case-insensitively.
                continue
            if item["mimeType"] == FOLDER_MIME_TYPE:
                match.folders.append(item["id"])
            else:
                match.files.append(item)
        return match, self._cache_path_id(cache, path, *match.folders)

    def _resolve_prefix(self, path, cache, mount=None):
        """Return the deepest existing component, its matches and lookup cache."""
        parent = path
        remaining = []
        with self._cache_lock:
            while parent and parent not in cache.by_path:
                parent, name = posixpath.split(parent)
                remaining.append(name)
            folders = cache.by_path[parent].copy() if parent else [self.root]
        match = _PathMatch(folders)
        for name in reversed(remaining):
            child = posixpath.join(parent, name)
            found, cache = self._match_child(
                cache, match.folders, child, name, mount
            )
            if not found.folders and not found.files:
                break
            parent, match = child, found
            if not match.folders:
                break
        return parent, match, cache

    def _resolve_path(self, path, cache, mount=None) -> _PathMatch:
        prefix, match, _ = self._resolve_prefix(path, cache, mount)
        return match if prefix == path else _PathMatch([])

    def _create_missing_dirs(self, path, parent, folders, cache):
        # The prefix lookup already established that the first child is missing.
        names = path[len(parent) :].lstrip("/").split("/")
        for index, name in enumerate(names):
            parent = posixpath.join(parent, name)
            if index:
                match, cache = self._match_child(cache, folders, parent, name)
                if match.folders:
                    folders = match.folders
                    continue
                if match.files:
                    raise NotADirectoryError(posixpath.join(self.root, parent))
            folders = [self._create_dir(cache, min(folders), name, parent)]
        return folders

    def _path_to_item_ids(self, path, create=False):
        if not path:
            return [self.root]
        prefix, match, cache = self._resolve_prefix(
            path, *self._directory_cache()
        )
        if create:
            if not match.folders:
                raise NotADirectoryError(posixpath.join(self.root, prefix))
            if prefix != path:
                return self._create_missing_dirs(
                    path, prefix, match.folders, cache
                )
            return match.folders
        return match.ids if prefix == path else []

    def _get_item_id(self, path, create=False):
        _, base = self._split_mounted_path(path)
        ids = self._path_to_item_ids(base, create=create)
        if ids:
            return min(ids)
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

    def _create_dir(
        self, cache, parent_id, name, remote_path, *, exist_ok=True
    ):
        # Wait only for creates of the same name in the same Drive parent.
        key = (parent_id, name)
        with self._cache_lock:
            lock = self._creation_locks.get(key)
            if lock is None:
                lock = self._creation_locks[key] = threading.RLock()
        with lock:
            with self._cache_lock:
                if cached := cache.by_path.get(remote_path):
                    if not exist_ok:
                        raise FileExistsError(
                            posixpath.join(self.root, remote_path)
                        )
                    return cached[0]
            item = self._gdrive_create_dir(parent_id, name)
            self._cache_path_id(cache, remote_path, item["id"])
            return item["id"]

    def mkdir(self, path, create_parents=True, **kwargs):
        self._mkdir(path, create_parents, exist_ok=False)

    def makedirs(self, path, exist_ok=False):
        self._mkdir(path, create_parents=True, exist_ok=exist_ok)

    def _mkdir(self, path, create_parents, exist_ok):
        root, base = self._split_mounted_path(path)
        if not base:
            if root not in {"root", "appDataFolder"}:
                metadata = self._get_root_metadata(fresh=True)
                if metadata.get("trashed"):
                    raise FileNotFoundError(
                        errno.ENOENT, os.strerror(errno.ENOENT), path
                    )
                if metadata.get("mimeType") != FOLDER_MIME_TYPE:
                    raise FileExistsError(path)
            if not exist_ok:
                raise FileExistsError(path)
            return

        cache, mount = self._directory_cache()
        with self._cache_lock:
            if base in cache.by_path:
                if not exist_ok:
                    raise FileExistsError(path)
                return
        parent, name = posixpath.split(base)
        prefix, match, cache = self._resolve_prefix(parent, cache, mount)
        if not match.folders:
            raise NotADirectoryError(posixpath.join(root, prefix))
        folders = match.folders
        if prefix != parent:
            if not create_parents:
                raise FileNotFoundError(posixpath.join(root, parent))
            folders = self._create_missing_dirs(parent, prefix, folders, cache)
        leaf, cache = self._match_child(cache, folders, base, name, mount)
        if leaf.folders or leaf.files:
            if not leaf.folders or not exist_ok:
                raise FileExistsError(path)
            return
        self._create_dir(cache, min(folders), name, base, exist_ok=exist_ok)

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

    @_gdrive_retry
    def info(self, path):
        bucket, base = self.split_path(path)
        with _translate_not_found(path):
            item_id = self._get_item_id(path)
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

    def _walk_folders(self, base, start_ids, cache):
        # Cache entries are hints about which parents can share a request.
        # Build paths only from the parent links in the current Drive listing.
        frontier = dict.fromkeys(start_ids, base)
        with self._cache_lock:
            to_list = dict.fromkeys(frontier)
            to_list.update(
                {
                    item_id: None
                    for item_id, path in cache.by_id.items()
                    if _in_subtrees(path, (base,))
                }
            )
        children: dict[str, list[dict]] = {}
        visited: set[str] = set()
        while frontier:
            children.update({item_id: [] for item_id in to_list})
            for item in self._gdrive_list_ids(to_list):
                parent = next(
                    (p for p in item.get("parents", ()) if p in to_list), None
                )
                # Drive reports the canonical ID when a root alias was queried.
                if (
                    parent is None
                    and self.root in {"root", "appDataFolder"}
                    and self.root in to_list
                ):
                    parent = self.root
                if parent is not None:
                    children[parent].append(item)

            visited.update(frontier)
            following = {}
            for parent, parent_path in frontier.items():
                for item in children.pop(parent):
                    path = posixpath.join(parent_path, item["name"])
                    if item["mimeType"] == FOLDER_MIME_TYPE:
                        item_id = item["id"]
                        cache = self._cache_path_id(cache, path, item_id)
                        if item_id not in visited:
                            following[item_id] = path
                    yield path, item
            frontier = following
            to_list = {
                item_id: None
                for item_id in frontier
                if item_id not in children
            }

    def find(self, path, detail=False, **kwargs):
        root, base = self._split_mounted_path(path)
        cache, mount = self._directory_cache()
        dirs = self._resolve_path(base, cache, mount).folders
        contents = [
            _file_entry(posixpath.join(root, name), item)
            for name, item in self._walk_folders(base, dirs, cache)
            if item["mimeType"] != FOLDER_MIME_TYPE
        ]
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

    def rmdir(self, path):
        """Remove one empty Drive folder, honoring the trash_only setting."""
        root, base = self._split_mounted_path(path)
        if base:
            # Name duplicates and external renames must be checked before
            # deleting. A cached folder ID alone cannot establish uniqueness.
            match = self._resolve_path(base, _DirectoryCache())
            if not match.ids:
                raise FileNotFoundError(
                    errno.ENOENT, os.strerror(errno.ENOENT), path
                )
            if not match.folders:
                raise NotADirectoryError(
                    errno.ENOTDIR, os.strerror(errno.ENOTDIR), path
                )
            if len(match.ids) != 1:
                raise FileExistsError(
                    errno.EEXIST,
                    "Ambiguous Drive path; use the folder ID",
                    path,
                )
            item_id = match.folders[0]
        else:
            if root in {"root", "appDataFolder"}:
                raise PermissionError(
                    errno.EPERM, "Cannot remove a Drive root", path
                )
            item = self._get_root_metadata(fresh=True)
            if item.get("trashed"):
                raise FileNotFoundError(
                    errno.ENOENT, os.strerror(errno.ENOENT), path
                )
            if item.get("mimeType") != FOLDER_MIME_TYPE:
                raise NotADirectoryError(
                    errno.ENOTDIR, os.strerror(errno.ENOTDIR), path
                )
            if item.get("driveId") == root:
                raise PermissionError(
                    errno.EPERM, "Cannot remove a Drive root", path
                )
            item_id = root

        with _translate_not_found(path):
            self._gdrive_delete_file(item_id, empty_directory=path)
        self.invalidate_cache(path)

    def _check_empty_folder(self, item_id, path):
        query = f"{_quote_query_value(item_id)} in parents"
        if self._trash_only:
            query += " and trashed=false"
        pages = self.client.ListFile(
            dict(
                self._list_params,
                q=query,
                pageSize=1,
                fields="nextPageToken,incompleteSearch,files(id)",
            )
        )
        for page in pages:
            if page:
                raise OSError(
                    errno.ENOTEMPTY, os.strerror(errno.ENOTEMPTY), path
                )
            if pages.metadata.get("incompleteSearch"):
                raise OSError(errno.EIO, "Incomplete Drive listing", path)

    def rm_file(self, path):
        item_id = self._get_item_id(path)
        self._gdrive_delete_file(item_id)
        if self._is_cached_directory(item_id):
            self.invalidate_cache(path)

    @_gdrive_retry
    def _gdrive_delete_file(self, item_id, *, empty_directory=None):
        from pydrive2.files import ApiRequestError

        param = {"id": item_id}
        # it does not create a file on the remote
        item = self.client.CreateFile(param)

        try:
            if empty_directory is not None:
                self._check_empty_folder(item_id, empty_directory)
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
