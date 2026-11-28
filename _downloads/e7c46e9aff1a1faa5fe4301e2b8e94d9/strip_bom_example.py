from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

# Authenticate the client.
gauth = GoogleAuth()
gauth.LocalWebserverAuth()
drive = GoogleDrive(gauth)

# Create a file, set content, and upload.
file1 = drive.CreateFile()
original_file_content = "Generic, non-exhaustive\n ASCII test string."
file1.SetContentString(original_file_content)
file1["mimeType"] = "application/vnd.google-apps.document"
file1.Upload(media_mime_type="text/plain")


# Download the file.
file2 = drive.CreateFile({"id": file1["id"]})

# Print content before download.
print("Original text:")
print(repr(original_file_content))
print(f"Number of chars: {len(original_file_content)}")
print()

# Download document as text file WITH the BOM and print the contents.
content_with_bom = file2.GetContentString(mimetype="text/plain")
print("Content with BOM:")
print(repr(content_with_bom))
print(f"Number of chars: {len(content_with_bom)}")
print()

# Download document as text file WITHOUT the BOM and print the contents.
content_without_bom = file2.GetContentString(
    mimetype="text/plain", remove_bom=True
)
print("Content without BOM:")
print(repr(content_without_bom))
print(f"Number of chars: {len(content_without_bom)}")

# Remove the document created for this example.
file1.Delete()
