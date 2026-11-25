fsspec filesystem
=================

:class:`~pydrive2.fs.GDriveFileSystem` provides an `fsspec`_-compatible
interface to Google Drive.

Installation
------------

.. code-block:: sh

  pip install 'pydrive2[fsspec]'

The ``[fsspec]`` extra installs fsspec. If fsspec is already installed,
installing plain PyDrive2 also registers ``gdrive://``. This registration
takes precedence over fsspec's built-in mapping to ``gdrive-fsspec``. If
both packages are installed, ``fsspec.open("gdrive://...")`` uses PyDrive2's
paths and credentials. To select the other backend, instantiate
``gdrive_fsspec.GoogleDriveFileSystem`` directly.

The first path component is a case-sensitive Drive ID, ``root``, or
``appDataFolder``. Open an existing file by ID, or name a file relative to a
folder that the account can access:

.. code-block:: python

    import fsspec

    options = {
        "use_service_account": True,
        "client_json_file_path": "/path/to/service-account.json",
    }
    with fsspec.open("gdrive://FILE_ID", "rb", **options) as stream:
        data = stream.read()

    with fsspec.open("gdrive://FOLDER_ID/report.csv", "rt", **options) as stream:
        header = stream.readline()

    fs = fsspec.filesystem("gdrive", path="FOLDER_ID", **options)
    names = fs.ls("FOLDER_ID")

A filesystem instance is scoped to one root. Use URLs under the same folder
ID or root alias in a single ``fsspec.open_files`` call. Sibling URLs reuse
the filesystem and its directory ID cache. Reads by file ID skip name
lookups. Reads are sequential. To create a file, use a folder ID or root
alias followed by a filename. As with the direct PyDrive2 filesystem,
a write creates a Drive item even when a sibling has the same name. All
reads require credentials, including files shared by link.

Use ``fs.rmdir("root/empty")`` to remove an empty folder. A nonempty
folder raises ``OSError`` with ``errno.ENOTEMPTY``. If the name is
ambiguous, open a filesystem at the intended folder ID and remove it by
ID. In trash mode, already trashed children are ignored. Permanent removal
also checks for trashed children. Drive checks emptiness and performs the
removal in separate requests, so coordinate concurrent writers when using
this operation.

Drive queries for directory entries exclude trashed items. For a bare file
ID, ``exists()`` returns false and ``info()`` raises ``FileNotFoundError``
when the item is in the trash. Opening or downloading by ID requests the
content directly. Drive can still serve a trashed file this way if the
account has access.

Local webserver
---------------

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        "root",
        client_id="my_client_id",
        client_secret="my_client_secret",
    )

By default, credentials will be cached per 'client_id', but if you are using
multiple users you might want to use 'profile' to avoid accidentally using
someone else's cached credentials:

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        "root",
        client_id="my_client_id",
        client_secret="my_client_secret",
        profile="myprofile",
    )

PyDrive2 2.x uses a separate default cache. The first interactive connection
after upgrading from 1.x prompts you to sign in. If you supplied a user
credential file explicitly, choose a fresh path. For DVC jobs that use
``GDRIVE_CREDENTIALS_DATA`` for a user account, replace that secret with the
newly saved JSON. Service-account jobs can reuse their configured key.

Writing cached credentials to a file and using it if it already exists (which
avoids interactive auth):

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        "root",
        client_id="my_client_id",
        client_secret="my_client_secret",
        client_json_file_path="/path/to/keyfile.json",
    )

Using cached credentials from json string (avoids interactive auth):

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        "root",
        client_id="my_client_id",
        client_secret="my_client_secret",
        client_json=json_string,
    )

Service account
---------------

Using json keyfile path:

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        # replace with ID of a drive or directory and give service account
        # access to it
        "root",
        use_service_account=True,
        client_json_file_path="/path/to/keyfile.json",
    )

Using json keyfile string:

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        # replace with ID of a drive or directory and give service account
        # access to it
        "root",
        use_service_account=True,
        client_json=json_string,
    )

If your service account uses `domain-wide delegation`_, set
``client_user_email`` to the email address of the user it should act as.

Additional parameters
---------------------

:trash_only (bool):
    Move files to trash instead of deleting.
:acknowledge_abuse (bool):
    Acknowledge the risk when downloading a file flagged as abusive.
    See :ref:`abusive-files`.

Using filesystem
----------------

.. code-block:: python

    # replace `root` with ID of a drive or directory and give service account
    # access to it
    for root, dnames, fnames in fs.walk("root"):
        for dname in dnames:
            print(f"dir: {root}/{dname}")

        for fname in fnames:
            print(f"file: {root}/{fname}")

For sequential reads, pass a positive ``block_size`` to ``fs.open()`` to set
the Drive download request size in bytes. A writer buffers data locally,
using a temporary file for larger uploads. Calling ``flush()`` flushes the
local buffer. Closing the writer uploads the file to Drive and reports any
upload error.

Filesystem instance offers a large number of methods for getting information
about and manipulating files, refer to fsspec docs on
`how to use a filesystem`_.

.. _`fsspec`: https://filesystem-spec.readthedocs.io/en/latest/
.. _domain-wide delegation:
   https://developers.google.com/workspace/guides/create-credentials#set-up-domain-wide-delegation
.. _`how to use a filesystem`:
   https://filesystem-spec.readthedocs.io/en/latest/usage.html#use-a-file-system
