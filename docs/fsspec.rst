fsspec filesystem
=================

:class:`~pydrive2.fs.GDriveFileSystem` provides an `fsspec`_-compatible
interface to Google Drive.

Installation
------------

.. code-block:: sh

    python -m pip install 'PyDrive2[fsspec]'

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

Use ``fs.rmdir("FOLDER_ID/empty")`` to remove an empty folder. A nonempty
folder raises ``OSError`` with ``errno.ENOTEMPTY``. If the name is
ambiguous, open a filesystem at the intended folder ID and remove it by
ID. In trash mode, already trashed children are ignored. Permanent removal
also checks for trashed children. For ordinary folders, the credentials
must grant access to list metadata across Drive. A per-file grant
(``drive.file``) can hide children. For a folder in the app data space,
the grant must cover app data. If the granted scopes are unknown,
``rmdir`` raises ``PermissionError``. Signing in again with PyDrive2 saves
the granted scopes alongside the token. Drive checks emptiness and performs the
removal in separate requests, so coordinate concurrent writers when using
this operation.

A filesystem instance is scoped to one root. Use URLs under the same folder
ID or root alias in a single ``fsspec.open_files`` call. Sibling URLs reuse
the filesystem and its directory ID cache. Reads by file ID skip name
lookups. Reads are sequential. To create a file, use a folder ID or root
alias followed by a filename. As with the direct PyDrive2 filesystem,
a write creates a Drive item even when a sibling has the same name. All
reads require credentials, including files shared by link.

The filesystem extra requires fsspec 2024.2.0 or newer. Use
``find(path, detail=True)`` to request metadata. The second positional
argument specifies traversal depth. Directory names returned by ``ls()``
and ``info()`` end in a slash. ``find(withdirs=True)`` returns directory
paths without a trailing slash, like fsspec's other traversal methods.

Named directory lookups reuse cached Drive IDs. Listing a parent refreshes
the folders seen under that parent. If a listing discovers a folder under
a new name, its cached children follow the folder's Drive ID.
If another client moves,
trashes, or deletes a folder, call ``fs.invalidate_cache("root/empty")``
before looking up its old path again. The next lookup checks Drive for all
folders with that name, including any same-name folder that still exists.

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

The filesystem caches OAuth user credentials per client ID. Give each user
a separate ``profile`` when several users share a client ID and machine.

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        "root",
        client_id="my_client_id",
        client_secret="my_client_secret",
        profile="myprofile",
    )

To choose where PyDrive2 saves and refreshes your user token, set
``client_json_file_path``. For existing deployments, see :doc:`upgrading`.

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        "root",
        client_id="my_client_id",
        client_secret="my_client_secret",
        client_json_file_path="/path/to/user-token.json",
    )

To load saved user credentials from a JSON string, pass ``client_json``.

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

Give the service account access to the folder and mount its ID.

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        "FOLDER_ID",
        use_service_account=True,
        client_json_file_path="/path/to/keyfile.json",
    )

You can also pass the service-account key as a JSON string.

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        "FOLDER_ID",
        use_service_account=True,
        client_json=json_string,
    )

If your service account uses `domain-wide delegation`_, set
``client_user_email`` to the email address of the user it should act as.

Additional parameters
---------------------

:trash_only (bool):
    Move files to the trash on removal (default: true). Set to false for
    permanent deletion.
:acknowledge_abuse (bool):
    Acknowledge the risk when downloading a file flagged as abusive.
    See :ref:`abusive-files`.

Using filesystem
----------------

.. code-block:: python

    fs.makedirs("FOLDER_ID/reports/2026", exist_ok=True)
    for parent, directories, files in fs.walk("FOLDER_ID/reports"):
        for name in files:
            print(f"{parent}/{name}")

``mkdir(path, create_parents=False)`` requires an existing parent.
``makedirs(path, exist_ok=True)`` creates missing parents and accepts an
existing directory. The filesystem caches directory IDs to speed up
repeated lookups. After an external move or deletion, use
``fs.invalidate_cache(path)`` for the affected path or
``fs.invalidate_cache()`` to clear the whole cache.

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
   https://developers.google.com/identity/protocols/oauth2/service-account#delegatingauthority
.. _`how to use a filesystem`:
   https://filesystem-spec.readthedocs.io/en/latest/usage.html#use-a-file-system
