import errno
import io
import logging
import os
import posixpath
import re
import tempfile
import threading
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field
from weakref import WeakValueDictionary

import appdirs
from fsspec.spec import AbstractFileSystem
from funcy import chunks, retry
from google.oauth2 import service_account
from tqdm.utils import CallbackIOWrapper

from pydrive2.auth import GoogleAuth, InvalidCredentialsError
from pydrive2.drive import GoogleDrive
from pydrive2.fs.cache import FOLDER_MIME_TYPE, DirectoryCache
from pydrive2.fs.utils import IterStream

logger = logging.getLogger(__name__)

_INFO_FIELDS = "mimeType,size,md5Checksum"
_LIST_FIELDS = (
    f"nextPageToken,incompleteSearch,files(id,name,parents,{_INFO_FIELDS})"
)
_MAX_QUERY_TERMS = 50
_DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
_LIST_ALL_SCOPES = {
    _DRIVE_SCOPE + suffix
    for suffix in ("", ".readonly", ".metadata", ".metadata.readonly")
}
_APP_DATA_SCOPES = {_DRIVE_SCOPE + ".appdata", _DRIVE_SCOPE + ".appfolder"}

COMMON_SETTINGS = {
    "get_refresh_token": True,
    "oauth_scope": [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.appdata",
    ],
}


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


def _select_item_id(path, ids):
    if ids:
        return min(ids)
    raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), path)


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
        :param client_json: Saved user credentials or a service-account key as JSON.
        :type client_json: str.
        :param client_json_file_path: User token cache or service-account key path.
        :type client_json_file_path: str.
        :param use_service_account: Use service account.
        :type use_service_account: bool.
        :param profile: Profile name for caching credentials
            (ignored for service account).
        :type profile: str.
        :param trash_only: Move files to trash instead of deleting.
        :type trash_only: bool.
        :param acknowledge_abuse: Acknowledging the risk and download file
            identified as abusive.
        :type acknowledge_abuse: bool
        :raises: GDriveAuthError
        """
        super().__init__(**kwargs)
        self.path = path
        self.root, self.base = self.split_path(path)
        self._dirs = DirectoryCache(self.root)
        self._cache_lock = self._dirs.lock
        self._mount_lock = threading.RLock()
        self._root_lock = threading.RLock()
        self._root_metadata: dict = {}
        self._creation_locks: WeakValueDictionary = WeakValueDictionary()

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
        path = self._strip_protocol(path)
        if "//" in path:
            path = re.sub(r"/{2,}", "/", path)
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

    def _resolve_mount(self):
        generation = self._dirs.generation
        if not self.base or self._dirs.contains(self.base, generation):
            return generation, None
        with self._mount_lock:
            generation = self._dirs.generation
            mount, generation = self._resolve_path(self.base, generation)
            if not mount.ids:
                raise FileNotFoundError(
                    errno.ENOENT,
                    os.strerror(errno.ENOENT),
                    f"Confirm {self.path} exists and you can access it",
                )
            return generation, mount

    def invalidate_cache(self, path=None):
        base = self._split_mounted_path(path)[1] if path else ""
        super().invalidate_cache(path)
        with self._cache_lock:
            if not base:
                self._root_metadata = {}
            self._dirs.invalidate(base)

    def _invalidate_folder(self, item_id, *paths, known_folder=False):
        with self._cache_lock:
            if item_id == self.root:
                self.invalidate_cache(self.root)
            elif known_folder or item_id in self._dirs.parents:
                for path in paths:
                    super().invalidate_cache(path)
                self._dirs.removed(
                    item_id, [self._split_mounted_path(p)[1] for p in paths]
                )

    @property
    def _list_params(self):
        params = {"corpora": "user"}
        if self.root == "appDataFolder":
            params["spaces"] = "appDataFolder"
        elif self.root != "root":
            try:
                metadata = self._get_root_metadata()
            except FileNotFoundError as exc:
                raise PermissionError from exc
            if "appDataFolder" in metadata.get("spaces", ()):
                params["spaces"] = "appDataFolder"
            elif metadata.get("driveId"):
                params.update(driveId=metadata["driveId"], corpora="drive")
        return params

    @_gdrive_retry
    def _get_root_metadata(
        self, *, fresh=False, fields="driveId,mimeType,trashed"
    ):
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
                    item.FetchMetadata(f"{fields},spaces")
            except FileNotFoundError:
                with self._cache_lock:
                    if self._root_metadata is cached:
                        self.invalidate_cache(self.root)
                raise
            with self._cache_lock:
                if self._root_metadata is cached:
                    if item.get("trashed"):
                        self.invalidate_cache(self.root)
                    else:
                        self._root_metadata = {
                            "spaces": item.get("spaces", ()),
                            "driveId": item.get("driveId"),
                            "mimeType": item.get("mimeType"),
                        }
            return item.metadata

    def _gdrive_list(self, query):
        param = {
            "q": query,
            "pageSize": 1000,
            "fields": _LIST_FIELDS,
        }
        param.update(self._list_params)
        file_list = self.client.ListFile(param)

        get_list = _gdrive_retry(lambda: next(file_list, None))
        for page in iter(get_list, None):
            if file_list.metadata.get("incompleteSearch"):
                raise OSError(errno.EIO, "Incomplete Drive listing")
            yield from (item.metadata for item in page)

    def _query_children(self, parents, names=()):
        parent_query = " or ".join(
            f"{_quote_query_value(p)} in parents" for p in parents
        )
        query = f"({parent_query}) and trashed=false"
        if names:
            terms = " or ".join(f"name={_quote_query_value(n)}" for n in names)
            query += f" and {terms}" if len(names) == 1 else f" and ({terms})"
        return self._gdrive_list(query)

    def _list_children(self, listing, *, require_parent=False):
        names = (listing.name,) if listing.name is not None else ()
        # Drive can return the same ID on multiple pages or in different
        # batches. Wait for the last attributable row before constructing paths.
        rows: dict[str, tuple[str | None, dict]] = {}
        for batch in chunks(_MAX_QUERY_TERMS, listing.folders):
            batch_ids = set(batch)
            unplaced = {}
            for item in self._query_children(batch, names):
                if names and item["name"] != listing.name:
                    continue  # Drive matches case-insensitively.
                file_id = item["id"]
                parent = next(
                    (p for p in item.get("parents", ()) if p in batch_ids),
                    None,
                )
                if parent is None:
                    if len(batch) == 1:
                        parent = batch[0]
                    elif (
                        self.root in {"root", "appDataFolder"}
                        and self.root in batch_ids
                        and item.get("parents")
                    ):
                        # Drive can return the canonical ID of the root alias.
                        parent = self.root
                if parent is None and require_parent:
                    unplaced[file_id] = item["name"]
                    continue
                unplaced.pop(file_id, None)
                previous = rows.get(file_id)
                if previous is not None:
                    previous_parent, _ = previous
                    if previous_parent in item.get("parents", ()):
                        # Shared memberships still refer to one output item.
                        parent = previous_parent
                rows[file_id] = parent, item

            # Some responses omit parents. Resolve only the missing IDs, using
            # their names to avoid paging through unrelated siblings.
            for parent_id in batch:
                if not unplaced:
                    break
                probes = dict.fromkeys(unplaced.values())
                # Bound the OR expression for unusually large sets of names.
                if len(probes) > _MAX_QUERY_TERMS:
                    probes.clear()
                for item in self._query_children([parent_id], probes):
                    file_id = item["id"]
                    if file_id in unplaced:
                        del unplaced[file_id]
                        rows[file_id] = parent_id, item
            if unplaced:
                # An item can change during the probes. This batch cannot
                # establish which cached folders have disappeared.
                listing.complete = False

        for parent, item in rows.values():
            listing.add(parent, item)
            yield parent, item

    def _match_child(
        self,
        generation,
        parent_ids,
        path,
        name,
        mount=None,
        *,
        folders_only=False,
        create_in=None,
        exist_ok=True,
    ):
        match = mount if path == self.base else None
        if match is None:
            folders = self._dirs.lookup(
                parent_ids, name, generation, folders_only=folders_only
            )
            if folders:
                match = _PathMatch(folders)
        created = False
        if match is None:
            match = _PathMatch([])
            with self._dirs.listing(
                generation, parent_ids, name=name
            ) as listing:
                for _, item in self._list_children(listing):
                    if item["mimeType"] == FOLDER_MIME_TYPE:
                        match.folders.append(item["id"])
                    else:
                        match.files.append(item)
                if (
                    create_in is not None
                    and not match.folders
                    and not match.files
                ):
                    item = self._gdrive_create_dir(create_in, name)
                    self._dirs.created(listing, create_in, item["id"])
                    match.folders.append(item["id"])
                    created = True
            generation = listing.generation
        if not exist_ok and not created:
            raise FileExistsError(posixpath.join(self.root, path))
        return match, generation

    def _resolve_prefix(
        self, path, generation, mount=None, *, folders_only=False
    ):
        """Return the deepest existing component, its matches and lookup generation."""
        parent, folders, remaining = self._dirs.prefix(
            path, generation, folders_only=folders_only
        )
        match = _PathMatch(folders)
        for name in remaining:
            child = posixpath.join(parent, name)
            found, generation = self._match_child(
                generation,
                match.folders,
                child,
                name,
                mount,
                folders_only=folders_only or child != path,
            )
            if not found.folders and not found.files:
                break
            parent, match = child, found
            if not match.folders:
                break
        return parent, match, generation

    def _resolve_path(
        self, path, generation, mount=None, *, folders_only=False
    ):
        prefix, match, generation = self._resolve_prefix(
            path, generation, mount, folders_only=folders_only
        )
        return (match if prefix == path else _PathMatch([])), generation

    def _ensure_dirs(self, path, generation, mount=None):
        parent, folders, remaining = self._dirs.prefix(
            path, generation, folders_only=True
        )
        for name in remaining:
            parent = posixpath.join(parent, name)
            match, generation = self._get_or_create_child(
                generation, folders, parent, name, mount
            )
            if not match.folders:
                raise NotADirectoryError(posixpath.join(self.root, parent))
            folders = match.folders
        return folders, generation

    def _lookup_path(self, path, *, folders_only=False):
        if not path:
            with self._cache_lock:
                return _PathMatch([self.root]), self._dirs.generation
        generation, mount = self._resolve_mount()
        return self._resolve_path(
            path, generation, mount, folders_only=folders_only
        )

    def _path_to_item_ids(self, path, create=False):
        if create and path:
            generation, mount = self._resolve_mount()
            folders, _ = self._ensure_dirs(path, generation, mount)
            return folders
        match, _ = self._lookup_path(path)
        return match.ids

    def _get_item_id(self, path, create=False):
        _, base = self._split_mounted_path(path)
        ids = self._path_to_item_ids(base, create=create)
        return _select_item_id(path, ids)

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

    def _get_or_create_child(
        self, generation, parents, path, name, mount=None, *, exist_ok=True
    ):
        parent_id = min(parents)
        key = (parent_id, name)
        with self._cache_lock:
            lock = self._creation_locks.get(key)
            if lock is None:
                lock = self._creation_locks[key] = threading.RLock()
        with lock:
            # Check Drive under the same name lock as the create on a cache miss.
            return self._match_child(
                generation,
                parents,
                path,
                name,
                mount,
                folders_only=True,
                create_in=parent_id,
                exist_ok=exist_ok,
            )

    def mkdir(self, path, create_parents=True, **kwargs):
        self._mkdir(path, create_parents, exist_ok=False)

    def makedirs(self, path, exist_ok=False):
        self._mkdir(path, create_parents=True, exist_ok=exist_ok)

    def _mkdir(self, path, create_parents, exist_ok):
        root, base = self._split_mounted_path(path)
        cached = base and self._dirs.has_directory(base, self.base)
        if not cached and root not in {"root", "appDataFolder"}:
            metadata = self._get_root_metadata(fresh=True)
            if metadata.get("trashed"):
                raise FileNotFoundError(
                    errno.ENOENT, os.strerror(errno.ENOENT), root
                )
            if metadata.get("mimeType") != FOLDER_MIME_TYPE:
                if base:
                    raise NotADirectoryError(
                        errno.ENOTDIR, os.strerror(errno.ENOTDIR), root
                    )
                raise FileExistsError(path)
        if not base or cached:
            if not exist_ok:
                raise FileExistsError(path)
            return

        generation, mount = self._resolve_mount()
        parent, name = posixpath.split(base)
        if create_parents:
            folders, generation = self._ensure_dirs(parent, generation, mount)
        else:
            prefix, match, generation = self._resolve_prefix(
                parent, generation, mount, folders_only=True
            )
            if not match.folders:
                raise NotADirectoryError(posixpath.join(root, prefix))
            if prefix != parent:
                raise FileNotFoundError(posixpath.join(root, parent))
            folders = match.folders
        leaf, _ = self._get_or_create_child(
            generation, folders, base, name, mount, exist_ok=exist_ok
        )
        if not leaf.folders:
            raise FileExistsError(path)

    def exists(self, path):
        root, base = self._split_mounted_path(path)
        if base and self._dirs.has_directory(base, self.base):
            return True
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
        bucket, base = self._split_mounted_path(path)
        if not base and bucket not in {"root", "appDataFolder"}:
            gdrive_file = self._get_root_metadata(
                fresh=True, fields=f"driveId,trashed,{_INFO_FIELDS}"
            )
        else:
            with _translate_not_found(path):
                item_id = self._get_item_id(path)
                gdrive_file = self.client.CreateFile({"id": item_id})
                fields = _INFO_FIELDS if base else f"{_INFO_FIELDS},trashed"
                _gdrive_retry(gdrive_file.FetchMetadata)(fields=fields)
        if not base and gdrive_file.get("trashed"):
            raise FileNotFoundError(
                errno.ENOENT, os.strerror(errno.ENOENT), path
            )

        return _file_entry(
            posixpath.join(bucket, base).rstrip("/"), gdrive_file
        )

    def ls(self, path, detail=False):
        bucket, base = self._split_mounted_path(path)

        match, generation = self._lookup_path(base)
        dir_ids = match.ids
        if not dir_ids:
            raise FileNotFoundError(
                errno.ENOENT, os.strerror(errno.ENOENT), path
            )

        root_path = posixpath.join(bucket, base)
        contents = []
        with self._dirs.listing(generation, dir_ids) as listing:
            for _, item in self._list_children(listing):
                contents.append(
                    _file_entry(posixpath.join(root_path, item["name"]), item)
                )
        if detail:
            return contents
        return [entry["name"] for entry in contents]

    def _walk_folders(self, base, start_ids, generation, maxdepth):
        frontier = dict.fromkeys(start_ids, base)
        to_list = self._dirs.warm_parents(frontier, maxdepth, generation)
        depth = 0
        children: dict[str, list[dict]] = {}
        visited: set[str] = set()
        while frontier:
            depth += 1
            if to_list:
                with self._dirs.listing(generation, to_list) as listing:
                    children.update((parent, []) for parent in to_list)
                    for parent, item in self._list_children(
                        listing, require_parent=True
                    ):
                        children[parent].append(item)
                generation = listing.generation

            visited.update(frontier)
            following = {}
            for parent, parent_path in frontier.items():
                for item in children.pop(parent):
                    path = posixpath.join(parent_path, item["name"])
                    if item["mimeType"] == FOLDER_MIME_TYPE:
                        item_id = item["id"]
                        if item_id not in visited:
                            following[item_id] = path
                    yield path, item
            if depth == maxdepth:
                return
            frontier = following
            to_list = {
                item_id: None
                for item_id in frontier
                if item_id not in children
            }

    def find(
        self, path, maxdepth=None, withdirs=False, detail=False, **kwargs
    ):
        # PyDrive2 historically accepted a boolean detail as its second argument.
        if isinstance(maxdepth, bool):
            warnings.warn(
                "Positional detail is deprecated. Pass detail=True or detail=False.",
                DeprecationWarning,
                stacklevel=2,
            )
            detail = detail or maxdepth
            maxdepth = None
        root, base = self._split_mounted_path(path)
        if maxdepth is not None and maxdepth < 1:
            raise ValueError("maxdepth must be at least 1")
        generation, mount = self._resolve_mount()
        match, generation = self._resolve_path(base, generation, mount)
        file = (
            min(match.files, key=lambda item: item["id"], default=None)
            if not match.folders
            else None
        )
        start = posixpath.join(root, base).rstrip("/")
        check_empty_root = False
        if not base and root not in {"root", "appDataFolder"}:
            if self._root_metadata.get("mimeType") == FOLDER_MIME_TYPE:
                check_empty_root = withdirs
            else:
                try:
                    metadata = self._get_root_metadata(
                        fresh=True, fields=f"driveId,trashed,{_INFO_FIELDS}"
                    )
                except FileNotFoundError:
                    return {} if detail else []
                if metadata.get("trashed"):
                    return {} if detail else []
                if metadata["mimeType"] != FOLDER_MIME_TYPE:
                    file = metadata
        if file is not None:
            return {start: _file_entry(start, file)} if detail else [start]
        if not match.folders:
            return {} if detail else []

        contents = []
        for relative, item in self._walk_folders(
            base, match.folders, generation, maxdepth
        ):
            check_empty_root = False
            name = posixpath.join(root, relative)
            if item["mimeType"] == FOLDER_MIME_TYPE:
                if withdirs:
                    contents.append(_directory_entry(name))
            else:
                contents.append(_file_entry(name, item))

        if check_empty_root:
            try:
                self.info(path)
            except FileNotFoundError:
                return {} if detail else []

        if withdirs:
            contents.insert(0, _directory_entry(start))
        contents.sort(key=lambda entry: entry["name"])
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
        item = self._gdrive_upload_fobj(name, parent_id, stream)
        self._dirs.file_added(parent_id, name)
        return item

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
        self._dirs.file_added(parent_id, name)

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

        _, source = self._split_mounted_path(path1)
        match, generation = self._lookup_path(source)
        file1_id = _select_item_id(path1, match.ids)
        file1 = self.client.CreateFile({"id": file1_id})
        if src_name != dst_name:
            file1["name"] = dst_name

        destination_parent = None
        if src_parent != dst_parent:
            _, parent_path = self._split_mounted_path(dst_parent)
            parent_match, _ = self._lookup_path(parent_path, folders_only=True)
            if parent_match.files and not parent_match.folders:
                raise NotADirectoryError(dst_parent)
            destination_parent = _select_item_id(
                dst_parent, parent_match.folders
            )
            file1["parents"] = [destination_parent]

        file1.Upload()
        if (
            file1_id in match.folders
            or file1.get("mimeType") == FOLDER_MIME_TYPE
        ):
            destination = posixpath.join(dst_parent, dst_name)
            if self._dirs.moved(
                generation, file1_id, destination_parent, dst_name
            ):
                super().invalidate_cache(path1)
                super().invalidate_cache(destination)
            else:
                self._invalidate_folder(
                    file1_id, path1, destination, known_folder=True
                )
        else:
            _, parent_path = self._split_mounted_path(dst_parent)
            self._dirs.file_added(
                destination_parent, dst_name, parent_path=parent_path
            )

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

    def _fresh_folder_match(self, base):
        # A cached folder ID cannot establish whether the name is still unique.
        prefix, match, _ = self._resolve_prefix(base, None)
        if not match.folders:
            blocked = posixpath.join(self.root, prefix)
            self.invalidate_cache(blocked)
            raise NotADirectoryError(
                errno.ENOTDIR, os.strerror(errno.ENOTDIR), blocked
            )
        if prefix != base:
            missing = base.removeprefix(prefix).lstrip("/").partition("/")[0]
            path = posixpath.join(self.root, prefix, missing)
            self.invalidate_cache(path)
            raise FileNotFoundError(
                errno.ENOENT, os.strerror(errno.ENOENT), path
            )
        return match

    def rmdir(self, path):
        """Remove one empty Drive folder, honoring the trash_only setting."""
        root, base = self._split_mounted_path(path)
        if base:
            # Walking the target also checks the mount when it is an ancestor.
            if not _in_subtrees(base, (self.base,)):
                self._fresh_folder_match(self.base)
            match = self._fresh_folder_match(base)
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

        try:
            with _translate_not_found(path):
                self._gdrive_delete_file(item_id, empty_directory=path)
        except FileNotFoundError:
            self._invalidate_folder(item_id, path, known_folder=True)
            raise
        self._invalidate_folder(item_id, path, known_folder=True)

    def _check_empty_folder(self, item_id, path):
        query = f"{_quote_query_value(item_id)} in parents"
        if self._trash_only:
            query += " and trashed=false"
        params = self._list_params
        pages = self.client.ListFile(
            dict(
                params,
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

        # drive.file can return an empty, complete page while hiding children.
        credentials = self.client.auth.credentials
        if isinstance(credentials, service_account.Credentials):
            scopes = credentials.scopes
        else:
            scopes = getattr(credentials, "granted_scopes", None)
        required = (
            _APP_DATA_SCOPES
            if params.get("spaces") == "appDataFolder"
            else _LIST_ALL_SCOPES
        )
        if required.isdisjoint(scopes or ()):
            raise PermissionError(
                errno.EACCES,
                "Cannot verify folder emptiness with these OAuth scopes",
                path,
            )

    def rm_file(self, path):
        _, base = self._split_mounted_path(path)
        match, _ = self._lookup_path(base)
        item_id = _select_item_id(path, match.ids)
        known_folder = item_id in match.folders
        self._gdrive_delete_file(item_id)
        self._invalidate_folder(item_id, path, known_folder=known_folder)

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
