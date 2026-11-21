import errno
import json
import os
import threading
import webbrowser
from functools import wraps
from pathlib import Path
from typing import ClassVar

import httplib2
from cryptography.hazmat.primitives.serialization import pkcs12
from google.auth.crypt import RSASigner
from google.auth.exceptions import RefreshError as GoogleRefreshError
from google.oauth2 import credentials as user_credentials
from google.oauth2 import service_account
from google_auth_httplib2 import AuthorizedHttp, Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from oauthlib.oauth2 import AccessDeniedError, OAuth2Error

from ._credentials import (
    CredentialStore,
    InvalidCredentialsError,
    StoredUserCredentials,
)
from .apiattr import ApiAttribute, ApiAttributeMixin
from .settings import (
    InvalidConfigError,
    LoadSettingsFile,
    SettingsError,
    ValidateSettings,
)


class AuthError(Exception):
    """Base error for authentication/authorization errors."""


class AuthenticationRejected(AuthError):
    """User rejected authentication."""


class AuthenticationError(AuthError):
    """General authentication error."""


class RefreshError(AuthError):
    """Access token refresh error."""


def LoadAuth(decoratee):
    """Decorator to check if the auth is valid and loads auth if not."""

    @wraps(decoratee)
    def _decorated(self, *args, **kwargs):
        # Initialize auth if needed.
        if self.auth is None:
            self.auth = GoogleAuth()
        # Re-create access token if it expired.
        if self.auth.access_token_expired:
            if self.auth.credentials is not None and not isinstance(
                self.auth.credentials, user_credentials.Credentials
            ):
                self.auth._refresh_if_expired()
            elif self.auth.auth_method == "service":
                self.auth.ServiceAuth()
            else:
                self.auth.LocalWebserverAuth()

        # Initialise service if not built yet.
        if self.auth.service is None:
            self.auth.Authorize()

        # Ensure that a thread-safe HTTP object is provided.
        if (
            kwargs is not None
            and "param" in kwargs
            and kwargs["param"] is not None
            and "http" in kwargs["param"]
            and kwargs["param"]["http"] is not None
        ):
            self.http = kwargs["param"]["http"]
            del kwargs["param"]["http"]

        else:
            # If HTTP object not specified, create or resuse an HTTP
            # object from the thread local storage.
            http = getattr(self.auth.thread_local, "http", None)
            if http is None or (
                isinstance(http, AuthorizedHttp)
                and http.credentials is not self.auth.credentials
            ):
                self.auth.thread_local.http = self.auth.Get_Http_Object()
            self.http = self.auth.thread_local.http

        return decoratee(self, *args, **kwargs)

    return _decorated


class GoogleAuth(ApiAttributeMixin):
    """Google Drive authentication using google-auth.

    Loads all settings and credentials from one 'settings.yaml' file
    and performs common OAuth2.0 related functionality such as authentication
    and authorization.
    """

    DEFAULT_SETTINGS: ClassVar[dict[str, str | bool | list[str]]] = {
        "client_config_backend": "file",
        "client_config_file": "client_secrets.json",
        "save_credentials": False,
        "oauth_scope": ["https://www.googleapis.com/auth/drive"],
    }
    CLIENT_CONFIGS_LIST: ClassVar[list[str]] = [
        "client_id",
        "client_secret",
        "auth_uri",
        "token_uri",
        "revoke_uri",
        "redirect_uri",
    ]
    settings = ApiAttribute("settings")
    client_config = ApiAttribute("client_config")
    flow = ApiAttribute("flow")
    credentials = ApiAttribute("credentials")
    http = ApiAttribute("http")
    service = ApiAttribute("service")
    auth_method = ApiAttribute("auth_method")

    def __init__(
        self, settings_file="settings.yaml", http_timeout=None, settings=None
    ):
        """Create an instance of GoogleAuth.

        :param settings_file: path of settings file. 'settings.yaml' by default.
        :type settings_file: str.
        :param settings: settings dict.
        :type settings: dict.
        """
        self.http_timeout = http_timeout
        ApiAttributeMixin.__init__(self)
        self.thread_local = threading.local()
        self._refresh_lock = threading.RLock()
        self.client_config = {}

        if settings is None and settings_file:
            try:
                settings = LoadSettingsFile(settings_file)
            except SettingsError:
                pass

        self.settings = settings or self.DEFAULT_SETTINGS
        ValidateSettings(self.settings)

        self._configured_store = self._storage_from_settings()
        self._login_store = self._configured_store

    @property
    def access_token_expired(self):
        """Checks if access token doesn't exist or is expired.

        :returns: bool -- True if access token doesn't exist or is expired.
        """
        return self.credentials is None or not self.credentials.valid

    def _use_flow_credentials(self, credentials):
        if self.settings.get("save_credentials"):
            credentials = StoredUserCredentials.from_flow(
                credentials, self._login_store
            )
            self._login_store.save(credentials)
        self.credentials = credentials

    def LocalWebserverAuth(
        self,
        host_name="localhost",
        port_numbers=None,
        launch_browser=True,
        bind_addr=None,
    ):
        """Authenticate and authorize from user by creating local web server and
        retrieving authentication code.

        This function is not for web server application. It creates local web
        server for user from standalone application.

        If GDRIVE_NON_INTERACTIVE environment variable is set, this function
        raises AuthenticationError.

        :param host_name: host name of the local web server.
        :type host_name: str.
        :param port_numbers: list of port numbers to be tried to used.
        :type port_numbers: list.
        :param launch_browser: should browser be launched automatically
        :type launch_browser: bool
        :param bind_addr: optional IP address for the local web server to listen on.
            If not specified, it will listen on the address specified in the
            host_name parameter.
        :type bind_addr: str.
        :raises: AuthenticationRejected, AuthenticationError
        """
        if self.credentials is None and self.settings.get("save_credentials"):
            self._load_credentials(self._login_store)
        if not self.access_token_expired:
            return
        if self.credentials is not None and (
            not isinstance(self.credentials, user_credentials.Credentials)
            or self.credentials.refresh_token
            or self.credentials.refresh_handler
        ):
            self._refresh_if_expired()
            return
        if os.getenv("GDRIVE_NON_INTERACTIVE"):
            raise AuthenticationError(
                "Non interactive mode (GDRIVE_NON_INTERACTIVE env) is enabled"
            )
        if self.flow is None:
            self.GetFlow()
        if port_numbers is None:
            port_numbers = (8080, 8090)
        if launch_browser:
            try:
                webbrowser.get()
            except webbrowser.Error:
                launch_browser = False
        for port in port_numbers:
            try:
                credentials = self.flow.run_local_server(
                    host=host_name,
                    bind_addr=bind_addr,
                    port=port,
                    open_browser=launch_browser,
                    **self._authorization_params(),
                )
            except OSError as exc:
                if exc.errno not in (errno.EADDRINUSE, errno.EACCES):
                    raise
                continue
            except AccessDeniedError as exc:
                raise AuthenticationRejected(
                    "User rejected authentication"
                ) from exc
            except (OAuth2Error, Warning) as exc:
                raise AuthenticationError(
                    "OAuth2 authorization failed"
                ) from exc
            self._use_flow_credentials(credentials)
            self.Authorize()
            return
        raise AuthenticationError(
            "Could not bind to any configured OAuth callback port"
        )

    def CommandLineAuth(self):
        """Print a login URL and receive the authorization on a local port."""
        return self.LocalWebserverAuth(launch_browser=False)

    def ServiceAuth(self):
        """Authenticate with a service account, optionally acting for a user."""
        self.auth_method = "service"
        if not isinstance(self.credentials, service_account.Credentials):
            self.LoadServiceConfigSettings()
            config = self.client_config
            options = {
                "scopes": self.settings["oauth_scope"],
                "subject": config.get("client_user_email"),
            }
            if "client_pkcs12_file_path" in config:
                with open(config["client_pkcs12_file_path"], "rb") as stream:
                    key, _, _ = pkcs12.load_key_and_certificates(
                        stream.read(), b"notasecret"
                    )
                if key is None:
                    raise InvalidCredentialsError(
                        "No private key in PKCS12 file"
                    )
                self.credentials = service_account.Credentials(
                    RSASigner(key),
                    config["client_service_email"],
                    "https://oauth2.googleapis.com/token",
                    **options,
                )
            else:
                if "client_json_dict" in config:
                    info = config["client_json_dict"]
                elif "client_json" in config:
                    info = json.loads(config["client_json"])
                else:
                    with open(
                        config["client_json_file_path"], encoding="utf-8"
                    ) as stream:
                        info = json.load(stream)
                info = {
                    "token_uri": "https://oauth2.googleapis.com/token",
                    **info,
                }
                self.credentials = (
                    service_account.Credentials.from_service_account_info(
                        info, **options
                    )
                )
            self.service = None
        self._refresh_if_expired()
        if self.service is None:
            self.Authorize()

    def _storage_from_settings(self):
        backend = self.settings.get("save_credentials_backend")
        if backend == "file":
            filename = self.settings.get("save_credentials_file")
            if filename is None:
                raise InvalidConfigError(
                    "Please specify credentials file to read"
                )
            return CredentialStore(Path(filename))
        if backend == "dictionary":
            dictionary = self.settings.get("save_credentials_dict")
            key = self.settings.get("save_credentials_key")
            if dictionary is None:
                raise InvalidConfigError("Please specify credentials dict")
            if key is None:
                raise InvalidConfigError("Please specify credentials key")
            return CredentialStore((dictionary, key))
        if self.settings.get("save_credentials"):
            raise InvalidConfigError(
                f"Unknown save_credentials_backend: {backend}"
            )
        return None

    def _credential_backend(self, backend):
        if backend is None:
            backend = self.settings.get("save_credentials_backend")
        if backend is None:
            raise InvalidConfigError("Please specify credential backend")
        if backend not in ("file", "dictionary"):
            raise InvalidConfigError("Unknown save_credentials_backend")
        return backend

    def _storage_for(self, backend, filename=None):
        if filename is not None:
            return CredentialStore(Path(filename))
        store = self._configured_store
        if store is None or store.backend != backend:
            raise InvalidConfigError(
                f"Credential backend {backend!r} is not configured"
            )
        return store

    def LoadCredentials(self, backend=None):
        """Loads credentials or create empty credentials if it doesn't exist.

        :param backend: target backend to save credential to.
        :type backend: str.
        :raises: InvalidConfigError
        """
        if self._credential_backend(backend) == "file":
            self.LoadCredentialsFile()
        else:
            self._load_credentials(self._storage_for("dictionary"))

    def LoadCredentialsFile(self, credentials_file=None):
        """Loads credentials or create empty credentials if it doesn't exist.

        Loads credentials file from path in settings if not specified.

        :param credentials_file: path of credentials file to read.
        :type credentials_file: str.
        :raises: InvalidConfigError, InvalidCredentialsError
        """
        self._load_credentials(self._storage_for("file", credentials_file))

    def _load_credentials(self, store):
        with self._refresh_lock:
            credentials = store.load()
            if credentials and credentials.refresh_token:
                config = self.client_config or self.settings.get(
                    "client_config", {}
                )
                if (
                    not config
                    and self.settings["client_config_backend"] == "file"
                    and Path(self.settings["client_config_file"]).is_file()
                ):
                    self.LoadClientConfigFile()
                    config = self.client_config
                credentials.use_client_config(config)
            self.credentials = credentials
            self._login_store = store

    def SaveCredentials(self, backend=None):
        """Saves credentials according to specified backend.

        If you have any specific credentials backend in mind, don't use this
        function and use the corresponding function you want.

        :param backend: backend to save credentials.
        :type backend: str.
        :raises: InvalidConfigError
        """
        if self._credential_backend(backend) == "file":
            self.SaveCredentialsFile()
        else:
            self._save_credentials("dictionary")

    def SaveCredentialsFile(self, credentials_file=None):
        """Saves credentials to the file in JSON format.

        :param credentials_file: destination to save file to.
        :type credentials_file: str.
        :raises: InvalidConfigError, InvalidCredentialsError
        """
        self._save_credentials("file", credentials_file)

    def _save_credentials(self, backend, filename=None):
        if not isinstance(self.credentials, user_credentials.Credentials):
            raise InvalidCredentialsError(
                "Only signed-in user credentials can be saved"
            )
        self._storage_for(backend, filename).save(self.credentials)

    def LoadClientConfig(self, backend=None):
        """Loads client configuration according to specified backend.

        If you have any specific backend to load client configuration from in mind,
        don't use this function and use the corresponding function you want.

        :param backend: backend to load client configuration from.
        :type backend: str.
        :raises: InvalidConfigError
        """
        if backend is None:
            backend = self.settings.get("client_config_backend")
            if backend is None:
                raise InvalidConfigError(
                    "Please specify client config backend"
                )
        if backend == "file":
            self.LoadClientConfigFile()
        elif backend == "settings":
            self.LoadClientConfigSettings()
        elif backend == "service":
            self.LoadServiceConfigSettings()
        else:
            raise InvalidConfigError("Unknown client_config_backend")

    def LoadClientConfigFile(self, client_config_file=None):
        """Loads client configuration file downloaded from APIs console.

        Loads client config file from path in settings if not specified.

        :param client_config_file: path of client config file to read.
        :type client_config_file: str.
        :raises: InvalidConfigError
        """
        if client_config_file is None:
            client_config_file = self.settings["client_config_file"]
        try:
            with open(client_config_file, encoding="utf-8") as stream:
                config = json.load(stream)
            (client_type,) = config
            if client_type not in ("web", "installed"):
                raise ValueError("Expected web or installed client")
            info = config[client_type]
            self.client_config = {
                name: info[name]
                for name in (
                    "client_id",
                    "client_secret",
                    "auth_uri",
                    "token_uri",
                )
            }
            self.client_config.update(
                redirect_uri=info["redirect_uris"][0],
                revoke_uri=info.get("revoke_uri"),
            )
        except (OSError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise InvalidConfigError("Invalid client secrets file") from exc

    def LoadServiceConfigSettings(self):
        """Loads client configuration from settings.
        :raises: InvalidConfigError
        """
        configs = [
            "client_json_file_path",
            "client_json_dict",
            "client_json",
            "client_pkcs12_file_path",
        ]

        for config in configs:
            value = self.settings["service_config"].get(config)
            if value:
                self.client_config[config] = value
                break
        else:
            raise InvalidConfigError(
                f"One of {configs} is required for service authentication"
            )

        service_config = self.settings["service_config"]
        self.client_config["client_user_email"] = service_config.get(
            "client_user_email"
        )
        if config == "client_pkcs12_file_path":
            if not service_config.get("client_service_email"):
                raise InvalidConfigError(
                    "Missing client_service_email for PKCS12 key"
                )
            self.client_config["client_service_email"] = service_config[
                "client_service_email"
            ]

    def LoadClientConfigSettings(self):
        """Loads client configuration from settings file.

        :raises: InvalidConfigError
        """
        for config in self.CLIENT_CONFIGS_LIST:
            try:
                self.client_config[config] = self.settings["client_config"][
                    config
                ]
            except KeyError:
                raise InvalidConfigError(
                    "Insufficient client config in settings"
                )

    def GetFlow(self):
        """Gets Flow object from client configuration.

        :raises: InvalidConfigError
        """
        if not all(
            config in self.client_config for config in self.CLIENT_CONFIGS_LIST
        ):
            self.LoadClientConfig()
        redirect_uri = self.client_config["redirect_uri"]
        if redirect_uri == "urn:ietf:wg:oauth:2.0:oob":
            redirect_uri = "http://localhost"
        self.flow = InstalledAppFlow.from_client_config(
            {"installed": self.client_config},
            scopes=self.settings["oauth_scope"],
            redirect_uri=redirect_uri,
            autogenerate_code_verifier=True,
        )

    def _authorization_params(self):
        if self.settings.get("get_refresh_token"):
            return {"access_type": "offline", "prompt": "consent"}
        return {"access_type": "online"}

    def _refresh_if_expired(self):
        with self._refresh_lock:
            if self.access_token_expired:
                self.Refresh()

    def Refresh(self):
        """Refreshes the access_token.

        :raises: RefreshError
        """
        with self._refresh_lock:
            if self.credentials is None:
                raise RefreshError("No credential to refresh.")
            user = isinstance(self.credentials, user_credentials.Credentials)
            if user and not (
                self.credentials.refresh_token
                or self.credentials.refresh_handler
            ):
                raise RefreshError(
                    "No refresh_token found. "
                    "Please set access_type of OAuth to offline."
                )
            if self.http is None:
                self.http = self._build_http()
            http = (
                self.http.http
                if isinstance(self.http, AuthorizedHttp)
                else self.http
            )
            try:
                self.credentials.refresh(Request(http))
            except GoogleRefreshError as exc:
                raise RefreshError(
                    f"Access token refresh failed: {exc}"
                ) from exc
            if (
                user
                and self.settings.get("save_credentials")
                and not isinstance(self.credentials, StoredUserCredentials)
            ):
                self._login_store.save(self.credentials)

    def GetAuthUrl(self):
        """Creates authentication url where user visits to grant access.

        :returns: str -- Authentication url.
        """
        if self.flow is None:
            self.GetFlow()
        url, _ = self.flow.authorization_url(**self._authorization_params())
        return url

    def Auth(self, code):
        """Authenticate, authorize, and build service.

        :param code: Code for authentication.
        :type code: str.
        :raises: AuthenticationError
        """
        self.Authenticate(code)
        self.Authorize()

    def Authenticate(self, code):
        """Authenticates given authentication code back from user.

        :param code: Code for authentication.
        :type code: str.
        :raises: AuthenticationError
        """
        if self.flow is None:
            self.GetFlow()
        try:
            self.flow.fetch_token(code=code)
            self._use_flow_credentials(self.flow.credentials)
        except (OAuth2Error, Warning) as exc:
            raise AuthenticationError(
                f"OAuth2 code exchange failed: {exc}"
            ) from exc
        print("Authentication successful.")

    def _build_http(self):
        http = httplib2.Http(timeout=self.http_timeout)
        # 308's are used by several Google APIs (Drive, YouTube)
        # for Resumable Uploads rather than Permanent Redirects.
        # This asks httplib2 to exclude 308s from the status codes
        # it treats as redirects
        # See also: https://stackoverflow.com/a/59850170/298182
        try:
            http.redirect_codes = http.redirect_codes - {308}
        except AttributeError:
            # http.redirect_codes does not exist in previous versions
            # of httplib2, so pass
            pass
        return http

    def Authorize(self):
        """Authorizes and builds service.

        :raises: AuthenticationError
        """
        if self.access_token_expired:
            raise AuthenticationError(
                "No valid credentials provided to authorize"
            )

        if self.http is None:
            self.http = self._build_http()
        if isinstance(self.http, AuthorizedHttp):
            self.http = self.http.http
        self.http = AuthorizedHttp(self.credentials, http=self.http)
        self.service = build(
            "drive", "v2", http=self.http, cache_discovery=False
        )

    def Get_Http_Object(self):
        """Create and authorize an httplib2.Http object. Necessary for
        thread-safety.
        :return: The http object to be used in each call.
        :rtype: httplib2.Http
        """
        return AuthorizedHttp(self.credentials, http=self._build_http())
