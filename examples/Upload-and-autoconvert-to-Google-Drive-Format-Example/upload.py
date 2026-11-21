# Original author: Evren Yurtesen - https://github.com/yurtesen/

"""
Uploads a file to a specific folder in Google Drive and converts it to a
Google Doc/Sheet/etc. if possible.

usage: upload.py <Google Drive folder ID> <local file path>
example usage: upload.py 0B5XXXXY9KddXXXXXXXA2c3ZXXXX /path/to/my/file
"""

import mimetypes
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

# Drive query string literals escape backslashes and single quotes.
escaped_name = myFileName.replace("\\", "\\\\").replace("'", "\\'")
file_list = drive.ListFile(
    {
        "q": f"'{parentId}' in parents and name='{escaped_name}' and trashed=false"
    }
).GetList()

# If file is found, update it, otherwise create new file.
if len(file_list) > 1:
    sys.exit("Multiple files match. Choose a unique destination name.")
if file_list:
    myFile = file_list[0]
else:
    myFile = drive.CreateFile({"parents": [parentId]})

# Upload new file content.
myFile.SetContentFile(myFilePath)
myFile["name"] = myFileName
source_type = mimetypes.guess_type(myFilePath)[0] or "application/octet-stream"
if not file_list:
    targets = drive.GetAbout(fields="importFormats")["importFormats"].get(
        source_type, []
    )
    if targets:
        myFile["mimeType"] = targets[0]
myFile.Upload(media_mime_type=source_type)
