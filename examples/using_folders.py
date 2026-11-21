from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

gauth = GoogleAuth()
gauth.LocalWebserverAuth()

drive = GoogleDrive(gauth)

# Create folder.
folder_metadata = {
    "name": "<your folder name here>",
    # The mimetype defines this new file as a folder, so don't change this.
    "mimeType": "application/vnd.google-apps.folder",
}
folder = drive.CreateFile(folder_metadata)
folder.Upload()

# Get folder info and print to screen.
folder_name = folder["name"]
folder_id = folder["id"]
print(f"name: {folder_name}, id: {folder_id}")

# Upload file to folder.
f = drive.CreateFile({"parents": [folder_id]})

# Make sure to add the path to the file to upload below.
f.SetContentFile("<file path here>")
f.Upload()
