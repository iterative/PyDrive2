|CI| |Conda| |PyPI|

.. |CI| image:: https://github.com/iterative/PyDrive2/workflows/Tests/badge.svg?branch=master
   :target: https://github.com/iterative/PyDrive2/actions
   :alt: GHA Tests

.. |Conda| image:: https://img.shields.io/conda/v/conda-forge/PyDrive2.svg?label=conda&logo=conda-forge
   :target: https://anaconda.org/conda-forge/PyDrive2
   :alt: Conda-forge

.. |PyPI| image:: https://img.shields.io/pypi/v/PyDrive2.svg?label=pip&logo=PyPI&logoColor=white
   :target: https://pypi.org/project/PyDrive2
   :alt: PyPI

PyDrive2
--------

*PyDrive2* is a wrapper library of
`google-api-python-client <https://github.com/google/google-api-python-client>`_
that simplifies many common Google Drive API V2 tasks. It is an actively
maintained fork of `https://pypi.python.org/pypi/PyDrive <https://pypi.python.org/pypi/PyDrive>`_.
By the authors and maintainers of the `Git for Data <https://dvc.org>`_ - DVC
project.

Project Info
------------

- Package: `https://pypi.python.org/pypi/PyDrive2 <https://pypi.python.org/pypi/PyDrive2>`_
- Documentation: `https://docs.iterative.ai/PyDrive2 <https://docs.iterative.ai/PyDrive2>`_
- Source: `https://github.com/iterative/PyDrive2 <https://github.com/iterative/PyDrive2>`_
- Changelog: `https://github.com/iterative/PyDrive2/releases <https://github.com/iterative/PyDrive2/releases>`_
- `Running tests </pydrive2/test/README.rst>`_

Features of PyDrive2
--------------------

-  Simplifies OAuth2.0 into just few lines with flexible settings.
-  Wraps `Google Drive API V2 <https://developers.google.com/drive/v2/web/about-sdk>`_ into
   classes of each resource to make your program more object-oriented.
-  Helps common operations else than API calls, such as content fetching
   and pagination control.
-  Provides `fsspec`_ filesystem implementation.

How to install
--------------

You can install PyDrive2 with regular ``pip`` command.

::

    $ pip install PyDrive2

To install the current development version from GitHub, use:

::

    $  pip install git+https://github.com/iterative/PyDrive2.git#egg=PyDrive2

OAuth made easy
---------------

Download *client\_secrets.json* from Google API Console and OAuth2.0 is
done in two lines. You can customize behavior of OAuth2 in one settings
file *settings.yaml*.

.. code:: python


    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive

    gauth = GoogleAuth()
    gauth.LocalWebserverAuth()

    drive = GoogleDrive(gauth)

File management made easy
-------------------------

Upload/update the file with one method. PyDrive2 will do it in the most
efficient way.

.. code:: python

    file1 = drive.CreateFile({'title': 'Hello.txt'})
    file1.SetContentString('Hello')
    file1.Upload() # Files.insert()

    file1['title'] = 'HelloWorld.txt'  # Change title of the file
    file1.Upload() # Files.patch()

    content = file1.GetContentString()  # 'Hello'
    file1.SetContentString(content+' World!')  # 'Hello World!'
    file1.Upload() # Files.update()

    file2 = drive.CreateFile()
    file2.SetContentFile('hello.png')
    file2.Upload()
    print('Created file %s with mimeType %s' % (file2['title'],
    file2['mimeType']))
    # Created file hello.png with mimeType image/png

    file3 = drive.CreateFile({'id': file2['id']})
    print('Downloading file %s from Google Drive' % file3['title']) # 'hello.png'
    file3.GetContentFile('world.png')  # Save Drive file as a local file

    # or download Google Docs files in an export format provided.
    # downloading a docs document as an html file:
    docsfile.GetContentFile('test.html', mimetype='text/html')

File listing pagination made easy
---------------------------------

*PyDrive2* handles file listing pagination for you.

.. code:: python

    # Auto-iterate through all files that matches this query
    file_list = drive.ListFile({'q': "'root' in parents"}).GetList()
    for file1 in file_list:
        print('title: {}, id: {}'.format(file1['title'], file1['id']))

    # Paginate file lists by specifying number of max results
    for file_list in drive.ListFile({'maxResults': 10}):
        print('Received {} files from Files.list()'.format(len(file_list))) # <= 10
        for file1 in file_list:
            print('title: {}, id: {}'.format(file1['title'], file1['id']))

Fsspec filesystem
-----------------

*PyDrive2* provides easy way to work with your files through `fsspec`_
compatible `GDriveFileSystem`_.

.. code:: python

    from pydrive2.fs import GDriveFileSystem

    fs = GDriveFileSystem("root", client_id=my_id, client_secret=my_secret)

    for root, dnames, fnames in fs.walk(""):
        ...

.. _`GDriveFileSystem`: https://docs.iterative.ai/PyDrive2/fsspec/

Concurrent access made easy
---------------------------

All API functions made to be thread-safe.

Contributors
------------

Thanks to all our contributors!

.. image:: https://contrib.rocks/image?repo=iterative/PyDrive2
   :target: https://github.com/iterative/PyDrive2/graphs/contributors

.. _`fsspec`: https://filesystem-spec.readthedocs.io/en/latest/
