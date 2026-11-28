PyDrive2
========

|CI| |PyPI| |conda-forge|

PyDrive2 is a Python client for the `Google Drive API v3`_ and an
`fsspec`_-compatible filesystem for Google Drive. Use it to upload, download,
and list files, or open them by URL with ``fsspec.open()``. PyDrive2 wraps
`google-api-python-client`_ and is a fork of `PyDrive`_ maintained by the
`DataChain`_ team.

Installation
------------

PyDrive2 2.0 requires Python 3.11 or newer.

.. code:: sh

    python -m pip install PyDrive2

To open ``gdrive://`` URLs with fsspec, install the filesystem extra.

.. code:: sh

    python -m pip install 'PyDrive2[fsspec]'

Authentication
--------------

Enable the Google Drive API in your Google Cloud project and create an
OAuth client for a desktop app. Save the downloaded JSON as
``client_secrets.json`` in your working directory. Run the following code
and sign in when the browser opens.

.. code:: python

    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive

    gauth = GoogleAuth()
    gauth.LocalWebserverAuth()
    drive = GoogleDrive(gauth)

You can configure authentication in ``settings.yaml``. The
`authentication guide`_ also covers service accounts.

Create, update, and download files
----------------------------------

The first ``Upload()`` creates the file. Call it again on the same object
to save changes to its name or contents.

.. code:: python

    note = drive.CreateFile({"name": "Hello.txt"})
    note.SetContentString("Hello")
    note.Upload()

    note["name"] = "HelloWorld.txt"
    note.SetContentString(note.GetContentString() + " World!")
    note.Upload()

To upload a local file, pass its path to ``SetContentFile()``. Use the Drive
ID to download it again.

.. code:: python

    photo = drive.CreateFile()
    photo.SetContentFile("hello.png")
    photo.Upload()

    download = drive.CreateFile({"id": photo["id"]})
    download.GetContentFile("downloaded.png")

To export an existing Google Doc, replace ``DOCUMENT_ID`` with its Drive ID
and specify the export format.

.. code:: python

    document = drive.CreateFile({"id": "DOCUMENT_ID"})
    document.GetContentFile("document.pdf", mimetype="application/pdf")

List files
----------

To collect all matching entries in My Drive's root folder, call
``GetList()`` without a page size. To process the same results a page at a
time, iterate over ``ListFile()`` directly.

.. code:: python

    query = "'root' in parents and trashed=false"
    entries = drive.ListFile({"q": query}).GetList()
    for entry in entries:
        print(entry["name"], entry["id"])

    for page in drive.ListFile({"q": query, "pageSize": 10}):
        for entry in page:
            print(entry["name"], entry["id"])

Use Google Drive with fsspec
----------------------------

Reuse the authenticated ``gauth`` object to read the note created above by
ID. You can also open a named path such as ``gdrive://root/HelloWorld.txt``
or list a folder through the `GDriveFileSystem`_ interface.

.. code:: python

    import fsspec

    with fsspec.open(f"gdrive://{note['id']}", "rt", google_auth=gauth) as stream:
        print(stream.read())  # Hello World!

    fs = fsspec.filesystem("gdrive", google_auth=gauth)
    print(fs.ls("root"))

Concurrent access
-----------------

PyDrive2 uses a separate HTTP transport for each thread. When using the Drive
client concurrently, create a separate file object or listing iterator in
each worker.

Project links
-------------

- `Documentation <https://docs.iterative.ai/PyDrive2/>`_
- `Source <https://github.com/iterative/PyDrive2>`_
- `Releases <https://github.com/iterative/PyDrive2/releases>`_
- `Running tests <https://github.com/iterative/PyDrive2/blob/main/pydrive2/test/README.rst>`_

Contributors
------------

Thanks to all our contributors!

.. image:: https://contrib.rocks/image?repo=iterative/PyDrive2
   :target: https://github.com/iterative/PyDrive2/graphs/contributors
   :alt: PyDrive2 contributors

.. |CI| image:: https://github.com/iterative/PyDrive2/actions/workflows/test.yml/badge.svg?branch=main
   :target: https://github.com/iterative/PyDrive2/actions/workflows/test.yml?query=branch%3Amain
   :alt: Tests on main

.. |PyPI| image:: https://raster.shields.io/pypi/v/pydrive2.png
   :target: https://pypi.org/project/PyDrive2/
   :alt: PyPI version

.. |conda-forge| image:: https://raster.shields.io/conda/vn/conda-forge/pydrive2.png
   :target: https://anaconda.org/conda-forge/pydrive2
   :alt: conda-forge version

.. _Google Drive API v3: https://developers.google.com/workspace/drive/api/guides/about-sdk
.. _fsspec: https://filesystem-spec.readthedocs.io/en/latest/
.. _google-api-python-client: https://github.com/google/google-api-python-client
.. _PyDrive: https://pypi.org/project/PyDrive/
.. _DataChain: https://datachain.ai/
.. _authentication guide: https://docs.iterative.ai/PyDrive2/oauth/
.. _GDriveFileSystem: https://docs.iterative.ai/PyDrive2/fsspec/
