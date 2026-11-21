Quickstart
==========

Authentication
--------------

Drive API requires OAuth2.0 for authentication. *PyDrive2* makes your life much
easier by handling complex authentication steps for you.

1. Go to `APIs Console`_ and make your own project.
2. Search for 'Google Drive API', select the entry, and click 'Enable'.
3. Select 'Credentials' from the left menu, click 'Create Credentials', select
   'OAuth client ID'.
4. Now, the product name and consent screen need to be set -> click 'Configure
   consent screen' and follow the instructions. Once finished:

   a. Select 'Application type' to be *Web application*.
   b. Enter an appropriate name.
   c. Input *http://localhost:8080/* for 'Authorized redirect URIs'.
   d. Click 'Create'.

5. Click 'Download JSON' on the right side of Client ID to download
   **client_secret_<really long ID>.json**.

The downloaded file has all authentication information of your application.
**Rename the file to "client_secrets.json" and place it in your working
directory.**

Create *quickstart.py* file and copy and paste the following code.

.. code-block:: python

    from pydrive2.auth import GoogleAuth

    gauth = GoogleAuth()
    # Creates local webserver and auto handles authentication.
    gauth.LocalWebserverAuth()

Run this code with *python quickstart.py* and you will see a web browser asking
you for authentication. Click *Accept* and you are done with authentication.
For more details, take a look at documentation: `OAuth made easy`_

.. _`APIs Console`: https://console.developers.google.com/iam-admin/projects
.. _`OAuth made easy`: /PyDrive2/oauth/

Creating and Updating Files
---------------------------

There are many methods to create and update file metadata and contents. With
*PyDrive2*, all you have to know is `Upload()`_ method which makes optimal API
call for you. Add the following code to your *quickstart.py* and run it.

.. code-block:: python

    from pydrive2.drive import GoogleDrive

    drive = GoogleDrive(gauth)

    # Create GoogleDriveFile instance with name 'Hello.txt'.
    file1 = drive.CreateFile({'name': 'Hello.txt'})
    # Set content of the file from given string.
    file1.SetContentString('Hello World!')
    file1.Upload()

This code will create a new file with name *Hello.txt* and its content *Hello
World!*. You can see and open this file from `Google Drive`_ if you want. For
more details, take a look at documentation: `File management made easy`_

.. _`Upload()`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile.Upload
.. _`Google Drive`: https://drive.google.com
.. _`File management made easy`: /PyDrive2/filemanagement/

Listing Files
-------------

*PyDrive2* handles paginations and parses response as list of
`GoogleDriveFile`_. Let's get name and id of all the files in the root folder
of Google Drive. Again, add the following code to *quickstart.py* and execute
it.

.. code-block:: python

    # Auto-iterate through all files that matches this query
    file_list = drive.ListFile(
        {'q': "'root' in parents and trashed=false"}
    ).GetList()
    for file1 in file_list:
        print('name: %s, id: %s' % (file1['name'], file1['id']))

Creating a Folder
-----------------

GoogleDrive treats everything as a file and assigns different mimetypes for
different file formats. A folder is thus also a file with a special mimetype.
The code below allows you to add a subfolder to an existing folder.

.. code-block:: python

    def create_folder(parent_folder_id, subfolder_name):
        newFolder = drive.CreateFile({
            'name': subfolder_name,
            "parents": [parent_folder_id],
            "mimeType": "application/vnd.google-apps.folder",
        })
        newFolder.Upload()
        return newFolder


Return File ID via File Name
-----------------------------

A common task is providing the Google Drive API with a file id.
``get_id_of_name`` demonstrates a simple workflow to return the id of a file
handle by searching the file names in a given directory. The function takes
two arguments, ``name`` and ``parent_directory_id``. ``name`` is a string
that will be compared against file names included in a directory identified by
the ``parent_directory_id``.

.. code-block:: python

    def get_id_of_name(name, parent_directory_id):
        foldered_list = drive.ListFile({
            'q': "'" + parent_directory_id + "' in parents and trashed=false"
        }).GetList()
        for file in foldered_list:
            if file['name'] == name:
                return file['id']
        return None

Browse Folders
--------------

This returns a json output of the data in a directory with some important
attributes like size, name, parent_id.

.. code-block:: python

    browsed = []

    def folder_browser(folder_list, parent_id):
        for element in folder_list:
            if type(element) is dict:
                print(element['name'])
            else:
                print(element)
        print(
            "Enter Name of Folder You Want to Use\n"
            "Enter '/' to use current folder\n"
            "Enter ':' to create New Folder and use that"
        )
        inp = input()
        if inp == '/':
            return parent_id
        elif inp == ':':
            print("Enter Name of Folder You Want to Create")
            inp = input()
            newfolder = create_folder(parent_id, inp)
            if not os.path.exists(
                HOME_DIRECTORY + ROOT_FOLDER_NAME + os.path.sep + USERNAME
            ):
                os.makedirs(
                    HOME_DIRECTORY + ROOT_FOLDER_NAME + os.path.sep + USERNAME
                )
            return newfolder['id']
        else:
            folder_selected = inp
            for element in folder_list:
                if type(element) is dict:
                    if element["name"] == folder_selected:
                        struc = element["list"]
                        browsed.append(folder_selected)
                        print("Inside " + folder_selected)
                        return folder_browser(struc, element['id'])

here ``folder_list`` is the list of folders that is present

You will see name and id of all the files and folders in root folder of your
Google Drive. For more details, refer to the documentation:
`File listing made easy`_

.. _`GoogleDriveFile`: /PyDrive2/pydrive2/#pydrive2.files.GoogleDriveFile
.. _`File listing made easy`: /PyDrive2/filelist/
