Quickstart
==========

Authentication
--------------

1. Enable the Google Drive API in your Google Cloud project and configure
   the OAuth consent screen. If the app is in testing, add the account
   you will use as a test user.
2. Create an OAuth client ID with application type **Desktop app** and
   download its JSON configuration. Google's `Drive quickstart`_ walks
   through the console setup.
3. Save the downloaded file as ``client_secrets.json`` in your working
   directory. Run the following Python code and sign in when the browser
   opens. PyDrive2 receives the authorization code on a local callback.

.. code-block:: python

    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive

    gauth = GoogleAuth()
    gauth.LocalWebserverAuth()
    drive = GoogleDrive(gauth)

The examples below use this ``drive`` object. To save credentials across
runs or use a service account, see :doc:`oauth`.

Creating and Updating Files
---------------------------

Create a local file object, set its content, and call
:meth:`~pydrive2.files.GoogleDriveFile.Upload` to create the file on Drive.
Call the same method to save later changes to the file.

.. code-block:: python

    note = drive.CreateFile({"name": "Hello.txt"})
    note.SetContentString("Hello World!")
    note.Upload()
    print(note["id"])

    note["name"] = "Greetings.txt"
    note.Upload()

Listing Files
-------------

List the entries in My Drive's root folder, excluding trashed items.
:meth:`~pydrive2.apiattr.ApiResourceList.GetList` collects all matching
pages when the query omits ``pageSize``.

.. code-block:: python

    entries = drive.ListFile({"q": "'root' in parents and trashed=false"}).GetList()
    for entry in entries:
        print(entry["name"], entry["id"])

Creating a Folder
-----------------

A folder has the Drive folder MIME type. Set ``parents`` to a list
containing the destination folder ID. Use ``root`` for My Drive.

.. code-block:: python

    folder = drive.CreateFile({
        "name": "Reports",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": ["root"],
    })
    folder.Upload()

    note["parents"] = [folder["id"]]
    note.Upload()

Return File ID via File Name
----------------------------

Drive allows multiple entries with the same name. To find every matching
ID, list the parent folder and compare names.

.. code-block:: python

    def ids_for_name(parent_id, name):
        query = {"q": f"'{parent_id}' in parents and trashed=false"}
        return [
            item["id"] for item in drive.ListFile(query).GetList()
            if item["name"] == name
        ]

    print(ids_for_name(folder["id"], "Greetings.txt"))

Browse Folders
--------------

To browse a folder one page at a time, use its ID in a parent query. A
folder entry's ID can be used as the parent for the next listing.

.. code-block:: python

    parent_id = folder["id"]
    for page in drive.ListFile({"q": f"'{parent_id}' in parents and trashed=false"}):
        for item in page:
            print(item["id"], item["name"], item["mimeType"])

See :doc:`filemanagement` for downloads and permissions, and :doc:`filelist`
for pagination and field selection.

.. _Drive quickstart: https://developers.google.com/workspace/drive/api/quickstart/python
