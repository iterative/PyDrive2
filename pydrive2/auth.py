import json
import webbrowser
import httplib2
import threading

from googleapiclient.discovery import build
from functools import wraps
import google.oauth2.credentials
import google.oauth2.service_account
from .storage import FileBackend, DictionaryBackend

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
    """
    Decorator to check the self.auth & self.http object in a decorated API call.
    Loads a new GoogleAuth or Http object if needed.
    """

    @wraps(decoratee)
    def _decorated(self, *args, **kwargs):
        # Initialize auth if needed.
        if self.auth is None:
            self.auth = GoogleAuth()

        # Ensure that a thread-safe HTTP object is provided.
        if (
            kwargs is not None
            and "param" in kwargs
            and kwargs["param"] is not None
            and "http" in kwargs["param"]
            and kwargs["param"]["http"] is not None
        ):
            # overwrites the HTTP objects used by the Gdrive API object
            self.http = kwargs["param"]["http"]
            del kwargs["param"]["http"]

        else:
            # If HTTP object not specified, resuse HTTP from self.auth.thread_local
            self.http = self.auth.authorized_http

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

    SERVICE_CONFIGS_LIST = ["client_user_email"]
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
        :param settings: settings dict.
        :type settings: dict.
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
        self._storage_registry = {}
        self._default_storage = None
        self._credentials = None

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
    def default_storage(self):
        if not self.settings.get("save_credentials"):
            return None

        if not self._default_storage:
            self._InitializeStoragesFromSettings()
        return self._default_storage

    @property
    def credentials(self):
        if not self._credentials:
            if self.oauth_type in ("web", "installed"):
                # try to load from backend if available
                # credentials would auto-refresh if expired
                if self.default_storage:
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
        # returns a thread-safe, local, cached HTTP object
        if not getattr(self.thread_local, "http", None):
            # If HTTP object not available in thread_local,
            # create and store Authorized Http object in thread_local storage
            self.thread_local.http = self.Get_Http_Object()

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

        This function is not for web server application. It creates local web
        server for user from standalone application.

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

        port_number = 0
        for port in port_numbers:
            port_number = port
            try:
                self._credentials = self.flow.run_local_server(
                    host=host_name,
                    port=port_number,
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
                if self.default_storage:
                    self.SaveCredentials()

                return

        # If we have tried all ports and could not find a port
        print("Failed to start a local web server. Please check your firewall")
        print("settings and locally running programs that may be blocking or")
        print("using configured ports. Default ports are 8080 and 8090.")
        raise AuthenticationError("None of the specified ports are available")

    def CommandLineAuth(self):
        """Authenticate and authorize from user by printing authentication url
        retrieving authentication code from command-line.

        :returns: str -- code returned from commandline.
        """
        raise DeprecationWarning(
            "The command line auth has been deprecated. "
            "The recommended alternative is to use local webserver auth with a loopback address."
        )

    def ServiceAuth(self):
        """Authenticate and authorize using P12 private key, client id
        and client email for a Service account.
        :raises: AuthError, InvalidConfigError
        """
        keyfile_name = self.client_config.get("client_json_file_path")
        keyfile_dict = self.client_config.get("client_json_dict")
        keyfile_json = self.client_config.get("client_json")

        # setting the subject for domain-wide delegation
        additional_config = {}
        additional_config["subject"] = self.client_config.get(
            "client_user_email"
        )
        additional_config["scopes"] = self.settings["oauth_scope"]

        if not keyfile_dict and keyfile_json:
            # Compensating for missing ServiceAccountCredentials.from_json_keyfile
            keyfile_dict = json.loads(keyfile_json)

        if keyfile_dict:
            self._credentials = google.oauth2.service_account.Credentials.from_service_account_info(
                keyfile_dict, **additional_config
            )
        elif keyfile_name:
            self._credentials = google.oauth2.service_account.Credentials.from_service_account_file(
                keyfile_name, **additional_config
            )
        else:
            raise AuthenticationError("Invalid service credentials")

    def _InitializeStoragesFromSettings(self):
        backend = self.settings.get("save_credentials_backend")
        save_credentials = self.settings.get("save_credentials")
        if backend == "file":
            credentials_file = self.settings.get("save_credentials_file")
            if credentials_file is None:
                raise InvalidConfigError(
                    "Please specify credentials file to read"
                )

            self._storage_registry[backend] = FileBackend()

        elif backend == "dictionary":
            creds_dict = self.settings.get("save_credentials_dict")
            if creds_dict is None:
                raise InvalidConfigError("Please specify credentials dict")

            creds_key = self.settings.get("save_credentials_key")
            if creds_key is None:
                raise InvalidConfigError("Please specify credentials key")

            self._storage_registry[backend] = DictionaryBackend(creds_dict)

        elif save_credentials:
            raise InvalidConfigError(
                "Unknown save_credentials_backend: %s" % backend
            )

        self._default_storage = self._storage_registry.get(backend)

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
        elif backend == "dictionary":
            self._LoadCredentialsDictionary()
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
            self._default_storage = self._storage_registry["file"]
            credentials_file = self.settings.get("save_credentials_file")
            if self._default_storage is None:
                raise InvalidConfigError(
                    "Backend `file` is not configured, specify "
                    "credentials file to read in the settings "
                    "file or pass an explicit value"
                )
        else:
            self._default_storage = FileBackend()

        try:
            auth_info = self.default_storage.read_credentials(credentials_file)
        except FileNotFoundError:
            # if credential was not found, raise the error for handling
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

    def _LoadCredentialsDictionary(self):
        self._default_storage = self._storage_registry["dictionary"]
        if self._default_storage is None:
            raise InvalidConfigError(
                "Backend `dictionary` is not configured, specify "
                "credentials dict and key to read in the settings file"
            )

        creds_key = self.settings.get("save_credentials_key")

        self._credentials = self.default_storage.read_credentials(creds_key)

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
        elif backend == "dictionary":
            self._SaveCredentialsDictionary()
        else:
            raise InvalidConfigError("Unknown save_credentials_backend")

    def SaveCredentialsFile(self, credentials_file=None):
        """Saves credentials to the file in JSON format.

        :param credentials_file: destination to save file to.
        :type credentials_file: str.
        :raises: InvalidConfigError, InvalidCredentialsError
        """
        if self._credentials is None:
            raise InvalidCredentialsError("No credentials to save")

        if credentials_file is None:
            credentials_file = self.settings.get("save_credentials_file")
            if credentials_file is None:
                raise InvalidConfigError(
                    "Please specify credentials file to read"
                )

        storage = self._storage_registry["file"]

        try:
            storage.store_credentials(self._credentials, credentials_file)

        except OSError:
            raise InvalidCredentialsError(
                "Credentials file cannot be symbolic link"
            )

    def _SaveCredentialsDictionary(self):
        if self._credentials is None:
            raise InvalidCredentialsError("No credentials to save")

        storage = self._storage_registry["dictionary"]
        if storage is None:
            raise InvalidConfigError(
                "Backend `dictionary` is not configured, specify "
                "credentials dict and key to write in the settings file"
            )

        creds_key = self.settings.get("save_credentials_key")
        storage.store_credentials(self._credentials, creds_key)

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
        except ValueError as error:
            raise InvalidConfigError("Invalid client secrets file: %s" % error)

        self._client_config = checked_config
        self._oauth_type = client_type

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
        service_config = {}

        for config in configs:
            value = self.settings["service_config"].get(config)
            if value:
                service_config[config] = value
                break
        else:
            raise InvalidConfigError(
                f"One of {configs} is required for service authentication"
            )

        if config == "client_pkcs12_file_path":
            # see https://github.com/googleapis/google-auth-library-python/issues/288
            raise DeprecationWarning(
                "PKCS#12 files are no longer supported in the new google.auth library. "
                "Please download a new json service credential file from google cloud console. "
                "For more info, visit https://github.com/googleapis/google-auth-library-python/issues/288"
            )

        for config in self.SERVICE_CONFIGS_LIST:
            try:
                service_config[config] = self.settings["service_config"].get(
                    config
                )
            except KeyError:
                err = "Insufficient service config in settings"
                err += f"\n\nMissing: {config} key."
                raise InvalidConfigError(err)

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

    def GetFlow(self, scopes=None, **kwargs):
        """Gets Flow object from client configuration.

        :raises: InvalidConfigError
        """
        if not scopes:
            scopes = self.settings.get("oauth_scope")

        if self.oauth_type in ("web", "installed"):
            self._flow = InstalledAppFlow.from_client_config(
                {self.oauth_type: self.client_config},
                scopes=scopes,
                **kwargs,
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
            "Manual refresh had been deprecated as the"
            "new google auth library handles refresh automatically"
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
        raise DeprecationWarning(
            "Manual authorization of HTTP will be deprecated as the"
            "new google auth library handles the adding to relevant oauth headers automatically"
        )

    def Get_Http_Object(self):
        """
        Helper function to get a new Authorized Http object.
        :return: The http object to be used in each call.
        :rtype: httplib2.Http
        """

        return AuthorizedHttp(self.credentials, http=self._build_http())
