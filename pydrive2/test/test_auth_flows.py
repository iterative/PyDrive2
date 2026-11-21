import errno
import json
import os
import pickle
import stat
import subprocess
import sys
import webbrowser
from base64 import urlsafe_b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
from http.client import HTTPConnection
from threading import Barrier, Event
from urllib.parse import parse_qs, urlencode, urlparse

import httplib2
import pytest
import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from filelock import Timeout
from google.auth import jwt
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp, Request

from pydrive2._credentials import CredentialStore
from pydrive2.auth import (
    AuthenticationError,
    GoogleAuth,
    InvalidCredentialsError,
)
from pydrive2.fs.spec import GDriveAuthError, _client_auth
from pydrive2.settings import InvalidConfigError

TOKEN_URI = "https://oauth2.googleapis.com/token"
EMAIL = "service@example.test"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def service_info(key):
    return {
        "type": "service_account",
        "client_email": EMAIL,
        "private_key_id": "test-key",
        "token_uri": TOKEN_URI,
        "private_key": key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    }


@pytest.fixture
def token_endpoint(monkeypatch):
    calls = []

    def request(http, uri, method="GET", body=None, **kwargs):
        assert (uri, method) == (TOKEN_URI, "POST")
        calls.append(
            parse_qs(body.decode() if isinstance(body, bytes) else body)
        )
        return (
            httplib2.Response({"status": "200"}),
            json.dumps(
                {
                    "access_token": "fresh",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                }
            ).encode(),
        )

    monkeypatch.setattr(httplib2.Http, "request", request)
    return calls


@pytest.mark.parametrize("backend", ["file", "dictionary"])
def test_automatic_http_refresh_is_saved(backend, monkeypatch, tmp_path):
    settings = {
        "client_config_backend": "file",
        "save_credentials": True,
        "save_credentials_backend": backend,
    }
    if backend == "file":
        settings["save_credentials_file"] = str(tmp_path / "user.json")
    else:
        settings.update(save_credentials_dict={}, save_credentials_key="user")
    auth = GoogleAuth(settings=settings)
    auth.credentials = Credentials(
        "old",
        refresh_token="refresh",
        client_id="id",
        client_secret="secret",
        token_uri=TOKEN_URI,
        expiry=datetime(2099, 1, 1),
    )
    auth.SaveCredentials()
    auth.LoadCredentials()
    export = tmp_path / "export.json"
    auth.SaveCredentialsFile(export)
    http = auth.Get_Http_Object()
    access_tokens = []

    def request(uri, method="GET", headers=None, **kwargs):
        if uri == TOKEN_URI:
            return (
                httplib2.Response({"status": "200"}),
                json.dumps(
                    {
                        "access_token": "new",
                        "refresh_token": "rotated",
                        "expires_in": 3600,
                    }
                ).encode(),
            )
        access_tokens.append(headers["authorization"])
        status = "200" if headers["authorization"] == "Bearer new" else "401"
        return httplib2.Response({"status": status}), b"{}"

    monkeypatch.setattr(http.http, "request", request)
    assert http.request("https://example.test/drive")[0].status == 200
    assert access_tokens == ["Bearer old", "Bearer new"]
    reopened = GoogleAuth(settings=settings)
    reopened.LoadCredentials()
    assert reopened.credentials.refresh_token == "rotated"
    assert json.loads(export.read_text())["refresh_token"] == "refresh"


@pytest.mark.parametrize("save_credentials", [False, True])
def test_login_respects_save_credentials(
    save_credentials, tmp_path, mocker, token_endpoint
):
    path = tmp_path / "user.json"
    auth = GoogleAuth(
        settings={
            "client_config_backend": "file",
            "save_credentials": save_credentials,
            "save_credentials_backend": "file",
            "save_credentials_file": str(path),
        }
    )
    auth.LoadCredentialsFile(path)
    auth.flow = mocker.Mock()
    auth.flow.run_local_server.return_value = Credentials(
        "initial",
        refresh_token="refresh",
        client_id="id",
        client_secret="secret",
        token_uri=TOKEN_URI,
    )
    auth.LocalWebserverAuth(launch_browser=False)
    assert path.exists() == save_credentials
    # The transport can refresh the login before a subsequent LoadCredentials.
    http = auth.Get_Http_Object()
    http.credentials.refresh(Request(http.http))
    assert len(token_endpoint) == 1
    if save_credentials:
        assert json.loads(path.read_text())["token"] == "fresh"
    else:
        assert not path.exists()

    auth.SaveCredentials()
    assert json.loads(path.read_text())["token"] == "fresh"


def test_refresh_only_credentials_round_trip(tmp_path, token_endpoint):
    auth = GoogleAuth(settings_file=None)
    auth.credentials = Credentials(
        None,
        refresh_token="refresh",
        client_id="id",
        client_secret="secret",
        token_uri=TOKEN_URI,
    )
    path = tmp_path / "user.json"
    auth.SaveCredentialsFile(path)

    other = GoogleAuth(settings_file=None)
    other.LoadCredentialsFile(path)
    other.LocalWebserverAuth()
    assert other.credentials.valid
    assert json.loads(path.read_text())["token"] == "fresh"
    assert len(token_endpoint) == 1


def test_refresh_retries_failed_cache_write(tmp_path, mocker, token_endpoint):
    path = tmp_path / "user.json"
    auth = GoogleAuth(settings_file=None)
    auth.credentials = Credentials(
        None,
        refresh_token="refresh",
        client_id="id",
        client_secret="secret",
        token_uri=TOKEN_URI,
    )
    auth.SaveCredentialsFile(path)
    auth.LoadCredentialsFile(path)
    replace = mocker.patch("os.replace", side_effect=OSError("replace failed"))
    with pytest.raises(OSError, match="replace failed"):
        auth.Refresh()
    mocker.stop(replace)
    auth.Refresh()
    assert len(token_endpoint) == 1
    assert json.loads(path.read_text())["token"] == "fresh"


def test_concurrent_login_refreshes_and_saves_once(mocker, token_endpoint):
    saved = {}
    auth = GoogleAuth(
        settings={
            "client_config_backend": "file",
            "save_credentials": True,
            "save_credentials_backend": "dictionary",
            "save_credentials_dict": saved,
            "save_credentials_key": "user",
        }
    )
    auth.credentials = Credentials(
        None,
        refresh_token="refresh",
        client_id="id",
        client_secret="secret",
        token_uri=TOKEN_URI,
    )
    refresh_if_expired = auth._refresh_if_expired
    workers = Barrier(2)

    def refresh_together():
        workers.wait(timeout=10)
        refresh_if_expired()

    mocker.patch.object(
        auth, "_refresh_if_expired", side_effect=refresh_together
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(auth.LocalWebserverAuth) for _ in range(2)]
        for call in calls:
            call.result()

    assert len(token_endpoint) == 1
    assert json.loads(saved["user"])["token"] == "fresh"


@pytest.mark.parametrize("local_server", [False, True])
def test_scope_change_raises_authentication_error(mocker, local_server):
    auth = GoogleAuth(settings_file=None)
    auth.flow = mocker.Mock()
    error = Warning("Scope has changed")
    if local_server:
        auth.flow.run_local_server.side_effect = error
    else:
        auth.flow.fetch_token.side_effect = error
    with pytest.raises(AuthenticationError) as raised:
        if local_server:
            auth.LocalWebserverAuth(launch_browser=False)
        else:
            auth.Authenticate("code")
    assert raised.value.__cause__ is error


@pytest.mark.parametrize(
    "source",
    [
        "client_json",
        "client_json_dict",
        "client_json_file_path",
        "client_pkcs12_file_path",
    ],
)
def test_service_account_signing(
    source, service_info, key, tmp_path, token_endpoint
):
    config = {"client_user_email": "delegate@example.test"}
    if source == "client_json_dict":
        config[source] = service_info
    elif source == "client_json":
        config[source] = json.dumps(service_info)
    else:
        keyfile = tmp_path / "key"
        if source == "client_pkcs12_file_path":
            keyfile.write_bytes(
                pkcs12.serialize_key_and_certificates(
                    b"key",
                    key,
                    None,
                    None,
                    serialization.BestAvailableEncryption(b"notasecret"),
                )
            )
            config["client_service_email"] = EMAIL
        else:
            keyfile.write_text(json.dumps(service_info))
        config[source] = str(keyfile)
    settings = {
        "client_config_backend": "service",
        "service_config": config,
        "oauth_scope": ["scope-a", "scope-b"],
        "save_credentials": True,
        "save_credentials_backend": "dictionary",
        "save_credentials_dict": {},
        "save_credentials_key": "account",
    }
    auth = GoogleAuth(settings=settings)
    auth.ServiceAuth()
    assertion = token_endpoint[0]["assertion"][0]
    claims = jwt.decode(assertion, verify=False)
    assert (claims["iss"], claims["sub"], claims["scope"]) == (
        EMAIL,
        "delegate@example.test",
        "scope-a scope-b",
    )
    assert auth.credentials.valid
    assert isinstance(auth.http, AuthorizedHttp)
    assert auth.http.http is not auth.Get_Http_Object().http
    assert 308 not in auth.http.http.redirect_codes
    assert not settings["save_credentials_dict"]
    with pytest.raises(InvalidCredentialsError):
        auth.SaveCredentials()


def test_login_options_and_port_fallback(mocker):
    auth = GoogleAuth(
        settings={
            "client_config_backend": "settings",
            "get_refresh_token": True,
            "client_config": {"client_id": "id", "client_secret": "secret"},
        }
    )
    query = parse_qs(urlparse(auth.GetAuthUrl()).query)
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"]
    mocker.patch("webbrowser.get", side_effect=webbrowser.Error("No browser"))
    login = mocker.patch.object(
        auth.flow,
        "run_local_server",
        side_effect=[
            OSError(errno.EADDRINUSE, "busy"),
            Credentials("token"),
        ],
    )
    auth.LocalWebserverAuth(
        port_numbers=[8080, 8090],
        host_name="localhost",
        bind_addr="127.0.0.1",
    )
    assert [call.kwargs["port"] for call in login.call_args_list] == [
        8080,
        8090,
    ]
    assert login.call_args.kwargs["open_browser"] is False
    assert login.call_args.kwargs["bind_addr"] == "127.0.0.1"
    assert auth.service is not None


def test_custom_auth_and_online_only_cache(tmp_path, mocker):
    path = tmp_path / "online"
    auth = GoogleAuth(
        settings={
            "client_config_backend": "settings",
            "save_credentials": True,
            "save_credentials_backend": "file",
            "save_credentials_file": str(path),
            "client_config": {
                "client_id": "id",
                "client_secret": "secret",
                "redirect_uri": "https://example.test/callback",
            },
        }
    )
    query = parse_qs(urlparse(auth.GetAuthUrl()).query)
    assert query["access_type"] == ["online"]
    assert query["redirect_uri"] == ["https://example.test/callback"]
    mocker.patch.object(auth.flow, "fetch_token")
    auth.flow.oauth2session.token = {
        "access_token": "online",
        "expires_at": 4102444800,
    }
    auth.Auth("code")
    auth.flow.fetch_token.assert_called_once_with(code="code")
    other = GoogleAuth(settings_file=None)
    other.LoadCredentialsFile(path)
    assert other.credentials.valid
    assert other.credentials.refresh_token is None


def test_loopback_code_exchange(tmp_path, monkeypatch, mocker):
    auth = GoogleAuth(
        settings={
            "client_config_backend": "settings",
            "client_config": {
                "client_id": "client",
                "client_secret": "secret",
            },
            "get_refresh_token": True,
            "save_credentials": True,
            "save_credentials_backend": "file",
            "save_credentials_file": str(tmp_path / "user.json"),
        }
    )
    challenge = None
    callback = None

    def open_browser(url, **kwargs):
        nonlocal challenge, callback
        query = parse_qs(urlparse(url).query)
        assert query["code_challenge_method"] == ["S256"]
        challenge = query["code_challenge"][0]
        redirect = urlparse(query["redirect_uri"][0])
        callback = HTTPConnection(redirect.hostname, redirect.port, timeout=5)
        params = urlencode({"state": query["state"][0], "code": "approved"})
        callback.request("GET", f"{redirect.path}?{params}")
        return True

    def token_response(session, request, **kwargs):
        form = parse_qs(request.body)
        assert form["code"] == ["approved"]
        digest = sha256(form["code_verifier"][0].encode()).digest()
        assert urlsafe_b64encode(digest).rstrip(b"=").decode() == challenge
        response = requests.Response()
        response.request = request
        response.status_code = 200
        response._content = json.dumps(
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_in": 3600,
                "token_type": "Bearer",
            }
        ).encode()
        return response

    monkeypatch.setattr(
        webbrowser, "get", lambda *args: mocker.Mock(open=open_browser)
    )
    monkeypatch.setattr(requests.Session, "send", token_response)
    try:
        auth.LocalWebserverAuth(host_name="127.0.0.1", port_numbers=[0])
        assert callback.getresponse().status == 200
    finally:
        if callback is not None:
            callback.close()
    auth.LoadCredentials()
    assert auth.credentials.valid
    assert auth.credentials.refresh_token == "refresh"


def test_failed_file_save_preserves_previous_credentials(tmp_path, mocker):
    path = tmp_path / "user.json"
    auth = GoogleAuth(settings_file=None)
    auth.credentials = Credentials(
        "original", client_id="id", client_secret="secret"
    )
    auth.SaveCredentialsFile(path)
    original_files = set(tmp_path.iterdir())
    auth.credentials = Credentials("updated")
    mocker.patch("os.replace", side_effect=OSError("replace failed"))
    with pytest.raises(OSError, match="replace failed"):
        auth.SaveCredentialsFile(path)
    auth.LoadCredentialsFile(path)
    assert auth.credentials.token == "original"
    assert set(tmp_path.iterdir()) == original_files


def test_credentials_file_rejects_symlinks(tmp_path):
    auth = GoogleAuth(settings_file=None)
    auth.credentials = Credentials("test")
    link = tmp_path / "link"
    target = tmp_path / "target"
    target.write_text("original")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks unavailable")
    with pytest.raises(InvalidCredentialsError):
        auth.SaveCredentialsFile(link)
    with pytest.raises(InvalidCredentialsError):
        auth.LoadCredentialsFile(link)
    assert target.read_text() == "original"


def test_save_waits_for_open_credential_reader(tmp_path, monkeypatch):
    path = tmp_path / "user.json"
    writer = CredentialStore(path)
    writer.lock.timeout = 0
    writer.save(Credentials("original"))
    reader = GoogleAuth(settings_file=None)
    opened, finish = Event(), Event()
    fdopen = os.fdopen

    def hold_reader(fd, mode="r", **kwargs):
        stream = fdopen(fd, mode, **kwargs)
        if mode == "r":
            opened.set()
            assert finish.wait(10)
        return stream

    monkeypatch.setattr(os, "fdopen", hold_reader)
    with ThreadPoolExecutor(max_workers=1) as pool:
        loading = pool.submit(reader.LoadCredentialsFile, path)
        try:
            assert opened.wait(10)
            with pytest.raises(Timeout):
                writer.save(Credentials("updated"))
        finally:
            finish.set()
        loading.result(timeout=10)
    assert reader.credentials.token == "original"
    writer.save(Credentials("updated"))
    assert writer.load().token == "updated"


def test_read_only_and_missing_cache(tmp_path, mocker):
    path = tmp_path / "user.json"
    store = CredentialStore(path)
    acquire = mocker.patch.object(
        store.lock, "acquire", side_effect=OSError(errno.EROFS, "read only")
    )
    assert store.load() is None
    acquire.assert_not_called()
    path.write_text('{"token": "provisioned"}')
    assert store.load().token == "provisioned"
    assert list(tmp_path.iterdir()) == [path]


def test_reauth_uses_explicit_profile(tmp_path, mocker):
    default = tmp_path / "default.json"
    profile = tmp_path / "profile.json"
    auth = GoogleAuth(
        settings={
            "client_config_backend": "file",
            "save_credentials": True,
            "save_credentials_backend": "file",
            "save_credentials_file": str(default),
        }
    )
    auth.credentials = Credentials("default-user")
    auth.SaveCredentials()
    auth.credentials = Credentials("expired", expiry=datetime(2001, 1, 1))
    auth.SaveCredentialsFile(profile)
    auth.LoadCredentialsFile(profile)
    auth.flow = mocker.Mock()
    auth.flow.run_local_server.return_value = Credentials("new-profile-user")
    auth.LocalWebserverAuth()
    assert json.loads(profile.read_text())["token"] == "new-profile-user"
    assert json.loads(default.read_text())["token"] == "default-user"


@pytest.mark.parametrize("backend", ["file", "dictionary"])
def test_shared_cache_refresh_rotation(backend, tmp_path, monkeypatch):
    settings = {
        "client_config_backend": "file",
        "save_credentials": True,
        "save_credentials_backend": backend,
    }
    if backend == "file":
        settings["save_credentials_file"] = str(tmp_path / "user.json")
    else:
        settings.update(save_credentials_dict={}, save_credentials_key="user")
    first, second = (GoogleAuth(settings=settings) for _ in range(2))
    first.credentials = Credentials(
        None,
        refresh_token="original",
        client_id="id",
        client_secret="secret",
        token_uri=TOKEN_URI,
    )
    first.SaveCredentials()
    first.LoadCredentials()
    second.LoadCredentials()
    arrivals = Barrier(2)
    token_requests = []

    def request(http, uri, method, body=None, **kwargs):
        assert uri == TOKEN_URI
        assert parse_qs(body.decode())["refresh_token"] == ["original"]
        token_requests.append(uri)
        return httplib2.Response({"status": "200"}), json.dumps(
            {
                "access_token": "fresh",
                "refresh_token": "rotated",
                "expires_in": 3600,
            }
        ).encode()

    def refresh(auth):
        arrivals.wait(timeout=10)
        auth.Refresh()

    monkeypatch.setattr(httplib2.Http, "request", request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(refresh, [first, second]))
    assert len(token_requests) == 1
    for auth in (first, second):
        assert (auth.credentials.token, auth.credentials.refresh_token) == (
            "fresh",
            "rotated",
        )
        assert auth.credentials.valid
    detached = pickle.loads(pickle.dumps(second.credentials))
    assert detached.refresh_token == "rotated"
    assert detached.store is None


def test_refresh_does_not_overwrite_a_different_login(
    tmp_path, token_endpoint
):
    path = tmp_path / "user.json"
    auth = GoogleAuth(settings_file=None)
    auth.credentials = Credentials(
        None,
        refresh_token="first",
        client_id="id",
        client_secret="secret",
        token_uri=TOKEN_URI,
    )
    auth.SaveCredentialsFile(path)
    auth.LoadCredentialsFile(path)
    replacement = GoogleAuth(settings_file=None)
    replacement.credentials = Credentials("second-user")
    replacement.SaveCredentialsFile(path)

    auth.Refresh()
    assert len(token_endpoint) == 1
    assert auth.credentials.token == "fresh"
    replacement.LoadCredentialsFile(path)
    assert replacement.credentials.token == "second-user"


@pytest.mark.parametrize("backend", ["settings", "file"])
def test_token_endpoint_comes_from_client_configuration(
    backend, tmp_path, monkeypatch
):
    path = tmp_path / "user.json"
    endpoint = "https://proxy.example.test/oauth/token"
    config = {
        "client_id": "id",
        "client_secret": "secret",
        "token_uri": endpoint,
    }
    settings = {"client_config_backend": backend}
    if backend == "settings":
        settings["client_config"] = config
    else:
        client_file = tmp_path / "client_secrets.json"
        client_file.write_text(
            json.dumps(
                {
                    "web": {
                        **config,
                        "auth_uri": "https://accounts.example.test/auth",
                        "redirect_uris": ["http://localhost"],
                    }
                }
            )
        )
        settings["client_config_file"] = str(client_file)
    auth = GoogleAuth(settings=settings)
    auth.credentials = Credentials(
        None,
        refresh_token="refresh",
        token_uri="https://cache-only.example.test/token",
        client_id="id",
        client_secret="secret",
    )
    auth.SaveCredentialsFile(path)
    other = GoogleAuth(settings=settings)
    other.LoadCredentialsFile(path)
    calls = []

    def request(http, uri, **kwargs):
        calls.append(uri)
        return httplib2.Response(
            {"status": "200"}
        ), b'{"access_token":"fresh","expires_in":3600}'

    monkeypatch.setattr(httplib2.Http, "request", request)
    other.Refresh()
    assert calls == [endpoint]
    assert other.client_config == {}


@pytest.mark.parametrize("client_file", ["malformed", "service", "other-user"])
def test_cached_login_ignores_unrelated_client_config(
    tmp_path, monkeypatch, client_file
):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "user.json"
    cached = Credentials(
        None,
        refresh_token="refresh",
        client_id="id",
        client_secret="secret",
        token_uri="https://cache-only.example.test/token",
    )
    path.write_text(cached.to_json())
    config = tmp_path / "client_secrets.json"
    if client_file == "malformed":
        config.write_text("{")
    elif client_file == "service":
        config.write_text('{"type":"service_account"}')
    else:
        config.write_text(
            json.dumps(
                {
                    "installed": {
                        "client_id": "other",
                        "client_secret": "other-secret",
                        "token_uri": "https://other.example.test/token",
                        "auth_uri": "https://other.example.test/auth",
                        "redirect_uris": ["http://localhost"],
                    }
                }
            )
        )
    auth = GoogleAuth(settings_file=None)
    auth.LoadCredentialsFile(path)
    assert auth.credentials.refresh_token == "refresh"
    assert auth.credentials.token_uri == TOKEN_URI
    assert auth.client_config == {}
    if client_file != "other-user":
        with pytest.raises(InvalidConfigError):
            auth.LoadClientConfigFile()


@pytest.mark.parametrize("expiry", [None, datetime(2099, 1, 1)])
def test_token_only_cache(tmp_path, expiry):
    auth = GoogleAuth(settings_file=None)
    auth.credentials = Credentials("token", expiry=expiry)
    path = tmp_path / "user.json"
    auth.SaveCredentialsFile(path)
    auth.LoadCredentialsFile(path)
    assert auth.credentials.valid
    assert auth.credentials.expiry == expiry


def test_noninteractive_refresh_handler(monkeypatch):
    monkeypatch.setenv("GDRIVE_NON_INTERACTIVE", "true")
    auth = GoogleAuth(settings_file=None)
    auth.credentials = Credentials(
        None,
        refresh_handler=lambda request, scopes=None: (
            "brokered",
            datetime(2099, 1, 1),
        ),
    )
    auth.LocalWebserverAuth()
    assert auth.credentials.token == "brokered"
    assert auth.credentials.valid


def test_fsspec_upgrade_uses_new_cache(tmp_path, mocker):
    mocker.patch("appdirs.user_cache_dir", return_value=str(tmp_path))
    legacy = json.dumps({"_class": "OAuth2Credentials", "access_token": "old"})
    old_path = tmp_path / "client" / "default.json"
    old_path.parent.mkdir()
    old_path.write_text(legacy)
    login = mocker.patch(
        "google_auth_oauthlib.flow.InstalledAppFlow.run_local_server",
        return_value=Credentials("new-token"),
    )
    for _ in range(2):
        auth = _client_auth("client", "secret")
        assert auth.credentials.token == "new-token"
    login.assert_called_once()
    assert (tmp_path / "v2" / "client" / "default.json").is_file()
    for source in (
        {"client_json": legacy},
        {"client_json_file_path": str(old_path)},
    ):
        with pytest.raises(GDriveAuthError, match="sign in again") as exc:
            _client_auth("client", "secret", **source)
        assert isinstance(exc.value.__cause__, InvalidCredentialsError)
    assert old_path.read_text() == legacy


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DACL")
def test_windows_cache_preserves_file_permissions(tmp_path, monkeypatch):
    import win32security as security

    path = tmp_path / "user.json"
    auth = GoogleAuth(settings_file=None)
    auth.credentials = Credentials("first")
    auth.SaveCredentialsFile(path)
    flags = security.DACL_SECURITY_INFORMATION

    def dacl(filename):
        sd = security.GetFileSecurity(str(filename), flags)
        return security.ConvertSecurityDescriptorToStringSecurityDescriptor(
            sd, 1, flags
        )

    assert "(A;;FA;;;OW)" in dacl(path)
    # Add a distinct explicit grant so an always-owner-only replacement also fails.
    sd = security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:P(A;;FA;;;OW)(A;;FR;;;SY)", 1
    )
    security.SetFileSecurity(str(path), flags, sd)
    original = dacl(path)
    replace = os.replace

    def check_staged_acl(src, dst):
        if dst == path:
            assert dacl(src) == original
        return replace(src, dst)

    monkeypatch.setattr(os, "replace", check_staged_acl)
    auth.credentials = Credentials("updated")
    auth.SaveCredentialsFile(path)
    assert dacl(path) == original
    assert json.loads(path.read_text())["token"] == "updated"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file compression")
def test_windows_compressed_cache(tmp_path):
    import win32file

    path = tmp_path / "user.json"
    path.write_bytes(b'{"token":"compressed"}' + b" " * 65536)
    subprocess.run(
        ["compact.exe", "/C", "/EXE:XPRESS4K", "/F", str(path)], check=True
    )
    if (
        not win32file.GetFileAttributes(str(path))
        & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        pytest.skip("The volume does not support WOF-compressed files")
    auth = GoogleAuth(settings_file=None)
    auth.LoadCredentialsFile(path)
    assert auth.credentials.token == "compressed"
