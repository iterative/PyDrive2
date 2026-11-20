"""Credential persistence for GoogleAuth's file and dictionary backends."""

import json
import os
import tempfile
import threading
from pathlib import Path

from google.oauth2.credentials import Credentials


class InvalidCredentialsError(IOError):
    """Error trying to read or write credentials."""


class UserCredentials(Credentials):
    storage = None

    def refresh(self, request):
        storage = self.storage
        if storage is None:
            return super().refresh(request)

        # AuthorizedHttp can refresh during a request, including after a 401.
        # Persist rotated tokens on that path as well as on explicit Refresh().
        previous_token = self.token
        with storage.lock:
            if self.token == previous_token or not self.valid:
                super().refresh(request)
                storage.put(self)


def load_credentials(serialized, storage):
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
        # Online-only logins have an access token but no refresh token.
        info.setdefault("refresh_token", None)
        cred = UserCredentials.from_authorized_user_info(info)
    except (AttributeError, TypeError, ValueError) as exc:
        raise InvalidCredentialsError(
            "Select a fresh user credential file or dictionary key and sign "
            "in again with PyDrive2 2.x."
        ) from exc
    cred.storage = storage
    return cred


class FileStorage:
    def __init__(self, filename):
        self.path = Path(filename)
        self.lock = threading.RLock()

    def _check_path(self):
        if self.path.is_symlink():
            raise InvalidCredentialsError(
                "Credentials file cannot be symbolic link"
            )

    def get(self):
        with self.lock:
            self._check_path()
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path, flags)
            except FileNotFoundError:
                return None
            with os.fdopen(fd, encoding="utf-8") as stream:
                return load_credentials(stream.read(), self)

    def put(self, credentials):
        with self.lock:
            self._check_path()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(credentials.to_json())
                os.replace(name, self.path)
            finally:
                Path(name).unlink(missing_ok=True)


class DictionaryStorage:
    def __init__(self, dictionary, key):
        self.dictionary = dictionary
        self.key = key
        self.lock = threading.RLock()

    def get(self):
        with self.lock:
            return load_credentials(self.dictionary.get(self.key), self)

    def put(self, credentials):
        with self.lock:
            self.dictionary[self.key] = credentials.to_json()
