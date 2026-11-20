import errno
import json
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import parse_qs, urlparse

import httplib2
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from google.auth import jwt
from google.oauth2.credentials import Credentials
from google_auth_httplib2 import AuthorizedHttp

from pydrive2.auth import (
    AuthenticationError,
    GoogleAuth,
    InvalidCredentialsError,
)

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


def test_automatic_http_refresh_is_saved(monkeypatch):
    saved = {
        "user": json.dumps(
            {
                "token": "old",
                "expiry": "2099-01-01T00:00:00Z",
                "refresh_token": "refresh",
                "client_id": "id",
                "client_secret": "secret",
                "token_uri": TOKEN_URI,
            }
        )
    }
    auth = GoogleAuth(
        settings={
            "client_config_backend": "file",
            "save_credentials": True,
            "save_credentials_backend": "dictionary",
            "save_credentials_dict": saved,
            "save_credentials_key": "user",
        }
    )
    auth.LoadCredentials()
    http = auth.Get_Http_Object()

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
        status = "200" if headers["authorization"] == "Bearer new" else "401"
        return httplib2.Response({"status": status}), b"{}"

    monkeypatch.setattr(http.http, "request", request)
    assert http.request("https://example.test/drive")[0].status == 200
    assert json.loads(saved["user"])["refresh_token"] == "rotated"


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
    auth = GoogleAuth(
        settings={
            "client_config_backend": "settings",
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
    path = tmp_path / "online"
    auth.SaveCredentialsFile(path)
    other = GoogleAuth(settings_file=None)
    other.LoadCredentialsFile(path)
    assert other.credentials.valid
    assert other.credentials.refresh_token is None


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
