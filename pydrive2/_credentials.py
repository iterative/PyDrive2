import errno
import json
import os
import stat
import sys
import tempfile
from contextlib import nullcontext
from hashlib import sha256
from pathlib import Path
from threading import Lock, RLock
from uuid import uuid4
from weakref import WeakValueDictionary

from filelock import FileLock
from google.oauth2.credentials import Credentials

_dictionary_locks: WeakValueDictionary[tuple[int, str], RLock] = (
    WeakValueDictionary()
)
_registry_lock = Lock()


class InvalidCredentialsError(IOError):
    """Error trying to read or write credentials."""


class CredentialStore:
    def __init__(self, destination: Path | tuple[dict[str, str], str]):
        self.destination = destination
        self.lock: FileLock | RLock
        if isinstance(destination, Path):
            self.lock = FileLock(str(destination) + ".lock", mode=0o600)
        else:
            dictionary, key = destination
            with _registry_lock:
                self.lock = _dictionary_locks.setdefault(
                    (id(dictionary), key), RLock()
                )

    @property
    def backend(self):
        return "file" if isinstance(self.destination, Path) else "dictionary"

    def load(self) -> "StoredUserCredentials | None":
        if isinstance(self.destination, Path):
            serialized = _read_file(self.destination, self.lock)
        else:
            dictionary, key = self.destination
            with self.lock:
                serialized = dictionary.get(key)
        if not serialized:
            return None

        try:
            info = json.loads(serialized)
            if (
                not isinstance(info, dict)
                or info.get("type", "authorized_user") != "authorized_user"
                or "_class" in info
                or "access_token" in info
                or not (info.get("token") or info.get("refresh_token"))
            ):
                raise ValueError("Expected google-auth user credentials")
            if not info.get("refresh_token"):
                # The SDK requires these keys even for access-token-only JSON.
                info.setdefault("refresh_token", None)
                info.setdefault("client_id", None)
                info.setdefault("client_secret", None)
            credentials = StoredUserCredentials.from_authorized_user_info(info)
            credentials._granted_scopes = info.get("_pydrive2_granted_scopes")
            if not info.get("expiry") and not credentials.refresh_token:
                credentials.expiry = None
            credentials._session_id = info.get(
                "_pydrive2_session"
            ) or _token_identity(credentials)
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidCredentialsError(
                "Select a fresh user credential file or dictionary key and "
                "sign in again with PyDrive2 2.x."
            ) from exc
        credentials.store = self
        return credentials

    def save(self, credentials: Credentials):
        if credentials.refresh_token:
            if not credentials.client_id or not credentials.client_secret:
                raise InvalidCredentialsError(
                    "Saving a refresh token requires a client ID and client secret"
                )
        elif not credentials.token:
            raise InvalidCredentialsError("No user token to save")
        if isinstance(self.destination, Path):
            self.destination.parent.mkdir(parents=True, exist_ok=True)
        with self.lock:
            info = json.loads(credentials.to_json())
            if credentials.granted_scopes is not None:
                info["_pydrive2_granted_scopes"] = credentials.granted_scopes
            session = getattr(credentials, "_session_id", None)
            if session and session != _token_identity(credentials):
                # Keep the grant identifiable when its refresh token rotates.
                info["_pydrive2_session"] = session
            serialized = json.dumps(info)
            if isinstance(self.destination, Path):
                _write_file(self.destination, serialized)
            else:
                dictionary, key = self.destination
                dictionary[key] = serialized


def _token_identity(credentials):
    return sha256(
        (credentials.refresh_token or credentials.token).encode()
    ).hexdigest()


class StoredUserCredentials(Credentials):
    store: CredentialStore | None = None
    _session_id: str | None = None
    _pending_refresh: "StoredUserCredentials | None" = None

    @classmethod
    def from_flow(cls, credentials, store):
        user = cls(
            token=credentials.token,
            refresh_token=credentials.refresh_token,
            id_token=credentials.id_token,
            token_uri=credentials.token_uri,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            scopes=credentials.scopes,
            expiry=credentials.expiry,
            granted_scopes=credentials.granted_scopes,
        )
        user.store = store
        user._session_id = _token_identity(user)
        return user

    def __getstate__(self):
        state = super().__getstate__()
        state.pop("store", None)
        state.pop("_pending_refresh", None)
        return state

    def use_client_config(self, config):
        # google-auth discards token_uri from cache JSON. Only a matching
        # application configuration may override its trusted Google endpoint.
        endpoint = config.get("token_uri")
        if endpoint and config.get("client_id") == self.client_id:
            self._token_uri = endpoint

    def refresh(self, request):
        store = self.store
        if store is None:
            super().refresh(request)
            return
        # AuthorizedHttp can refresh during a request, including after a 401.
        previous_token = self.token
        with store.lock:
            if self.token != previous_token and self.valid:
                return
            cached = store.load()
            if (
                cached is None
                or cached._session_id != self._session_id
                or cached.client_id != self.client_id
            ):
                # A different login or an explicit deletion owns this location.
                self.store = None
                super().refresh(request)
                return
            # If publication failed after rotation, the old refresh token may
            # already be invalid. Retry the write before another exchange.
            pending = self._pending_refresh
            if pending is not None and cached.token == self.token:
                cached = pending
            else:
                pending = None
            if not cached.valid or (
                pending is None and cached.token == previous_token
            ):
                # Only the original client's trusted endpoint may receive tokens.
                cached._token_uri = self.token_uri
                Credentials.refresh(cached, request)
                pending = self._pending_refresh = cached
            if pending is not None:
                store.save(pending)
            self._pending_refresh = None
            self.token = cached.token
            self.expiry = cached.expiry
            self._refresh_token = cached.refresh_token
            self._rapt_token = cached.rapt_token
            if cached.id_token is not None:
                self._id_token = cached.id_token
            if cached.granted_scopes is not None:
                self._granted_scopes = cached.granted_scopes


def _check_path(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    # Windows uses the name-surrogate bit for symlinks and junctions.
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_reparse_tag", 0) & 0x20000000
    ):
        raise InvalidCredentialsError(
            "Credentials file cannot be symbolic link"
        )
    return info


def _read_file(path, lock):
    if _check_path(path) is None:
        return None
    try:
        read_lock = lock.acquire()
    except OSError as exc:
        # A pre-provisioned cache can be mounted read-only.
        if exc.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
            raise
        read_lock = nullcontext()
    try:
        with (
            read_lock,
            open(path, encoding="utf-8", opener=_open_no_follow) as reader,
        ):
            if sys.platform == "win32":
                # Open normally to hydrate cloud or compressed files. Windows
                # lacks O_NOFOLLOW, so verify the opened file before reading.
                info = _check_path(path)
                if info is None or not os.path.samestat(
                    info, os.fstat(reader.fileno())
                ):
                    raise InvalidCredentialsError(
                        "Credentials file changed while opening"
                    )
            return reader.read()
    except FileNotFoundError:
        return None


def _open_no_follow(path, flags):
    return os.open(path, flags | getattr(os, "O_NOFOLLOW", 0))


def _write_file(path, serialized):
    _check_path(path)
    if sys.platform == "win32":
        from ._windows_credentials import create_for_replace

        name = path.with_name(uuid4().hex + ".tmp")
        fd = create_for_replace(path, name)
    else:
        fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(serialized)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
