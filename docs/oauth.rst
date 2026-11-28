.. _oauth-made-easy:

Authentication
==============

.. _authentication-in-two-lines:

Sign in from a local application
--------------------------------

Follow :doc:`quickstart` to create a desktop OAuth client and download its
configuration as ``client_secrets.json``. PyDrive2 uses google-auth to
authorize requests to Drive.

.. code-block:: python

    from pydrive2.auth import GoogleAuth

    gauth = GoogleAuth()
    gauth.LocalWebserverAuth()

:meth:`~pydrive2.auth.GoogleAuth.LocalWebserverAuth` opens the browser and
listens on localhost, trying port 8080 and then 8090 by default. If you use
a web application client, register the exact callback URI, such as
``http://localhost:8080/``, and pass ``port_numbers=[8080]``.

To open the sign-in URL yourself, call
``gauth.LocalWebserverAuth(launch_browser=False)`` or
:meth:`~pydrive2.auth.GoogleAuth.CommandLineAuth`. Both print the URL and
wait for the same local callback. The browser must be able to reach the
callback on the machine running PyDrive2. Unattended jobs can use a saved
refresh token or a service account.

.. _automatic-and-custom-authentication-with-settings-yaml:
.. _sample-settings-yaml:

Save credentials across runs
----------------------------

By default, ``GoogleAuth()`` reads client configuration from
``client_secrets.json``, requests the Drive scope, and keeps the access token
in memory. To reuse a login, create ``settings.yaml`` in your working
directory and sign in once.

.. code-block:: yaml

    client_config_backend: file
    client_config_file: client_secrets.json
    save_credentials: true
    save_credentials_backend: file
    save_credentials_file: credentials.json
    get_refresh_token: true

The client configuration identifies your OAuth application. The separate
``credentials.json`` file contains the user's tokens. Give the application a
writable cache directory so it can lock and atomically replace the token
file on refresh. In containers, mount the directory. For an existing cache
from an earlier release, see :doc:`upgrading`.

Settings reference
------------------

Pass a settings file path as ``GoogleAuth(settings_file="path/to/settings.yaml")``
or supply a Python dictionary with ``GoogleAuth(settings=settings)``.

``client_config_backend``
    Use ``file`` (the default) to read ``client_config_file``,
    ``settings`` to supply a ``client_config`` dictionary, or ``service``
    with ``service_config`` for a service account.
``client_config``
    For the ``settings`` backend, provide ``client_id`` and ``client_secret``.
    PyDrive2 supplies Google's ``auth_uri`` and ``token_uri`` and defaults
    ``redirect_uri`` to ``http://localhost``. For a web callback, set
    ``redirect_uri`` to the URI registered for the client. The optional
    ``revoke_uri`` field defaults to null.
``oauth_scope``
    A list of OAuth scope URLs. The default is
    ``https://www.googleapis.com/auth/drive``. Choose scopes that grant
    access to the files your application needs.
``save_credentials`` and ``get_refresh_token``
    Both default to false. Enable both to save the login and request
    offline access. Credentials loaded from a cache are written back to
    that cache when refreshed.
``save_credentials_backend``
    When saving credentials, select ``file`` with ``save_credentials_file``,
    or ``dictionary`` with ``save_credentials_dict`` and
    ``save_credentials_key``. To access the updated value in your program,
    pass the actual dictionary to ``GoogleAuth(settings=...)``.
``service_config``
    Supply the JSON signing key as a path (``client_json_file_path``),
    dictionary (``client_json_dict``), or JSON string (``client_json``).
    For a PKCS12 key, specify both ``client_pkcs12_file_path`` and
    ``client_service_email``. For domain-wide delegation, set
    ``client_user_email`` to the user the account should act as.

Building your own authentication flow
-------------------------------------

For an application with its own web callback, configure a web OAuth client
and its registered redirect URI. Start a separate authorization flow per
login and keep the ``GoogleAuth`` instance and expected state in that user's
server-side session until the callback. The flow holds the PKCE verifier
needed for the token exchange.

.. code-block:: python

    from secrets import compare_digest
    from urllib.parse import parse_qs, urlsplit

    def start_login():
        gauth = GoogleAuth()
        url = gauth.GetAuthUrl()
        state = parse_qs(urlsplit(url).query)["state"][0]
        return url, gauth, state

    def finish_login(gauth, expected_state, callback_params):
        if not compare_digest(expected_state, callback_params.get("state", "")):
            raise ValueError("OAuth state mismatch")
        gauth.Auth(callback_params["code"])

Redirect the user to the returned URL. On callback, retrieve and consume
the saved login from the session, then pass the decoded query parameters
to ``finish_login``. If several workers handle requests, the callback
worker needs the original verifier as well as the expected state.

Authentication with a service account
-------------------------------------

Give the service account access to the Drive folder or files it will use.
To create files, use a shared drive where it can write, or configure
`domain-wide delegation`_ to act as a Workspace user with storage.

.. code-block:: python

    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive

    settings = {
        "client_config_backend": "service",
        "service_config": {"client_json_file_path": "service-account.json"},
    }
    gauth = GoogleAuth(settings=settings)
    gauth.ServiceAuth()
    drive = GoogleDrive(gauth)

.. _domain-wide delegation: https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority

.. _upgrading-from-pydrive2-1-x:

Upgrading saved credentials
---------------------------

See :doc:`upgrading` for the first login, token cache, and service-account
changes in PyDrive2 2.0.
