Run tests locally
=================

From the repository root, install the test dependencies with Python 3.11
or newer and run the offline suite.

.. code-block:: sh

    python -m pip install -e '.[tests,fsspec]'
    python -m pytest -m "not manual and not drive"

Live Drive tests
----------------

The ``drive`` tests make real Google Drive requests, including creating,
sharing, and deleting test files. Use a dedicated test account with the
Drive API enabled. The filesystem tests create files under the account's
``root`` and require write access and storage there.

For the noninteractive suite, set ``GDRIVE_USER_CREDENTIALS_DATA`` to the
**contents of a service-account JSON key**. Some tests read this variable
directly. The helpers also write it to ``/tmp/pydrive2/credentials.json``
and prepare local copies of the settings templates. Run locally in a
private test environment and remove the generated credential files when
finished.

.. code-block:: sh

    python -m pytest -m "drive and not manual"

Manual OAuth tests
------------------

Tests marked ``manual`` require interaction. For local browser sign-in, use
a desktop OAuth client. Place its downloaded configuration in
``pydrive2/test/client_secrets.json`` locally and configure the corresponding
test settings under ``pydrive2/test/settings/local/`` when using inline client
settings. Run the desired test by node ID, for example:

.. code-block:: sh

    python -m pytest -m manual pydrive2/test/test_oauth.py -k test_01

Keep local credentials and changes to the example client configuration out
of commits.
