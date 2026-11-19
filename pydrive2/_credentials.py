"""Credential persistence for GoogleAuth's file and dictionary backends."""

import base64
import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from google.oauth2 import credentials, service_account


class InvalidCredentialsError(IOError):
    """Error trying to read or write credentials."""


class _PersistentCredentials:
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


class UserCredentials(_PersistentCredentials, credentials.Credentials):
    pass


class ServiceCredentials(_PersistentCredentials, service_account.Credentials):
    @classmethod
    def from_key_info(cls, info, scopes, subject):
        info = dict(info)
        info.setdefault("token_uri", "https://oauth2.googleapis.com/token")
        cred = cls.from_service_account_info(
            info, scopes=scopes, subject=subject
        )
        # An explicitly saved service credential must be able to sign again
        # even if the original key file is no longer present.
        cred._saved_key_info = info
        cred._saved_subject = subject
        return cred

    def to_json(self):
        return json.dumps(
            {
                "type": "service_account",
                "key": self._saved_key_info,
                "scopes": self.scopes,
                "subject": self._saved_subject,
                "token": self.token,
                "expiry": (
                    self.expiry.isoformat() + "Z" if self.expiry else None
                ),
            }
        )


def p12_key_info(data, email, password=b"notasecret"):
    key, _, _ = pkcs12.load_key_and_certificates(data, password)
    if key is None:
        raise InvalidCredentialsError("No private key in PKCS12 file")
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return {
        "client_email": email,
        "private_key": pem.decode("ascii"),
        "token_uri": "https://oauth2.googleapis.com/token",
    }


def load_credentials(serialized, storage=None):
    if not serialized:
        return None
    try:
        data = json.loads(serialized)
        scopes = data.get("scopes")
        if data.get("_class") == "ServiceAccountCredentials":
            # Migrate credentials previously written by oauth2client.
            email = data["_service_account_email"]
            if data.get("_private_key_pkcs12"):
                info = p12_key_info(
                    base64.b64decode(data["_private_key_pkcs12"]),
                    email,
                    (
                        data.get("_private_key_password") or "notasecret"
                    ).encode(),
                )
            else:
                info = {
                    "client_email": email,
                    "private_key": data["_private_key_pkcs8_pem"],
                    "private_key_id": data.get("_private_key_id"),
                }
            info["token_uri"] = data["token_uri"]
            cred = ServiceCredentials.from_key_info(
                info, data["_scopes"].split(), data["_kwargs"].get("sub")
            )
        elif data.get("type") == "service_account":
            cred = ServiceCredentials.from_key_info(
                data["key"], scopes, data.get("subject")
            )
        else:
            cred = UserCredentials(
                token=None,
                refresh_token=data.get("refresh_token"),
                token_uri=data.get(
                    "token_uri", "https://oauth2.googleapis.com/token"
                ),
                client_id=data["client_id"],
                client_secret=data["client_secret"],
                scopes=scopes,
                rapt_token=data.get("rapt_token"),
                quota_project_id=data.get("quota_project_id"),
            )
        if not data.get("invalid"):
            cred.token = data.get("token", data.get("access_token"))
        expiry = data.get("expiry", data.get("token_expiry"))
        if expiry:
            cred.expiry = datetime.fromisoformat(expiry.rstrip("Z"))
        cred.storage = storage
        return cred
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise InvalidCredentialsError("Invalid saved credentials") from exc


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
