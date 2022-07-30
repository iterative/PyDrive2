import httplib2
import json
import google.oauth2.credentials
import google.oauth2.service_account
import threading
from functools import wraps

from googleapiclient.discovery import build
from pydrive2.storage import FileBackend
from .apiattr import ApiAttribute
from .apiattr import ApiAttributeMixin
from .settings import LoadSettingsFile
from .settings import ValidateSettings
from .settings import SettingsError
from .settings import InvalidConfigError

from .auth_helpers import verify_client_config
from oauthlib.oauth2.rfc6749.errors import OAuth2Error, MissingCodeError
from google_auth_oauthlib.flow import InstalledAppFlow
from google_auth_httplib2 import AuthorizedHttp
from warnings import warn


_CLIENT_AUTH_PROMPT_MESSAGE = "Please visit this URL:\n{url}\n"


class AuthError(Exception):
    """Base error for authentication/authorization errors."""


class InvalidCredentialsError(IOError):
    """Error trying to read credentials file."""


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

        return decoratee(self, *args, **kwargs)

    return _decorated


class GoogleAuth(ApiAttributeMixin):
    """Wrapper class for oauth2client library in google-api-python-client.

    Loads all settings and credentials from one 'settings.yaml' file
    and performs common OAuth2.0 related functionality such as authentication
    and authorization.
    """

    DEFAULT_SETTINGS = {
        "client_config_backend": "file",
        "client_config_file": "client_secrets.json",
        "save_credentials": False,
        "oauth_scope": ["https://www.googleapis.com/auth/drive"],
    }

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

        This constructor parses just the yaml settings file.
        All other config & auth related objects are lazily loaded (see properties section)

        :param settings_file: path of settings file. 'settings.yaml' by default.
        :type settings_file: str.
        """
        self.http_timeout = http_timeout
        ApiAttributeMixin.__init__(self)
        self.thread_local = threading.local()

        if settings is None and settings_file:
            try:
                settings = LoadSettingsFile(settings_file)
            except SettingsError:
                pass

        self.settings = settings or self.DEFAULT_SETTINGS
        ValidateSettings(self.settings)

        self._service = None
        self._client_config = None
        self._oauth_type = None
        self._flow = None
        self._storage = None
        self._credentials = None

    # Lazy loading, read-only properties
    @property
    def service(self):
        if not self._service:
            self._service = build("drive", "v2", cache_discovery=False)
        return self._service

    @property
    def client_config(self):
        if not self._client_config:
            self.LoadClientConfig()
        return self._client_config

    @property
    def oauth_type(self):
        if not self._oauth_type:
            self.LoadClientConfig()
        return self._oauth_type

    @property
    def flow(self):
        if not self._flow:
            self.GetFlow()
        return self._flow

    @property
    def storage(self):
        if not self.settings.get("save_credentials"):
            return None

        if not self._storage:
            self._InitializeStoragesFromSettings()
        return self._storage

    @property
    def credentials(self):
        if not self._credentials:
            if self.oauth_type in ("web", "installed"):
                # try to load from backend if available
                # credentials would auto-refresh if expired
                if self.storage:
                    try:
                        self.LoadCredentials()
                        return self._credentials
                    except FileNotFoundError:
                        pass

                self.LocalWebserverAuth()

            elif self.oauth_type == "service":
                self.ServiceAuth()
            else:
                raise InvalidConfigError(
                    "Only web, installed, service oauth is supported"
                )

        return self._credentials

    @property
    def authorized_http(self):
        # Ensure that a thread-safe, Authorized HTTP object is provided
        # If HTTP object not specified, create or resuse an HTTP
        # object from the thread local storage.
        if not getattr(self.thread_local, "http", None):
            self.thread_local.http = AuthorizedHttp(
                self.credentials, http=self._build_http()
            )

        return self.thread_local.http

    # Other properties
    @property
    def access_token_expired(self):
        """Checks if access token doesn't exist or is expired.

        :returns: bool -- True if access token doesn't exist or is expired.
        """
        if not self.credentials:
            return True

        return not self.credentials.valid

    def LocalWebserverAuth(
        self, host_name="localhost", port_numbers=None, launch_browser=True
    ):
        """Authenticate and authorize from user by creating local web server and
        retrieving authentication code.

        This function is not for web server application. It creates local web server
        for user from standalone application.

        :param host_name: host name of the local web server.
        :type host_name: str.
        :param port_numbers: list of port numbers to be tried to used.
        :type port_numbers: list.
        :param launch_browser: should browser be launched automatically
        :type launch_browser: bool
        :returns: str -- code returned from local web server
        :raises: AuthenticationRejected, AuthenticationError
        """
        if port_numbers is None:
            port_numbers = [8080, 8090]

        additional_config = {}
        # offline token request needed to obtain refresh token
        # make sure that consent is requested
        if self.settings.get("get_refresh_token"):
            additional_config["access_type"] = "offline"
            additional_config["prompt"] = "select_account"

        for port in port_numbers:
            try:
                self._credentials = self.flow.run_local_server(
                    host=host_name,
                    port=port,
                    authorization_prompt_message=_CLIENT_AUTH_PROMPT_MESSAGE,
                    open_browser=launch_browser,
                    **additional_config,
                )

            except OSError as e:
                print(
                    "Port {} is in use. Trying a different port".format(port)
                )

            except MissingCodeError as e:
                # if code is not found in the redirect uri's query parameters
                print(
                    "Failed to find 'code' in the query parameters of the redirect."
                )
                print("Please check that your redirect uri is correct.")
                raise AuthenticationError("No code found in redirect uri")

            except OAuth2Error as e:
                # catch all other oauth 2 errors
                print("Authentication request was rejected")
                raise AuthenticationRejected("User rejected authentication")

            # if any port results in successful auth, we're done
            if self._credentials:
                if self.storage:
                    self.SaveCredentials()

                return

        # If we have tried all ports and could not find a port
        print("Failed to start a local web server. Please check your firewall")
        print("settings and locally running programs that may be blocking or")
        print("using configured ports. Default ports are 8080 and 8090.")
        raise AuthenticationError()

    def CommandLineAuth(self):
        """Authenticate and authorize from user by printing authentication url
        retrieving authentication code from command-line.

        :returns: str -- code returned from commandline.
        """

        warn(
            (
                "The command line auth has been deprecated. "
                "The recommended alternative is to use local webserver auth with a loopback address."
            ),
            DeprecationWarning,
        )

        self.LocalWebserverAuth(host_name="127.0.0.1")

    def ServiceAuth(self):
        """Authenticate and authorize using P12 private key, client id
        and client email for a Service account.
        :raises: AuthError, InvalidConfigError
        """
        client_service_json = self.client_config.get("client_json_file_path")

        if client_service_json:
            additional_config = {}
            additional_config["subject"] = self.client_config.get(
                "client_user_email"
            )
            additional_config["scopes"] = self.settings["oauth_scope"]

            self._credentials = google.oauth2.service_account.Credentials.from_service_account_file(
                client_service_json, **additional_config
            )

        elif self.client_config.get("use_default"):
            # if no service credential file in yaml settings
            # default to checking env var `GOOGLE_APPLICATION_CREDENTIALS`
            credentials, _ = google.auth.default(
                scopes=self.settings["oauth_scope"]
            )
            self._credentials = credentials

    def _InitializeStoragesFromSettings(self):
        if self.settings.get("save_credentials"):
            backend = self.settings.get("save_credentials_backend")
            if backend != "file":
                raise InvalidConfigError(
                    "Unknown save_credentials_backend: %s" % backend
                )

            self._storage = FileBackend()

    def LoadCredentials(self, backend=None):
        """Loads credentials or create empty credentials if it doesn't exist.

        :param backend: target backend to save credential to.
        :type backend: str.
        :raises: InvalidConfigError
        """
        if backend is None:
            backend = self.settings.get("save_credentials_backend")
            if backend is None:
                raise InvalidConfigError("Please specify credential backend")
        if backend == "file":
            self.LoadCredentialsFile()
        else:
            raise InvalidConfigError("Unknown save_credentials_backend")

    def LoadCredentialsFile(self, credentials_file=None):
        """Loads credentials or create empty credentials if it doesn't exist.

        Loads credentials file from path in settings if not specified.

        :param credentials_file: path of credentials file to read.
        :type credentials_file: str.
        :raises: InvalidConfigError, InvalidCredentialsError
        """
        if credentials_file is None:
            credentials_file = self.settings.get("save_credentials_file")
            if credentials_file is None:
                raise InvalidConfigError(
                    "Please specify credentials file to read"
                )

        try:
            auth_info = self.storage.read_credentials(credentials_file)
        except FileNotFoundError:
            # if credential found was not found, raise the error for handling
            raise
        except OSError:
            # catch other errors
            raise InvalidCredentialsError(
                "Credentials file cannot be symbolic link"
            )

        try:
            self._credentials = google.oauth2.credentials.Credentials.from_authorized_user_info(
                auth_info, scopes=auth_info["scopes"]
            )
        except ValueError:
            # if saved credentials lack a refresh token
            # handled for backwards compatibility
            warn(
                "Loading authorized user credentials without a refresh token is "
                "not officially supported by google auth library. We recommend that "
                "you only store refreshable credentials moving forward."
            )

            self._credentials = google.oauth2.credentials.Credentials(
                token=auth_info.get("token"),
                token_uri="https://oauth2.googleapis.com/token",  # always overrides
                scopes=auth_info.get("scopes"),
                client_id=auth_info.get("client_id"),
                client_secret=auth_info.get("client_secret"),
            )

        # in-case reauth / consent required
        # create a flow object so that reauth flow can be triggered
        additional_config = {}
        if self.credentials.refresh_token:
            additional_config["access_type"] = "offline"

        self._flow = InstalledAppFlow.from_client_config(
            {self.oauth_type: self.client_config},
            self.credentials.scopes,
            **additional_config,
        )

    def SaveCredentials(self, backend=None):
        """Saves credentials according to specified backend.

        If you have any specific credentials backend in mind, don't use this
        function and use the corresponding function you want.

        :param backend: backend to save credentials.
        :type backend: str.
        :raises: InvalidConfigError
        """
        if backend is None:
            backend = self.settings.get("save_credentials_backend")
            if backend is None:
                raise InvalidConfigError("Please specify credential backend")
        if backend == "file":
            self.SaveCredentialsFile()
        else:
            raise InvalidConfigError("Unknown save_credentials_backend")

    def SaveCredentialsFile(self, credentials_file=None):
        """Saves credentials to the file in JSON format.

        :param credentials_file: destination to save file to.
        :type credentials_file: str.
        :raises: InvalidConfigError, InvalidCredentialsError
        """
        if not self.credentials:
            raise InvalidCredentialsError("No credentials to save")

        if credentials_file is None:
            credentials_file = self.settings.get("save_credentials_file")
            if credentials_file is None:
                raise InvalidConfigError(
                    "Please specify credentials file to read"
                )

        try:
            self.storage.store_credentials(self.credentials, credentials_file)

        except OSError:
            raise InvalidCredentialsError(
                "Credentials file cannot be symbolic link"
            )

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

        with open(client_config_file, "r") as json_file:
            client_config = json.load(json_file)

        try:
            # check the format of the loaded client config
            client_type, checked_config = verify_client_config(client_config)
        except ValueError as e:
            raise InvalidConfigError("Invalid client secrets file: %s" % e)

        self._client_config = checked_config
        self._oauth_type = client_type

    def LoadServiceConfigSettings(self):
        """Loads client configuration from settings file.
        :raises: InvalidConfigError
        """
        service_config = self.settings["service_config"]

        # see https://github.com/googleapis/google-auth-library-python/issues/288
        if "client_pkcs12_file_path" in service_config:
            raise DeprecationWarning(
                "PKCS#12 files are no longer supported in the new google.auth library. "
                "Please download a new json service credential file from google cloud console. "
                "For more info, visit https://github.com/googleapis/google-auth-library-python/issues/288"
            )

        self._client_config = service_config
        self._oauth_type = "service"

    def LoadClientConfigSettings(self):
        """Loads client configuration from settings file.

        :raises: InvalidConfigError
        """

        try:
            client_config = self.settings["client_config"]
        except KeyError as e:
            raise InvalidConfigError(
                "Settings does not contain 'client_config'"
            )

        try:
            _, checked_config = verify_client_config(
                client_config, with_oauth_type=False
            )
        except ValueError as e:
            raise InvalidConfigError("Invalid client secrets file: %s" % e)

        # assumed to be Installed App Flow as the Local Server Auth is appropriate for this type of device
        self._client_config = checked_config
        self._oauth_type = "installed"

    def GetFlow(self):
        """Gets Flow object from client configuration.

        :raises: InvalidConfigError
        """
        additional_config = {}
        scopes = self.settings.get("oauth_scope")

        if self.oauth_type in ("web", "installed"):
            self._flow = InstalledAppFlow.from_client_config(
                {self.oauth_type: self.client_config},
                scopes,
                **additional_config,
            )

        if self.oauth_type == "service":
            # In a service oauth2 flow,
            # the oauth subject does not have to provide any consent via the client
            pass

    def GetAuthUrl(self):
        """Creates authentication url where user visits to grant access.

        :returns: str -- Authentication url.
        """
        if self.oauth_type == "service":
            raise AuthenticationError(
                "Authentication is not required for service client type."
            )

        return self.flow.authorization_url()

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
        if self.oauth_type == "service":
            raise AuthenticationError(
                "Authentication is not required for service client type."
            )

        try:
            self.flow.fetch_token(code=code)

        except MissingCodeError as e:
            # if code is not found in the redirect uri's query parameters
            print(
                "Failed to find 'code' in the query parameters of the redirect."
            )
            print("Please check that your redirect uri is correct.")
            raise AuthenticationError("No code found in redirect")

        except OAuth2Error as e:
            # catch oauth 2 errors
            print("Authentication request was rejected")
            raise AuthenticationRejected("User rejected authentication")

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

    def Get_Http_Object(self):
        """Alias for self.authorized_http. To avoid creating multiple Http Objects by caching it per thread.
        :return: The http object to be used in each call.
        :rtype: httplib2.Http
        """

        # updated as alias for
        return self.authorized_http
