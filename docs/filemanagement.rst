File management made easy
=========================

There are many methods to create and update file metadata and contents.
With *PyDrive*, you don't have to care about any of these different API methods.
Edit the metadata or contents of a :class:`~pydrive2.files.GoogleDriveFile`
and call :meth:`~pydrive2.files.GoogleDriveFile.Upload` to save the changes.

Upload a new file
-----------------

In this example, ``gauth`` is an authenticated
:class:`~pydrive2.auth.GoogleAuth` instance.

.. code-block:: python

    from pydrive2.drive import GoogleDrive

    # Create GoogleDrive instance with authenticated GoogleAuth instance.
    drive = GoogleDrive(gauth)

    # Create GoogleDriveFile instance with title 'Hello.txt'.
    file1 = drive.CreateFile({'title': 'Hello.txt'})
    file1.Upload() # Upload the file.
    print('title: %s, id: %s' % (file1['title'], file1['id']))
    # title: Hello.txt, id: {{FILE_ID}}

:meth:`~pydrive2.drive.GoogleDrive.CreateFile` creates a local file object
with the drive's credentials. Call :meth:`~pydrive2.files.GoogleDriveFile.Upload`
to create ``Hello.txt`` on Google Drive.

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

You can update the metadata on a :class:`~pydrive2.files.GoogleDriveFile`
as you would update a ``dict``.
The `Drive API v2 file resource`_ describes the metadata fields.

Sample code continues from `Upload a new file`_:

.. code-block:: python

    file1['title'] = 'HelloWorld.txt' # Change title of the file.
    file1.Upload() # Update metadata.
    print('title: %s' % file1['title']) # title: HelloWorld.txt.

Now, the title of your file has changed to 'HelloWorld.txt'.

Download file metadata from file ID
-----------------------------------

To get metadata for an existing file, pass its ID to
:meth:`~pydrive2.drive.GoogleDrive.CreateFile` and read the fields from the
returned file object.

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

See the `Drive API v2 file resource`_ for the available fields.

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

See the `permissions.insert request body`_ for the permission format.
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

Multiple files can have the same title. To narrow the results, combine
``title`` with another field such as ``mimeType``. Google's
`query string examples`_ use Drive API v3 field names. When adapting them
for PyDrive2, use the corresponding v2 names. For example, the filename
field in the query below is ``title``.

.. code-block:: python

    from pydrive2.drive import GoogleDrive
    # Create GoogleDrive instance with authenticated GoogleAuth instance.
    drive = GoogleDrive(gauth)
    filename = 'file_test'
    mimetype = 'text/plain'
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

Set the content with :meth:`~pydrive2.files.GoogleDriveFile.SetContentFile`
or :meth:`~pydrive2.files.GoogleDriveFile.SetContentString`, then call
:meth:`~pydrive2.files.GoogleDriveFile.Upload`.

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

:meth:`~pydrive2.files.GoogleDriveFile.SetContentString` and
:meth:`~pydrive2.files.GoogleDriveFile.GetContentString` accept an optional
``encoding`` argument.

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
without first saving it to disk and calling
:meth:`~pydrive2.files.GoogleDriveFile.SetContentFile`.

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
``id`` of the folder in the metadata passed to
:meth:`~pydrive2.drive.GoogleDrive.CreateFile`.
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

Use :meth:`~pydrive2.files.GoogleDriveFile.GetContentFile` to download to
disk or :meth:`~pydrive2.files.GoogleDriveFile.GetContentString` to read
the content as text.

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

Google Drive can `add a byte order mark to plain-text exports`_ of Google
Docs. To read a Google Doc as text, request ``text/plain`` explicitly.
To save it to disk, :meth:`~pydrive2.files.GoogleDriveFile.GetContentFile`
defaults to a plain-text export. Both calls below strip the byte order mark.

.. code-block:: python

    doc = drive.CreateFile({'id': 'YOUR_GOOGLE_DOC_ID'})
    text = doc.GetContentString(mimetype='text/plain', remove_bom=True)
    doc.GetContentFile('document.txt', remove_bom=True)

See the :download:`complete Python example <../examples/strip_bom_example.py>`.

.. _abusive-files:

Abusive files
-------------

Files identified as `abusive`_ (malware, etc.) are only downloadable by the owner.
If you see a
'This file has been identified as malware or spam and cannot be downloaded'
error, set 'acknowledge_abuse=True' parameter in `GetContentFile()`. By using
it you indicate that you acknowledge the risks of downloading potential malware.

.. _Drive API v2 file resource:
   https://developers.google.com/workspace/drive/api/reference/rest/v2/files#resource:-file
.. _permissions.insert request body:
   https://developers.google.com/workspace/drive/api/reference/rest/v2/permissions/insert#request-body
.. _query string examples:
   https://developers.google.com/workspace/drive/api/guides/search-files#examples
.. _add a byte order mark to plain-text exports:
   https://github.com/googleapis/google-api-nodejs-client/issues/2404
.. _`abusive`: https://support.google.com/docs/answer/148505
