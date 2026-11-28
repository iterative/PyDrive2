# Original author: Evren Yurtesen - https://github.com/yurtesen/

"""
Uploads a file to a specific folder in Google Drive and converts it to a
Google Doc/Sheet/etc. if possible.

usage: upload.py <Google Drive folder ID> <local file path>
example usage: upload.py 0B5XXXXY9KddXXXXXXXA2c3ZXXXX /path/to/my/file
"""

import mimetypes
import sys
from pathlib import Path

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

if len(sys.argv) != 3:
    sys.exit("usage: upload.py <Google Drive folder ID> <local file path>")

parentId = sys.argv[1]
myFilePath = sys.argv[2]
myFileName = Path(myFilePath).name

gauth = GoogleAuth(settings_file=Path(__file__).with_name("settings.yaml"))
gauth.CommandLineAuth()
drive = GoogleDrive(gauth)

# Drive query string literals escape backslashes and single quotes.
escaped_name = myFileName.replace("\\", "\\\\").replace("'", "\\'")
file_list = drive.ListFile(
    {
        "q": f"'{parentId}' in parents and name='{escaped_name}' and trashed=false"
    }
).GetList()

# Drive name queries can include names that differ only in case.
file_list = [item for item in file_list if item["name"] == myFileName]
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
