fsspec filesystem
=================

*PyDrive2* provides easy way to work with your files through `fsspec`_
compatible `GDriveFileSystem`_.

Installation
------------

.. code-block:: sh

  pip install 'pydrive2[fsspec]'

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
        # replace with ID of a drive or directory and give service account access to it
        "root",
        use_service_account=True,
        client_json_file_path="/path/to/keyfile.json",
    )

Using json keyfile string:

.. code-block:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem(
        # replace with ID of a drive or directory and give service account access to it
        "root",
        use_service_account=True,
        client_json=json_string,
    )

Use `client_user_email` if you are using `delegation of authority`_.

Additional parameters
---------------------

:trash_only (bool): Move files to trash instead of deleting.
:acknowledge_abuse (bool): Acknowledging the risk and download file identified as abusive. See `Abusive files`_ for more info.

Using filesystem
----------------

.. code-block:: python

    # replace `root` with ID of a drive or directory and give service account access to it
    for root, dnames, fnames in fs.walk("root"):
        for dname in dnames:
            print(f"dir: {root}/{dname}")
        
        for fname in fnames:
            print(f"file: {root}/{fname}")

Filesystem instance offers a large number of methods for getting information
about and manipulating files, refer to fsspec docs on
`how to use a filesystem`_.

.. _`fsspec`: https://filesystem-spec.readthedocs.io/en/latest/
.. _`GDriveFileSystem`: /PyDrive2/pydrive2/#pydrive2.fs.GDriveFileSystem
.. _`delegation of authority`: https://developers.google.com/admin-sdk/directory/v1/guides/delegation
.. _`Abusive files`: /PyDrive2/filemanagement/index.html#abusive-files
.. _`how to use a filesystem`: https://filesystem-spec.readthedocs.io/en/latest/usage.html#use-a-file-system
