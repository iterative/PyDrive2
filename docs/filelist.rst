File listing made easy
=============================

*PyDrive* handles paginations and parses response as list of `GoogleDriveFile`_.

Get all files which matches the query
-------------------------------------

Create `GoogleDriveFileList`_ instance with `parameters of Files.list()`_ as ``dict``. 
Call `GetList()`_ and you will get all files that matches your query as a list of `GoogleDriveFile`_.
The syntax and possible option of the query ``q`` parameter can be found in `search for files` Google documentation.

.. code-block:: python

    from pydrive2.drive import GoogleDrive

    drive = GoogleDrive(gauth) # Create GoogleDrive instance with authenticated GoogleAuth instance

    # Auto-iterate through all files in the root folder.
    file_list = drive.ListFile({'q': "'root' in parents and trashed=false"}).GetList()
    for file1 in file_list:
      print('title: %s, id: %s' % (file1['title'], file1['id']))

You can update metadata or content of these `GoogleDriveFile`_ instances if you need it.

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


.. _`GoogleDriveFile`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile
.. _`GoogleDriveFileList`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFileList
.. _`parameters of Files.list()`: https://developers.google.com/drive/v2/reference/files/list#request
.. _`GetList()`: /PyDrive2/pydrive2/#pydrive2.apiattr.ApiResourceList.GetList
.. _`search for files`: https://developers.google.com/drive/api/v2/search-files
