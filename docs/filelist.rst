File listing made easy
======================

PyDrive2 handles pagination and returns
:class:`~pydrive2.files.GoogleDriveFile` instances.

Get all files which matches the query
-------------------------------------

Pass the `files.list query parameters`_ to
:meth:`~pydrive2.drive.GoogleDrive.ListFile` as a dictionary. It returns a
:class:`~pydrive2.files.GoogleDriveFileList`.
:meth:`~pydrive2.apiattr.ApiResourceList.GetList` fetches all matches when
you omit ``pageSize``. If you set ``pageSize``, each call fetches one page.

Use Drive v3 names such as ``name`` and ``modifiedTime`` in the
`file search guide`_. Request specific fields with a field mask, for
example ``{"fields": "files(id,name,parents)"}``. PyDrive2 requests the
next page token automatically so field selection preserves pagination.

.. code-block:: python

    from pydrive2.drive import GoogleDrive

    # Create GoogleDrive instance with authenticated GoogleAuth instance
    drive = GoogleDrive(gauth)

    # Auto-iterate through all files in the root folder.
    file_list = drive.ListFile(
        {'q': "'root' in parents and trashed=false"}
    ).GetList()
    for file1 in file_list:
        print('name: %s, id: %s' % (file1['name'], file1['id']))

You can update the metadata or content of each returned file.

Paginate and iterate through files
----------------------------------

To process one page at a time, iterate over the result of
:meth:`~pydrive2.drive.GoogleDrive.ListFile`. In this example, each page
contains up to ten files.

.. code-block:: python

    # Paginate file lists by specifying number of max results
    for file_list in drive.ListFile({'q': 'trashed=true', 'pageSize': 10}):
        print('Received %s files from Files.list()' % len(file_list))  # <= 10
        for file1 in file_list:
            print('name: %s, id: %s' % (file1['name'], file1['id']))


.. _files.list query parameters:
   https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list#query-parameters
.. _file search guide:
   https://developers.google.com/workspace/drive/api/guides/search-files
