import json
import os
import tempfile
from pathlib import Path

from google.oauth2.credentials import Credentials


class InvalidCredentialsError(IOError):
    """Error trying to read or write credentials."""


class CredentialStore:
    def __init__(self, destination: Path | tuple[dict[str, str], str], lock):
        self.destination = destination
        self.lock = lock

    @property
    def backend(self):
        return "file" if isinstance(self.destination, Path) else "dictionary"

    def load(self):
        with self.lock:
            if isinstance(self.destination, Path):
                serialized = _read_file(self.destination)
            else:
                dictionary, key = self.destination
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
                # google-auth omits this key when saving online-only logins,
                # but requires it to be present when loading them.
                info.setdefault("refresh_token", None)
                credentials = StoredUserCredentials.from_authorized_user_info(
                    info
                )
            except (AttributeError, TypeError, ValueError) as exc:
                raise InvalidCredentialsError(
                    "Select a fresh user credential file or dictionary key and "
                    "sign in again with PyDrive2 2.x."
                ) from exc
            credentials.store = self
            return credentials

    def save(self, credentials: Credentials):
        with self.lock:
            serialized = credentials.to_json()
            if isinstance(self.destination, Path):
                _write_file(self.destination, serialized)
            else:
                dictionary, key = self.destination
                dictionary[key] = serialized


class StoredUserCredentials(Credentials):
    store: CredentialStore | None = None

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
        return user

    def refresh(self, request):
        store = self.store
        if store is None:
            return super().refresh(request)
        # AuthorizedHttp can refresh during a request, including after a 401.
        previous_token = self.token
        with store.lock:
            if self.token == previous_token or not self.valid:
                super().refresh(request)
                store.save(self)


def _check_path(path):
    if path.is_symlink():
        raise InvalidCredentialsError(
            "Credentials file cannot be symbolic link"
        )


def _read_file(path):
    _check_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(fd, encoding="utf-8") as stream:
        return stream.read()


def _write_file(path, serialized):
    _check_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(serialized)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
