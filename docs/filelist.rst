File listing made easy
=============================

PyDrive2 handles pagination and returns
:class:`~pydrive2.files.GoogleDriveFile` instances.

Get all files which matches the query
-------------------------------------

Pass the `files.list query parameters`_ to
:meth:`~pydrive2.drive.GoogleDrive.ListFile` as a dictionary. It returns a
:class:`~pydrive2.files.GoogleDriveFileList`. Call
:meth:`~pydrive2.apiattr.ApiResourceList.GetList` to retrieve all matches.
Google's `file search guide`_ explains the ``q`` parameter. PyDrive2 uses
Drive API v2, so use ``title`` where the guide's v3 examples use ``name``.

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

*PyDrive* provides Pythonic way of paginating and iterating through list of files. 
Here is an example how to do this, ``maxResults`` below defines how many 
files it retrieves at once and we wrap it into a ``for`` loop to iterate:

Sample code continues from above:

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
