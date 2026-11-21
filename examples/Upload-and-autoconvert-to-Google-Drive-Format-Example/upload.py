# Original author: Evren Yurtesen - https://github.com/yurtesen/

"""
Uploads a file to a specific folder in Google Drive and converts it to a
Google Doc/Sheet/etc. if possible.

usage: upload.py <Google Drive folder ID> <local file path>
example usage: upload.py 0B5XXXXY9KddXXXXXXXA2c3ZXXXX /path/to/my/file
"""

import sys
from os import path

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.settings import LoadSettingsFile

# Update this value to the correct location.
#   e.g. "/usr/local/scripts/pydrive/settings.yaml"
PATH_TO_SETTINGS_FILE = None
assert PATH_TO_SETTINGS_FILE is not None  # Fail if path not specified.

gauth = GoogleAuth()
gauth.settings = LoadSettingsFile(filename=PATH_TO_SETTINGS_FILE)
gauth.CommandLineAuth()
drive = GoogleDrive(gauth)

# If provided arguments incorrect, print usage instructions and exit.
if len(sys.argv) < 3:
    print("usage: upload.py <Google Drive folder ID> <local file path>")
    sys.exit(1)

parentId = sys.argv[1]
myFilePath = sys.argv[2]
myFileName = path.basename(sys.argv[2])

# Check if file name already exists in folder.
file_list = drive.ListFile(
    {
        "q": f'"{parentId}" in parents and title="{myFileName}" and trashed=false'
    }
).GetList()

# If file is found, update it, otherwise create new file.
if len(file_list) == 1:
    myFile = file_list[0]
else:
    myFile = drive.CreateFile(
        {"parents": [{"kind": "drive#fileLink", "id": parentId}]}
    )

# Upload new file content.
myFile.SetContentFile(myFilePath)
myFile["title"] = myFileName
# The `convert` flag indicates to Google Drive whether to convert the
# uploaded file into a Google Drive native format, i.e. Google Sheet for
# CSV or Google Document for DOCX.
myFile.Upload({"convert": True})
