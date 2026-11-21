OAuth made easy
===============

Authentication in two lines
---------------------------

OAuth2.0 is complex and difficult to start with. To make it more simple,
*PyDrive2* makes all authentication into just two lines.

.. code-block:: python

    from pydrive2.auth import GoogleAuth

    gauth = GoogleAuth()
    # Create local webserver and auto handles authentication.
    gauth.LocalWebserverAuth()

The :doc:`quickstart` registers ``http://localhost:8080/`` as the redirect
URI for a web application client. With that client, use
:meth:`~pydrive2.auth.GoogleAuth.LocalWebserverAuth` and keep port 8080
available to receive the authorization code.

To open the login URL yourself, call
``gauth.LocalWebserverAuth(launch_browser=False)`` or
:meth:`~pydrive2.auth.GoogleAuth.CommandLineAuth`. Both print the URL
and wait for the same local callback. Open the URL in a browser that can
reach the callback port on the machine running PyDrive2. For unattended
jobs, use a service account or saved user credentials with a refresh token.

Automatic and custom authentication with *settings.yaml*
--------------------------------------------------------

Read this section if you need a custom authentication flow, **such as silent
authentication on a remote machine**. For an example of such a setup have a
look at `Sample settings.yaml`_.

OAuth is complicated and it requires a lot of settings. By default,
when you don't provide any settings, *PyDrive* will automatically set default
values which works for most of the cases. Here are some default settings.

- Read client configuration from file *client_secrets.json*
- OAuth scope: :code:`https://www.googleapis.com/auth/drive`
- Don't save credentials
- Don't retrieve refresh token

However, you might want to customize these settings while maintaining two lines
of clean code. If that is the case, you can make *settings.yaml* file in your
working directory and *PyDrive* will read it to customize authentication
behavior.

These are all the possible fields of a *settings.yaml* file:

.. code-block:: python

    client_config_backend: {{str}}
    client_config_file: {{str}}
    client_config:
      client_id: {{str}}
      client_secret: {{str}}
      auth_uri: {{str}}
      token_uri: {{str}}
      redirect_uri: {{str}}
      revoke_uri: {{str}}

    service_config:
      client_user_email: {{str}}
      client_json_file_path: {{str}}
      client_json_dict: {{dict}}
      client_json: {{str}}

    save_credentials: {{bool}}
    save_credentials_backend: {{str}}
    save_credentials_file: {{str}}
    save_credentials_dict: {{dict}}
    save_credentials_key: {{str}}

    get_refresh_token: {{bool}}

    oauth_scope: {{list of str}}

Fields explained:

:client_config_backend (str):
    From where to read client configuration(API application settings such as
    client_id and client_secrets) from. Valid values are 'file', 'settings' and
    'service'.
    **Default**: 'file'.
    **Required**: No.
:client_config_file (str):
    When *client_config_backend* is 'file', path to the file containing client
    configuration.
    **Default**: 'client_secrets.json'.
    **Required**: No.
:client_config (dict):
    Place holding dictionary for client configuration when
    *client_config_backend* is 'settings'.
    **Required**: Yes, only if *client_config_backend* is 'settings' and not
    using *service_config*
:client_config['client_id'] (str):
    Client ID of the application.
    **Required**: Yes, only if *client_config_backend* is 'settings'
:client_config['client_secret'] (str):
    Client secret of the application.
    **Required**: Yes, only if *client_config_backend* is 'settings'
:client_config['auth_uri'] (str):
    The authorization server endpoint URI.
    **Default**: ``https://accounts.google.com/o/oauth2/auth``.
    **Required**: No.
:client_config['token_uri'] (str):
    The token server endpoint URI.
    **Default**: ``https://accounts.google.com/o/oauth2/token``.
    **Required**: No.
:client_config['redirect_uri'] (str):
    Redirection endpoint URI.
    **Default**: ``http://localhost``.
    **Required**: No.
:client_config['revoke_uri'] (str):
    Revoke endpoint URI.
    **Default**: None.
    **Required**: No.
:service_config (dict):
    Place holding dictionary for client configuration when
    *client_config_backend* is 'service' or 'settings' and using service
    account.
    **Required**: Yes, only if *client_config_backend* is 'service' or
    'settings' and not using *client_config*
:service_config['client_user_email'] (str):
    Email address of the user the service account acts as through
    `domain-wide delegation`_.
    **Required**: No.
:service_config['client_json_file_path'] (str):
    Path to service account `.json` key file.
    **Required**: No.
:service_config['client_json_dict'] (dict):
    Service account `.json` key file loaded into a dictionary.
    **Required**: No.
:service_config['client_json'] (str):
    Service account `.json` key file loaded into a string.
    **Required**: No.
:save_credentials (bool):
    Save OAuth user credentials after signing in. A fresh login stays in
    memory when this setting is false. When you load saved credentials,
    PyDrive2 writes refreshed tokens back to the same file or dictionary key.
    **Default**: False.
    **Required**: No.
:save_credentials_backend (str):
    Backend to save credentials to. 'file' and 'dictionary' are the only valid
    values for now.
    **Default**: 'file'.
    **Required**: No.
:save_credentials_file (str):
    Destination of the credentials file. Give the application a writable
    directory for the lock and atomic file replacement. In containers, mount
    the cache directory so the file can be replaced on refresh.
    **Required**: Yes, only if *save_credentials_backend* is 'file'.
:save_credentials_dict (dict):
    Dict to use for storing credentials.
    **Required**: Yes, only if *save_credentials_backend* is 'dictionary'.
:save_credentials_key (str):
    Key within the *save_credentials_dict* to store the credentials in.
    **Required**: Yes, only if *save_credentials_backend* is 'dictionary'.
:get_refresh_token (bool):
    True if you want to retrieve refresh token along with access token.
    **Default**: False.
    **Required**: No.
:oauth_scope (list of str):
    OAuth scopes to request.
    **Default**: ``['https://www.googleapis.com/auth/drive']``.
    **Required**: No.

.. _domain-wide delegation:
   https://developers.google.com/workspace/guides/create-credentials#set-up-domain-wide-delegation

Upgrading from PyDrive2 1.x
___________________________

Sign in once with PyDrive2 2.x to create new OAuth user credentials. Keep
your existing OAuth client ID and client secret. If you configured a cache,
choose a new ``save_credentials_file`` or ``save_credentials_key`` before
signing in. For unattended jobs, set ``get_refresh_token: True`` during
sign-in and provision the newly saved credentials before upgrading the job.

Service accounts authenticate directly with their configured JSON or PKCS12
key. Keep that key available to the application. If your deployment relied
solely on a saved 1.x service-account cache, provision its signing key.

Sample *settings.yaml*
______________________

::

    client_config_backend: settings
    client_config:
      client_id: 9637341109347.apps.googleusercontent.com
      client_secret: psDskOoWr1P602PXRTHi

    save_credentials: True
    save_credentials_backend: file
    save_credentials_file: credentials.json

    get_refresh_token: True

    oauth_scope:
      - https://www.googleapis.com/auth/drive.file
      - https://www.googleapis.com/auth/drive.install
      - https://www.googleapis.com/auth/drive.metadata

Building your own authentication flow
-------------------------------------

You might want to build your own authentication flow. For example, you might
want to integrate your existing website with Drive API. In that case, you can
customize an authentication flow as follows:

1. Get the authorization URL from :meth:`~pydrive2.auth.GoogleAuth.GetAuthUrl`.
2. Direct the user to the authorization URL. Receive the authorization code at
   the redirect URI registered for your application and verify the OAuth state
   in the callback.
3. Pass the authorization code to :meth:`~pydrive2.auth.GoogleAuth.Auth` on
   the same instance. Keep the flow for each login until the callback so the
   exchange uses its original PKCE verifier. If a separate worker handles
   the callback, retain the state and verifier in the user's server-side
   session and restore the verifier on the flow before exchanging the code.

Your *settings.yaml* will work for your customized authentication flow, too.

Here is a sample code for your customized authentication flow

.. code-block:: python

    from pydrive2.auth import GoogleAuth

    # Configure your registered redirect URI in settings.yaml
    gauth = GoogleAuth()
    # Create authentication url user needs to visit
    auth_url = gauth.GetAuthUrl()
    # Your application handles the redirect
    code = ReceiveVerifiedCallback(auth_url)
    gauth.Auth(code) # Authorize and build service from the code

Authentication with a service account
-------------------------------------

A `Service account`_ is a special type of Google account intended to represent
a non-human user that needs to authenticate and be authorized to access data in
Google APIs.

Typically, service accounts are used in scenarios such as:

- Running workloads on virtual machines (VMs).
- Running workloads on data centers that call Google APIs.
- Running workloads which are not tied to the lifecycle of a human user.

If we use OAuth client ID we need to do one manual login into the account with
:meth:`~pydrive2.auth.GoogleAuth.LocalWebserverAuth`. If we use a service
account the login is automatic.


.. code-block:: python

    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive

    def login_with_service_account():
        """
        Google Drive service with a service account.
        note: for the service account to work, you need to share the folder or
        files with the service account email.

        :return: google auth
        """
        # Define the settings dict to use a service account
        # Set oauth_scope here to request specific Drive permissions.
        settings = {
                    "client_config_backend": "service",
                    "service_config": {
                        "client_json_file_path": "service-secrets.json",
                    }
                }
        # Create instance of GoogleAuth
        gauth = GoogleAuth(settings=settings)
        # Authenticate
        gauth.ServiceAuth()
        return gauth


.. _`Service account`:
   https://developers.google.com/workspace/guides/create-credentials#service-account
