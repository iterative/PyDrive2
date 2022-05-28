import httplib2
import json
import oauth2client.clientsecrets as clientsecrets
import google.oauth2.credentials
import google.oauth2.service_account

from googleapiclient.discovery import build

from functools import wraps
from oauth2client.service_account import ServiceAccountCredentials
from oauth2client.client import FlowExchangeError
from oauth2client.client import AccessTokenRefreshError
from oauth2client.client import OAuth2WebServerFlow
from oauth2client.client import OOB_CALLBACK_URN
from oauth2client.file import Storage
from oauth2client.tools import ClientRedirectHandler
from oauth2client.tools import ClientRedirectServer
from oauth2client._helpers import scopes_to_string
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


DEFAULT_SETTINGS = {
    "client_config_backend": "file",
    "client_config_file": "client_secrets.json",
    "save_credentials": False,
    "oauth_scope": ["https://www.googleapis.com/auth/drive"],
}

_CLIENT_AUTH_PROMPT_MESSAGE = "Please visit this URL:\n{url}\n"


DEFAULT_SETTINGS = {
    "client_config_backend": "file",
    "client_config_file": "client_secrets.json",
    "save_credentials": False,
    "oauth_scope": ["https://www.googleapis.com/auth/drive"],
}


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


class GoogleAuth(ApiAttributeMixin):
    """Wrapper class for oauth2client library in google-api-python-client.

    Loads all settings and credentials from one 'settings.yaml' file
    and performs common OAuth2.0 related functionality such as authentication
    and authorization.
    """

    SERVICE_CONFIGS_LIST = ["client_user_email"]
    settings = ApiAttribute("settings")
    client_config = ApiAttribute("client_config")
    flow = ApiAttribute("flow")
    credentials = ApiAttribute("credentials")
    http = ApiAttribute("http")
    service = ApiAttribute("service")
    auth_method = ApiAttribute("auth_method")

    def __init__(self, settings_file="settings.yaml", http_timeout=None):
        """Create an instance of GoogleAuth.

        This constructor parses just he yaml settings file.
        All other settings are lazy

        :param settings_file: path of settings file. 'settings.yaml' by default.
        :type settings_file: str.
        """
        self.http_timeout = http_timeout
        ApiAttributeMixin.__init__(self)

        try:
            self.settings = LoadSettingsFile(settings_file)
        except SettingsError:
            self.settings = DEFAULT_SETTINGS
        else:
            # if no exceptions
            ValidateSettings(self.settings)
        self._storages = self._InitializeStoragesFromSettings()
        # Only one (`file`) backend is supported now
        self._default_storage = self._storages["file"]

        self._service = None
        self._credentials = None
        self._client_config = None
        self._oauth_type = None
        self._flow = None

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
    def credentials(self):
        if not self._credentials:
            if self.oauth_type in ("web", "installed"):
                self.LocalWebserverAuth()

            elif self.oauth_type == "service":
                self.ServiceAuth()
            else:
                raise InvalidConfigError(
                    "Only web, installed, service oauth is supported"
                )

        return self._credentials

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

        try:
            for port in port_numbers:
                self._credentials = self.flow.run_local_server(
                    host=host_name,
                    port=port,
                    authorization_prompt_message=_CLIENT_AUTH_PROMPT_MESSAGE,
                    open_browser=launch_browser,
                    **additional_config,
                )
                # if any port results in successful auth, we're done
                break

        except OSError as e:
            # OSError: [WinError 10048] ...
            # When WSGIServer.allow_reuse_address = False,
            # raise OSError when binding to a used port

            # If some other error besides the socket address reuse error
            if e.errno != 10048:
                raise

            print("Port {} is in use. Trying a different port".format(port))

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

        # If we have tried all ports and could not find a port
        if not self._credentials:
            print(
                "Failed to start a local web server. Please check your firewall"
            )
            print(
                "settings and locally running programs that may be blocking or"
            )
            print("using configured ports. Default ports are 8080 and 8090.")
            raise AuthenticationError()

    def CommandLineAuth(self):
        """Authenticate and authorize from user by printing authentication url
        retrieving authentication code from command-line.

        :returns: str -- code returned from commandline.
        """
        self.flow.redirect_uri = OOB_CALLBACK_URN
        authorize_url = self.GetAuthUrl()
        print("Go to the following link in your browser:")
        print()
        print("    " + authorize_url)
        print()
        return input("Enter verification code: ").strip()

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
        result = {"file": None}
        backend = self.settings.get("save_credentials_backend")
        save_credentials = self.settings.get("save_credentials")
        if backend == "file":
            credentials_file = self.settings.get("save_credentials_file")
            if credentials_file is None:
                raise InvalidConfigError(
                    "Please specify credentials file to read"
                )
            result[backend] = Storage(credentials_file)
        elif save_credentials:
            raise InvalidConfigError(
                "Unknown save_credentials_backend: %s" % backend
            )
        return result

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
            self._default_storage = self._storages["file"]
            if self._default_storage is None:
                raise InvalidConfigError(
                    "Backend `file` is not configured, specify "
                    "credentials file to read in the settings "
                    "file or pass an explicit value"
                )
        else:
            self._default_storage = Storage(credentials_file)

        try:
            self.credentials = self._default_storage.get()
        except OSError:
            raise InvalidCredentialsError(
                "Credentials file cannot be symbolic link"
            )

        if self.credentials:
            self.credentials.set_store(self._default_storage)

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
        if self.credentials is None:
            raise InvalidCredentialsError("No credentials to save")

        if credentials_file is None:
            storage = self._storages["file"]
            if storage is None:
                raise InvalidConfigError(
                    "Backend `file` is not configured, specify "
                    "credentials file to read in the settings "
                    "file or pass an explicit value"
                )
        else:
            storage = Storage(credentials_file)

        try:
            storage.put(self.credentials)
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

    def Refresh(self):
        """Refreshes the access_token.

        :raises: RefreshError
        """
        raise DeprecationWarning(
            "Refresh is now handled automatically within the new google.auth Credential objects. "
            "There's no need to manually refresh your credentials now."
        )

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
        """Create and authorize an httplib2.Http object. Necessary for
        thread-safety.
        :return: The http object to be used in each call.
        :rtype: httplib2.Http
        """
        return AuthorizedHttp(self.credentials, http=self._build_http())
