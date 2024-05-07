File management made easy
=========================

There are many methods to create and update file metadata and contents.
With *PyDrive*, you don't have to care about any of these different API methods.
Manipulate file metadata and contents from `GoogleDriveFile`_ object and call
`Upload()`_. *PyDrive* will make the optimal API call for you.

Upload a new file
-----------------

Here is a sample code to upload a file. ``gauth`` is an authenticated `GoogleAuth`_ object.

.. code-block:: python

    from pydrive2.drive import GoogleDrive

    # Create GoogleDrive instance with authenticated GoogleAuth instance.
    drive = GoogleDrive(gauth)

    # Create GoogleDriveFile instance with title 'Hello.txt'.
    file1 = drive.CreateFile({'title': 'Hello.txt'})
    file1.Upload() # Upload the file.
    print('title: %s, id: %s' % (file1['title'], file1['id']))
    # title: Hello.txt, id: {{FILE_ID}}

Now, you will have a file 'Hello.txt' uploaded to your Google Drive. You can open it from web interface to check its content, 'Hello World!'.

Note that `CreateFile()`_ will create `GoogleDriveFile`_ instance but not actually upload a file to Google Drive. You can initialize `GoogleDriveFile`_ object by itself. However, it is not recommended to do so in order to keep authentication consistent.

Delete, Trash and un-Trash files
--------------------------------
You may want to delete, trash, or un-trash a file. To do this use ``Delete()``,
``Trash()`` or ``UnTrash()`` on a GoogleDriveFile object.

*Note:* ``Trash()`` *moves a file into the trash and can be recovered,*
``Delete()`` *deletes the file permanently and immediately.*

.. code-block:: python

    # Create GoogleDriveFile instance and upload it.
    file1 = drive.CreateFile()
    file1.Upload()

    file1.Trash()  # Move file to trash.
    file1.UnTrash()  # Move file out of trash.
    file1.Delete()  # Permanently delete the file.

Update file metadata
--------------------

You can manipulate file metadata from a `GoogleDriveFile`_ object just as you manipulate a ``dict``.
The format of file metadata can be found in the Google Drive API documentation: `Files resource`_.

Sample code continues from `Upload a new file`_:

.. code-block:: python

    file1['title'] = 'HelloWorld.txt' # Change title of the file.
    file1.Upload() # Update metadata.
    print('title: %s' % file1['title']) # title: HelloWorld.txt.

Now, the title of your file has changed to 'HelloWorld.txt'.

Download file metadata from file ID
-----------------------------------

You might want to get file metadata from file ID. In that case, just initialize
`GoogleDriveFile`_ with file ID and access metadata from `GoogleDriveFile`_
just as you access ``dict``.

Sample code continues from above:

.. code-block:: python

    # Create GoogleDriveFile instance with file id of file1.
    file2 = drive.CreateFile({'id': file1['id']})
    print('title: %s, mimeType: %s' % (file2['title'], file2['mimeType']))
    # title: HelloWorld.txt, mimeType: text/plain

Handling special metadata
-------------------------

Not all metadata can be set with the methods described above.
PyDrive gives you access to the metadata of an object through
``file_object.FetchMetadata()``. This function has two optional parameters:
``fields`` and ``fetch_all``.

.. code-block:: python

    file1 = drive.CreateFile({'id': '<some file ID here>'})

    # Fetches all basic metadata fields, including file size, last modified etc.
    file1.FetchMetadata()

    # Fetches all metadata available.
    file1.FetchMetadata(fetch_all=True)

    # Fetches the 'permissions' metadata field.
    file1.FetchMetadata(fields='permissions')
    # You can update a list of specific fields like this:
    file1.FetchMetadata(fields='permissions,labels,mimeType')

For more information on available metadata fields have a look at the
`official documentation`_.

Insert permissions
__________________
Insert, retrieving or deleting permissions is illustrated by making a file
readable to all who have a link to the file.

.. code-block:: python

    file1 = drive.CreateFile()
    file1.Upload()

    # Insert the permission.
    permission = file1.InsertPermission({
                            'type': 'anyone',
                            'value': 'anyone',
                            'role': 'reader'})

    print(file1['alternateLink'])  # Display the sharable link.

Note: ``InsertPermission()`` calls ``GetPermissions()`` after successfully
inserting the permission.

You can find more information on the permitted fields of a permission
`here <https://developers.google.com/drive/v2/reference/permissions/insert#request-body>`_.
This file is now shared and anyone with the link can view it. But what if you
want to check whether a file is already shared?

List permissions
________________

Permissions can be fetched using the ``GetPermissions()`` function of a
``GoogleDriveFile``, and can be used like so:

.. code-block:: python

    # Create a new file
    file1 = drive.CreateFile()
    # Fetch permissions.
    permissions = file1.GetPermissions()
    print(permissions)

    # The permissions are also available as file1['permissions']:
    print(file1['permissions'])

For the more advanced user: ``GetPermissions()`` is a shorthand for:

.. code-block:: python

    # Fetch Metadata, including the permissions field.
    file1.FetchMetadata(fields='permissions')

    # The permissions array is now available for further use.
    print(file1['permissions'])

Remove a Permission
___________________
*PyDrive* allows you to remove a specific permission using the
``DeletePermission(permission_id)`` function. This function allows you to delete
one permission at a time by providing the permission's ID.

.. code-block:: python

    file1 = drive.CreateFile({'id': '<file ID here>'})
    permissions = file1.GetPermissions()  # Download file permissions.

    permission_id = permissions[1]['id']  # Get a permission ID.

    file1.DeletePermission(permission_id)  # Delete the permission.

Get files by complex queries
----------------------------

We can get a file by name and by other constraints, usually a filename will be
unique but we can have two equal names with different extensions, e.g.,
*123.jpeg and 123.mp3*. So if you expect only one file add more constraints to
the query, see `Query string examples <query_parameters>`_, as a result we get
a list of `GoogleDriveFile`_ instances.

.. code-block:: python

    from pydrive2.drive import GoogleDrive
    # Create GoogleDrive instance with authenticated GoogleAuth instance.
    drive = GoogleDrive(gauth)
    filename = 'file_test'
    # Query
    query = {'q': f"title = '{filename}' and mimeType='{mimetype}'"}
    # Get list of files that match against the query
    files = drive.ListFile(query).GetList()

List revisions
________________

Revisions can be fetched using the ``GetRevisions()`` function of a
``GoogleDriveFile``, and can be used like so:

.. code-block:: python

    # Create a new file
    file1 = drive.CreateFile()
    # Fetch revisions.
    revisions = file1.GetRevisions()
    print(revisions)

Not all files objects have revisions. If GetRevisions is called on a
file object that does not have revisions, an exception will be raised.

Upload and update file content
------------------------------

Managing file content is as easy as managing file metadata. You can set file
content with either `SetContentFile(filename)`_ or `SetContentString(content)`_
and call `Upload()`_ just as you did to upload or update file metadata.

Sample code continues from `Download file metadata from file ID`_:

.. code-block:: python

    file4 = drive.CreateFile({'title':'appdata.json', 'mimeType':'application/json'})
    file4.SetContentString('{"firstname": "John", "lastname": "Smith"}')
    file4.Upload() # Upload file.
    file4.SetContentString('{"firstname": "Claudio", "lastname": "Afshar"}')
    file4.Upload() # Update content of the file.

    file5 = drive.CreateFile()
    # Read file and set it as a content of this instance.
    file5.SetContentFile('cat.png')
    file5.Upload() # Upload the file.
    print('title: %s, mimeType: %s' % (file5['title'], file5['mimeType']))
    # title: cat.png, mimeType: image/png

**Advanced Users:** If you call SetContentFile and GetContentFile you can can
define which character encoding is to be used by using the optional
parameter `encoding`.

If you, for example, are retrieving a file which is stored on your Google
Drive which is encoded with ISO-8859-1, then you can get the content string
like so:

.. code-block:: python

    content_string = file4.GetContentString(encoding='ISO-8859-1')

Upload data as bytes in memory buffer
--------------------------------------

Data can be kept as bytes in an in-memory buffer when we use the ``io`` module’s
Byte IO operations, we can upload files that reside in memory, for
example we have a base64 image, we can decode the string and upload it to drive
without the need to save as a file and use `SetContentFile(filename)`_

.. code-block:: python

    import io
    from pydrive2.drive import GoogleDrive

    # Create GoogleDrive instance with authenticated GoogleAuth instance.
    drive = GoogleDrive(gauth)
    # Define file name and type
    metadata = {
        'title': 'image_test',
        'mimeType': 'image/jpeg'
    }
    # Create file
    file = drive.CreateFile(metadata=metadata)
    # Buffered I/O implementation using an in-memory bytes buffer.
    image_file = io.BytesIO(image_bytes)
    # Set the content of the file
    file.content = image_file
    # Upload the file to google drive
    file.Upload()

Upload file to a specific folder
--------------------------------

In order to upload a file into a specific drive folder we need to pass the
``id`` of the folder in the metadata ``param`` from `CreateFile()`_.
Save the image from the previous example into a specific folder``:``

.. code-block:: python

    metadata = {
        'parents': [
            {"id": id_drive_folder}
        ],
        'title': 'image_test',
        'mimeType': 'image/jpeg'
    }
    # Create file
    file = drive.CreateFile(metadata=metadata)
    file.Upload()

Download file content
---------------------

Just as you uploaded file content, you can download it using
`GetContentFile(filename)`_ or `GetContentString()`_.

Sample code continues from above:

.. code-block:: python

    # Initialize GoogleDriveFile instance with file id.
    file6 = drive.CreateFile({'id': file5['id']})
    file6.GetContentFile('catlove.png') # Download file as 'catlove.png'.

    # Initialize GoogleDriveFile instance with file id.
    file7 = drive.CreateFile({'id': file4['id']})
    content = file7.GetContentString()
    # content: '{"firstname": "Claudio", "lastname": "Afshar"}'

    file7.SetContentString(content.replace('lastname', 'familyname'))
    file7.Upload()
    # Uploaded content: '{"firstname": "Claudio", "familyname": "Afshar"}'

**Advanced users**: Google Drive is `known`_ to add BOM (Byte Order Marks) to
the beginning of some files, such as Google Documents downloaded as text files.
In some cases confuses parsers and leads to corrupt files.
PyDrive can remove the BOM from the beginning of a file when it
is downloaded. Just set the `remove_bom` parameter in `GetContentString()` or
`GetContentFile()` - see `examples/strip_bom_example.py` in the GitHub
repository for an example.

Abusive files
-------------

Files identified as `abusive`_ (malware, etc.) are only downloadable by the owner.
If you see a
'This file has been identified as malware or spam and cannot be downloaded'
error, set 'acknowledge_abuse=True' parameter in `GetContentFile()`. By using
it you indicate that you acknowledge the risks of downloading potential malware.

.. _`GoogleDriveFile`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile
.. _`Upload()`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile.Upload
.. _`GoogleAuth`: /PyDrive2/pydrive2/#pydrive2.auth.GoogleAuth
.. _`CreateFile()`: /PyDrive2/pydrive2/#pydrive2.drive.GoogleDrive.CreateFile
.. _`Files resource`: https://developers.google.com/drive/v2/reference/files#resource-representations
.. _`SetContentFile(filename)`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile.SetContentFile
.. _`SetContentString(content)`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile.SetContentString
.. _`GetContentFile(filename)`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile.GetContentFile
.. _`GetContentString()`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile.GetContentString
.. _`official documentation`: https://developers.google.com/drive/v2/reference/files#resource-representations
.. _`known`: https://productforums.google.com/forum/#!topic/docs/BJLimQDGtjQ
.. _`abusive`: https://support.google.com/docs/answer/148505
.. _`query_parameters`: https://developers.google.com/drive/api/guides/search-files#examples
