File listing made easy
=============================

PyDrive2 handles pagination and returns
:class:`~pydrive2.files.GoogleDriveFile` instances.

Get all files which matches the query
-------------------------------------

Pass the `files.list query parameters`_ to
:meth:`~pydrive2.drive.GoogleDrive.ListFile` as a dictionary. It returns a
:class:`~pydrive2.files.GoogleDriveFileList`.
:meth:`~pydrive2.apiattr.ApiResourceList.GetList` fetches all matches when
you omit ``maxResults``. If you set ``maxResults``, each call fetches one page.

Google's `file search guide`_ explains the ``q`` parameter using Drive API
v3 examples. PyDrive2 uses v2. When adapting a query, check the
`field comparison`_ for renamed fields. For example, use ``title`` for
``name`` and ``modifiedDate`` for ``modifiedTime``.

.. code-block:: python

    from pydrive2.drive import GoogleDrive

    drive = GoogleDrive(gauth) # Create GoogleDrive instance with authenticated GoogleAuth instance

    # Auto-iterate through all files in the root folder.
    file_list = drive.ListFile({'q': "'root' in parents and trashed=false"}).GetList()
    for file1 in file_list:
      print('title: %s, id: %s' % (file1['title'], file1['id']))

You can update the metadata or content of each returned file.

Paginate and iterate through files
----------------------------------

To process one page at a time, iterate over the result of
:meth:`~pydrive2.drive.GoogleDrive.ListFile`. In this example, each page
contains up to ten files.

.. code-block:: python

    # Paginate file lists by specifying number of max results
    for file_list in drive.ListFile({'q': 'trashed=true', 'maxResults': 10}):
      print('Received %s files from Files.list()' % len(file_list)) # <= 10
      for file1 in file_list:
          print('title: %s, id: %s' % (file1['title'], file1['id']))


.. _files.list query parameters:
   https://developers.google.com/workspace/drive/api/reference/rest/v2/files/list#query-parameters
.. _file search guide:
   https://developers.google.com/workspace/drive/api/guides/search-files
.. _field comparison:
   https://developers.google.com/workspace/drive/api/guides/v2-to-v3-reference#resource_field_differences_between_v2_and_v3
