File management made easy
=========================

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

    # Create GoogleDriveFile instance with name 'Hello.txt'.
    file1 = drive.CreateFile({'name': 'Hello.txt'})
    file1.SetContentString('Hello')
    file1.Upload()  # Create a text file.
    print('name: %s, id: %s' % (file1['name'], file1['id']))
    # name: Hello.txt, id: {{FILE_ID}}

:meth:`~pydrive2.drive.GoogleDrive.CreateFile` creates a local file object
with the drive's credentials. Call
:meth:`~pydrive2.files.GoogleDriveFile.Upload` to create ``Hello.txt`` on
Google Drive.

Delete, Trash and un-Trash files
--------------------------------

Use ``Trash()`` to move a file to the trash and ``UnTrash()`` to restore it.
``Delete()`` permanently deletes the file. This example creates a separate
file so you can continue working with ``Hello.txt`` in the following sections.

.. code-block:: python

    temporary = drive.CreateFile({"name": "temporary.txt"})
    temporary.Upload()
    temporary.Trash()
    temporary.UnTrash()
    temporary.Delete()

Update file metadata
--------------------

You can update the metadata on a :class:`~pydrive2.files.GoogleDriveFile`
as you would update a ``dict``.
The `Drive API v3 file resource`_ describes the metadata fields.

Sample code continues from `Upload a new file`_:

.. code-block:: python

    file1['name'] = 'HelloWorld.txt' # Change name of the file.
    file1.Upload() # Update metadata.
    print('name: %s' % file1['name']) # name: HelloWorld.txt.

Now, the name of your file has changed to 'HelloWorld.txt'.

Download file metadata from file ID
-----------------------------------

To get metadata for an existing file, pass its ID to
:meth:`~pydrive2.drive.GoogleDrive.CreateFile` and read the fields from the
returned file object.

Sample code continues from above:

.. code-block:: python

    # Create GoogleDriveFile instance with file id of file1.
    file2 = drive.CreateFile({'id': file1['id']})
    print('name: %s, mimeType: %s' % (file2['name'], file2['mimeType']))
    # name: HelloWorld.txt, mimeType: text/plain

Handling special metadata
-------------------------

Drive returns a subset of file fields by default. Request specific fields
with a field mask or use ``fetch_all=True`` to request all available fields.

.. code-block:: python

    file2.FetchMetadata(fields="id,name,owners,capabilities")
    file2.FetchMetadata(fetch_all=True)

See the `Drive API v3 file resource`_ for available fields.

Insert permissions
__________________

To share a file by link, create an ``anyone`` permission with the
``reader`` role. The file's sharing policy must allow link sharing.
Keep the returned permission ID if you want to revoke that grant later.

.. code-block:: python

    shared = drive.CreateFile({"name": "Shared note.txt"})
    shared.Upload()
    permission = shared.InsertPermission({
        "type": "anyone",
        "allowFileDiscovery": False,
        "role": "reader",
    })
    print(shared["webViewLink"])

See the `permissions.create request body`_ for other permission types.

List permissions
________________

Call ``GetPermissions()`` on an uploaded file or one opened by ID.
PyDrive2 follows the permission list's page tokens and stores the results
in the file object's ``permissions`` field.

.. code-block:: python

    for grant in shared.GetPermissions():
        print(grant["id"], grant["role"])

Remove a Permission
___________________

Revoke the grant created above using the ID returned by ``InsertPermission()``.

.. code-block:: python

    shared.DeletePermission(permission["id"])

Get files by complex queries
----------------------------

Multiple files can have the same name. Combine ``name`` with another
field such as ``mimeType`` in a Drive v3 search. See the
`query string examples`_ for additional filters.

.. code-block:: python

    from pydrive2.drive import GoogleDrive
    # Create GoogleDrive instance with authenticated GoogleAuth instance.
    drive = GoogleDrive(gauth)
    filename = 'file_test'
    mimetype = 'text/plain'
    # Query
    query = {'q': f"name = '{filename}' and mimeType='{mimetype}'"}
    # Get list of files that match against the query
    files = drive.ListFile(query).GetList()

List revisions
______________

Use ``GetRevisions()`` on a file that has been uploaded or opened by ID.

.. code-block:: python

    revisions = file1.GetRevisions()
    for revision in revisions:
        print(revision["id"])

.. _upgrading-from-drive-api-v2:

Import a file into Google Workspace
-----------------------------------

To import a CSV as a Google Sheet, set the destination MIME type on the
file and pass the source MIME type to ``Upload()``.

.. code-block:: python

    sheet = drive.CreateFile({
        "name": "Sales",
        "mimeType": "application/vnd.google-apps.spreadsheet",
    })
    sheet.SetContentFile("sales.csv")
    sheet.Upload(media_mime_type="text/csv")

For changes to fields and request parameters, see :doc:`upgrading`.

Upload and update file content
------------------------------

Set the content with :meth:`~pydrive2.files.GoogleDriveFile.SetContentFile`
or :meth:`~pydrive2.files.GoogleDriveFile.SetContentString`, then call
:meth:`~pydrive2.files.GoogleDriveFile.Upload`.

``SetContentFile()`` opens the local file. If ``Upload()`` fails, you can call
``Upload()`` again to retry with the same open file. PyDrive2 closes the file
when the upload succeeds or when you replace its content. If you abandon the
upload after an error, call ``file.content.close()`` to release it immediately.

Sample code continues from `Download file metadata from file ID`_:

.. code-block:: python

    file4 = drive.CreateFile({
        'name': 'appdata.json',
        'mimeType': 'application/json',
    })
    file4.SetContentString('{"firstname": "John", "lastname": "Smith"}')
    file4.Upload() # Upload file.
    file4.SetContentString('{"firstname": "Claudio", "lastname": "Afshar"}')
    file4.Upload() # Update content of the file.

    file5 = drive.CreateFile()
    # Read file and set it as a content of this instance.
    file5.SetContentFile('cat.png')
    file5.Upload() # Upload the file.
    print('name: %s, mimeType: %s' % (file5['name'], file5['mimeType']))
    # name: cat.png, mimeType: image/png

:meth:`~pydrive2.files.GoogleDriveFile.SetContentString` and
:meth:`~pydrive2.files.GoogleDriveFile.GetContentString` accept an optional
``encoding`` argument.

If you, for example, are retrieving a file which is stored on your Google
Drive which is encoded with ISO-8859-1, then you can get the content string
like so:

.. code-block:: python

    content_string = file4.GetContentString(encoding='ISO-8859-1')

Upload data as bytes in memory buffer
-------------------------------------

Assign a seekable binary stream to ``content``. The caller owns this
stream and can close it after the upload.

.. code-block:: python

    from io import BytesIO

    sample = drive.CreateFile({"name": "sample.bin"})
    with BytesIO(b"sample data") as stream:
        sample.content = stream
        sample.Upload()

Upload file to a specific folder
--------------------------------

Set ``parents`` to a list containing the destination folder ID. This
example uploads the contents of a local image into that folder.

.. code-block:: python

    image = drive.CreateFile({"parents": ["YOUR_FOLDER_ID"]})
    image.SetContentFile("cat.png")
    image.Upload()

Download file content
---------------------

Use :meth:`~pydrive2.files.GoogleDriveFile.GetContentFile` to download to
disk or :meth:`~pydrive2.files.GoogleDriveFile.GetContentString` to read
the content as text. Disk downloads open the destination after the first
successful read. If a later read fails, the file contains the chunks
already written.

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

If Drive flags a file as abusive, the owner or a shared-drive organizer
can explicitly acknowledge the risk when downloading it. Pass
``acknowledge_abuse=True`` to ``GetContentFile()`` for that download.
See the `files.get parameters`_ for the access requirements.

.. _files.get parameters: https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get

.. _Drive API v3 file resource:
   https://developers.google.com/workspace/drive/api/reference/rest/v3/files#resource:-file
.. _permissions.create request body:
   https://developers.google.com/workspace/drive/api/reference/rest/v3/permissions/create#request-body
.. _query string examples:
   https://developers.google.com/workspace/drive/api/guides/search-files#examples
.. _add a byte order mark to plain-text exports:
   https://github.com/googleapis/google-api-nodejs-client/issues/2404
