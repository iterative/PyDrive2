import filecmp
import json
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from tempfile import mkdtemp
from time import time
from unittest.mock import MagicMock

import httplib2
import pytest
import timeout_decorator
from googleapiclient import errors

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import ApiRequestError
from pydrive2.test import test_util
from pydrive2.test.test_util import (
    create_file,
    delete_dir,
    delete_file,
    pydrive_retry,
    settings_file_path,
    setup_credentials,
)


def auth_with_resource_key_mock() -> GoogleAuth:
    """
    Create GoogleAuth with mocked inner httplib2.Http simulating need
    for resourceKey header.
    """
    http_mock = MagicMock()

    def resource_key_request(
        uri,
        method="GET",
        body=None,
        headers=None,
        redirections=1,
        connection_type=None,
    ):
        """httplib2.Http.request mock."""
        # If "media" is not requested, it means we expect metadata
        fetch_meta_data = "alt=media" not in uri
        if (
            headers
            and "X-Goog-Drive-Resource-Keys" in headers
            and headers["X-Goog-Drive-Resource-Keys"]
            == "0BxphPoRgwhnodHNjS3JESnFNS1E/0-vjzOveuin3fnf4LUlfsD3A"
        ):
            if fetch_meta_data:
                # Fake meta data query response
                content = json.dumps({"name": "N48E012.zip"}).encode()
            else:
                # Fake file content query response
                content = b"some content"
            return (
                httplib2.Response(
                    {"status": "200", "content-length": str(len(content))}
                ),
                content,
            )
        # Simulate 404 response for file not found; body must be valid error JSON
        return (
            httplib2.Response({"status": "404"}),
            json.dumps({"error": {"code": 404}}).encode(),
        )

    http_mock.request.side_effect = resource_key_request
    ga = GoogleAuth(
        settings_file_path(
            "default.yaml", os.path.join(os.path.dirname(__file__), "")
        )
    )
    ga.thread_local.http = http_mock
    ga.ServiceAuth()
    return ga


@pytest.mark.drive
class GoogleDriveFileTest(unittest.TestCase):
    """Tests basic file operations of files.GoogleDriveFile.
    Upload and download of contents and metadata, and thread-safety checks.
    Equivalent to Files.create and Files.update in Google Drive API.
    """

    @classmethod
    def setup_class(cls):
        setup_credentials()

        cls.tmpdir = mkdtemp()

        cls.ga = GoogleAuth(
            settings_file_path("default.yaml", os.path.join(cls.tmpdir, ""))
        )
        cls.ga.ServiceAuth()

    @classmethod
    def tearDownClass(cls):
        delete_dir(cls.tmpdir)

    @classmethod
    def getTempFile(cls, prefix="", content=""):
        filename = os.path.join(cls.tmpdir, prefix + str(time()))
        if content:
            create_file(filename, content)
        return filename

    def test_Files_Insert(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("firsttestfile")
        file1["name"] = filename
        pydrive_retry(file1.Upload)  # Files.create

        self.assertEqual(file1.metadata["name"], filename)
        file2 = drive.CreateFile({"id": file1["id"]})  # Download file from id.
        self.assertEqual(file2["name"], filename)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Insert_Unicode(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("첫번째 파일")
        file1["name"] = filename
        pydrive_retry(file1.Upload)  # Files.create

        self.assertEqual(file1.metadata["name"], filename)
        file2 = drive.CreateFile({"id": file1["id"]})  # Download file from id.
        self.assertEqual(file2["name"], filename)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Insert_Content_String(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("secondtestfile")
        content = "hello world!"
        file1["name"] = filename
        file1.SetContentString(content)
        pydrive_retry(file1.Upload)  # Files.create

        self.assertEqual(file1.GetContentString(), content)

        pydrive_retry(
            file1.FetchContent
        )  # Force download and double check content
        self.assertEqual(file1.metadata["name"], filename)
        self.assertEqual(file1.GetContentString(), content)

        file2 = drive.CreateFile({"id": file1["id"]})  # Download file from id.
        pydrive_retry(file2.FetchContent)
        self.assertEqual(file2.GetContentString(), content)
        self.assertEqual(file2.metadata["name"], filename)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Insert_Content_Unicode_String(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("두번째 파일")
        content = "안녕 세상아!"
        file1["name"] = filename
        file1.SetContentString(content)
        pydrive_retry(file1.Upload)  # Files.create

        self.assertEqual(file1.GetContentString(), content)
        self.assertEqual(file1.metadata["name"], filename)
        pydrive_retry(
            file1.FetchContent
        )  # Force download and double check content.
        self.assertEqual(file1.GetContentString(), content)

        file2 = drive.CreateFile({"id": file1["id"]})  # Download file from id.
        pydrive_retry(file2.FetchContent)
        self.assertEqual(file2.GetContentString(), content)
        self.assertEqual(file2.metadata["name"], filename)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Insert_Content_File(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        contentFile = self.getTempFile("actual_content", "some string")
        file1.SetContentFile(contentFile)
        pydrive_retry(file1.Upload)  # Files.create

        self.assertEqual(file1.metadata["name"], os.path.basename(contentFile))
        pydrive_retry(
            file1.FetchContent
        )  # Force download and double check content.
        fileOut = self.getTempFile()
        pydrive_retry(file1.GetContentFile, fileOut)
        self.assertEqual(filecmp.cmp(contentFile, fileOut), True)

        file2 = drive.CreateFile({"id": file1["id"]})  # Download file from id.
        fileOut = self.getTempFile()
        pydrive_retry(file2.GetContentFile, fileOut)
        self.assertEqual(filecmp.cmp(contentFile, fileOut), True)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Patch(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("prepatchtestfile")
        newfilename = self.getTempFile("patchtestfile")
        file1["name"] = filename
        pydrive_retry(file1.Upload)  # Files.create

        self.assertEqual(file1.metadata["name"], filename)
        file1["name"] = newfilename
        pydrive_retry(file1.Upload)  # Files.update

        self.assertEqual(file1.metadata["name"], newfilename)
        file2 = drive.CreateFile({"id": file1["id"]})  # Download file from id.
        pydrive_retry(file2.FetchMetadata)
        self.assertEqual(file2.metadata["name"], newfilename)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Patch_By_Id(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("prepatchtestfile")
        newfilename = self.getTempFile("patchtestfile")
        file1["name"] = filename
        pydrive_retry(file1.Upload)  # Files.create

        file2 = drive.CreateFile(
            {"id": file1["id"]}
        )  # Patch file no download.
        file2["name"] = newfilename
        pydrive_retry(file2.Upload)  # Files.update

        file3 = drive.CreateFile({"id": file1["id"]})  # Download file from id.
        pydrive_retry(file3.FetchMetadata)
        self.assertEqual(file3.metadata["name"], newfilename)

    def test_Files_Patch_Skipping_Content(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("prepatchtestfile")
        newfilename = self.getTempFile("patchtestfile")
        content = "hello world!"

        file1["name"] = filename
        file1.SetContentString(content)
        pydrive_retry(file1.Upload)  # Files.create
        self.assertEqual(file1.metadata["name"], filename)

        file1["name"] = newfilename
        pydrive_retry(file1.Upload)  # Files.update
        self.assertEqual(file1.metadata["name"], newfilename)
        self.assertEqual(file1.GetContentString(), content)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Update_String(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("preupdatetestfile")
        newfilename = self.getTempFile("updatetestfile")
        content = "hello world!"
        newcontent = "hello new world!"

        file1["name"] = filename
        file1.SetContentString(content)
        pydrive_retry(file1.Upload)  # Files.create
        self.assertEqual(file1.metadata["name"], filename)
        self.assertEqual(file1.GetContentString(), content)

        pydrive_retry(
            file1.FetchContent
        )  # Force download and double check content.
        self.assertEqual(file1.GetContentString(), content)

        file1["name"] = newfilename
        file1.SetContentString(newcontent)
        pydrive_retry(file1.Upload)  # Files.update
        self.assertEqual(file1.metadata["name"], newfilename)
        self.assertEqual(file1.GetContentString(), newcontent)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Update_File(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("preupdatetestfile")
        newfilename = self.getTempFile("updatetestfile")
        contentFile = self.getTempFile("actual_content", "some string")
        contentFile2 = self.getTempFile("actual_content_2", "some string")

        file1["name"] = filename
        file1.SetContentFile(contentFile)
        pydrive_retry(file1.Upload)  # Files.create
        self.assertEqual(file1.metadata["name"], filename)

        pydrive_retry(
            file1.FetchContent
        )  # Force download and double check content.
        fileOut = self.getTempFile()
        pydrive_retry(file1.GetContentFile, fileOut)
        self.assertEqual(filecmp.cmp(contentFile, fileOut), True)

        file1["name"] = newfilename
        file1.SetContentFile(contentFile2)
        pydrive_retry(file1.Upload)  # Files.update
        self.assertEqual(file1.metadata["name"], newfilename)

        fileOut = self.getTempFile()
        pydrive_retry(file1.GetContentFile, fileOut)
        self.assertEqual(filecmp.cmp(contentFile2, fileOut), True)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Update_File_Keeps_Filename(self):
        # Tests https://github.com/iterative/PyDrive2/issues/272
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("preupdatetestfile")
        contentFile = self.getTempFile("actual_content", "some string")
        contentFile2 = self.getTempFile("actual_content_2", "some string")

        file1["name"] = filename
        file1.SetContentFile(contentFile)
        pydrive_retry(file1.Upload)  # Files.create
        self.assertEqual(file1.metadata["name"], filename)

        same_file = drive.CreateFile({"id": file1["id"]})
        same_file.SetContentFile(contentFile2)
        pydrive_retry(same_file.Upload)  # Files.update
        self.assertEqual(same_file.metadata["name"], filename)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Update_By_Id(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("preupdatetestfile")
        newfilename = self.getTempFile("updatetestfile")
        content = "hello world!"
        newcontent = "hello new world!"

        file1["name"] = filename
        file1.SetContentString(content)
        pydrive_retry(file1.Upload)  # Files.create

        file2 = drive.CreateFile({"id": file1["id"]})
        file2["name"] = newfilename
        file2.SetContentString(newcontent)
        pydrive_retry(file2.Upload)  # Files.update

        file3 = drive.CreateFile({"id": file1["id"]})
        pydrive_retry(file3.FetchContent)
        self.assertEqual(file3.GetContentString(), newcontent)
        self.assertEqual(file3["name"], newfilename)

    def test_Files_Download_Service(self):
        """
        Tests that a fresh GoogleDrive object can correctly authenticate
        and download from a file ID.
        """
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile("prepatchtestfile")
        content = "hello world!"

        file1["name"] = filename
        file1.SetContentString(content)
        pydrive_retry(file1.Upload)  # Files.create
        self.assertEqual(file1.metadata["name"], filename)
        fileOut1 = self.getTempFile()
        pydrive_retry(file1.GetContentFile, fileOut1)

        # fresh download-only instance
        auth = GoogleAuth(
            settings_file_path("default.yaml", os.path.join(self.tmpdir, ""))
        )
        auth.ServiceAuth()
        drive2 = GoogleDrive(auth)
        file2 = drive2.CreateFile({"id": file1["id"]})
        fileOut2 = self.getTempFile()
        pydrive_retry(file2.GetContentFile, fileOut2)
        self.assertEqual(filecmp.cmp(fileOut1, fileOut2), True)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Get_Content_Buffer(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        filename = self.getTempFile()
        content = "hello world!\ngoodbye, cruel world!"
        file1["name"] = filename
        file1.SetContentString(content)
        pydrive_retry(file1.Upload)  # Files.create

        buffer1 = pydrive_retry(file1.GetContentIOBuffer)
        self.assertEqual(file1.metadata["name"], filename)
        self.assertEqual(len(buffer1), len(content))
        self.assertEqual(b"".join(iter(buffer1)).decode("ascii"), content)

        buffer2 = pydrive_retry(file1.GetContentIOBuffer, encoding="ascii")
        self.assertEqual(len(buffer2), len(content))
        self.assertEqual("".join(iter(buffer2)), content)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Get_Content_Buffer_resourceKey_missing(self):
        """404 expected for file secured with resourceKey when not provided."""

        ga = auth_with_resource_key_mock()

        drive = GoogleDrive(ga)
        file1 = drive.CreateFile(
            {
                "id": "0BxphPoRgwhnodHNjS3JESnFNS1E",
            }
        )
        with self.assertRaisesRegex(
            ApiRequestError, "HttpError 404 when requesting"
        ):
            pydrive_retry(file1.GetContentIOBuffer)

    def test_Files_Get_Content_Buffer_resourceKey(self):
        """End to end scenario with real file."""
        ga = auth_with_resource_key_mock()
        drive = GoogleDrive(ga)
        file1 = drive.CreateFile(
            {
                "id": "0BxphPoRgwhnodHNjS3JESnFNS1E",
                "resourceKey": "0-vjzOveuin3fnf4LUlfsD3A",
            }
        )

        buffer1 = pydrive_retry(file1.GetContentIOBuffer)

        self.assertEqual(len(buffer1), 12)

    @pytest.mark.manual
    def test_Files_Get_Content_Buffer_resourceKey_missing_real(self):
        """
        404 expected for file secured with resourceKey when not provided.
        End to end scenario with real public file.
        """
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile(
            {
                "id": "0BxphPoRgwhnodHNjS3JESnFNS1E",
            }
        )
        with self.assertRaisesRegex(
            ApiRequestError, "HttpError 404 when requesting"
        ):
            pydrive_retry(file1.GetContentIOBuffer)

    @pytest.mark.manual
    def test_Files_Get_Content_Buffer_resourceKey_real(self):
        """End to end scenario with real public file."""
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile(
            {
                "id": "0BxphPoRgwhnodHNjS3JESnFNS1E",
                "resourceKey": "0-vjzOveuin3fnf4LUlfsD3A",
            }
        )

        buffer1 = pydrive_retry(file1.GetContentIOBuffer)

        self.assertEqual(len(buffer1), 6128902)

    def test_Files_Get_Content_File_resourceKey_missing(self):
        """404 expected for file secured with resourceKey when not provided."""
        ga = auth_with_resource_key_mock()
        drive = GoogleDrive(ga)
        file1 = drive.CreateFile(
            {
                "id": "0BxphPoRgwhnodHNjS3JESnFNS1E",
            }
        )
        fileOut = self.getTempFile()
        with self.assertRaisesRegex(
            ApiRequestError, "HttpError 404 when requesting"
        ):
            pydrive_retry(file1.GetContentFile, fileOut)

    def test_Files_Get_Content_File_resourceKey(self):
        ga = auth_with_resource_key_mock()
        drive = GoogleDrive(ga)
        file1 = drive.CreateFile(
            {
                "id": "0BxphPoRgwhnodHNjS3JESnFNS1E",
                "resourceKey": "0-vjzOveuin3fnf4LUlfsD3A",
            }
        )

        fileOut = self.getTempFile()
        pydrive_retry(file1.GetContentFile, fileOut)

        with open(fileOut, "rb") as f:
            self.assertEqual(len(f.read()), 12)

    def test_Files_Fetch_Metadata_resourceKey_missing(self):
        """404 expected for file secured with resourceKey when not provided."""
        ga = auth_with_resource_key_mock()
        drive = GoogleDrive(ga)
        file1 = drive.CreateFile(
            {
                "id": "0BxphPoRgwhnodHNjS3JESnFNS1E",
            }
        )
        with self.assertRaisesRegex(
            ApiRequestError, "HttpError 404 when requesting"
        ):
            pydrive_retry(file1.FetchMetadata)

    def test_Files_Fetch_Metadata_resourceKey(self):
        ga = auth_with_resource_key_mock()
        drive = GoogleDrive(ga)
        file1 = drive.CreateFile(
            {
                "id": "0BxphPoRgwhnodHNjS3JESnFNS1E",
                "resourceKey": "0-vjzOveuin3fnf4LUlfsD3A",
            }
        )

        pydrive_retry(file1.FetchMetadata)

        self.assertEqual(file1.metadata["name"], "N48E012.zip")

    def test_Upload_Download_Empty_File(self):
        filename = os.path.join(self.tmpdir, str(time()))
        create_file(filename, "")

        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        file1.SetContentFile(filename)
        pydrive_retry(file1.Upload)

        fileOut1 = self.getTempFile()
        pydrive_retry(file1.GetContentFile, fileOut1)
        self.assertEqual(os.path.getsize(fileOut1), 0)

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Upload_Download_Empty_String(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        file1.SetContentString("")
        pydrive_retry(file1.Upload)

        self.assertEqual(pydrive_retry(file1.GetContentString), "")

        # Force download and double check content
        pydrive_retry(file1.FetchContent)
        self.assertEqual(file1.GetContentString(), "")

        # Download file from id
        file2 = drive.CreateFile({"id": file1["id"]})
        pydrive_retry(file2.FetchContent)
        self.assertEqual(file2.GetContentString(), "")

        self.DeleteUploadedFiles(drive, [file1["id"]])

    # Tests for Trash/UnTrash/Delete.
    # ===============================

    def test_Files_Trash_File(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)
        self.assertFalse(file1.metadata["trashed"])

        # Download to verify non-trashed state on GDrive.
        file2 = drive.CreateFile({"id": file1["id"]})
        pydrive_retry(file2.FetchMetadata)
        self.assertFalse(file2.metadata["trashed"])

        pydrive_retry(file1.Trash)
        self.assertTrue(file1.metadata["trashed"])

        pydrive_retry(file2.FetchMetadata)
        self.assertTrue(file2.metadata["trashed"])

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Trash_File_Just_ID(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)
        self.assertFalse(file1.metadata["trashed"])

        # Trash file by ID.
        file2 = drive.CreateFile({"id": file1["id"]})
        pydrive_retry(file2.Trash)

        # Verify trashed by downloading metadata.
        pydrive_retry(file1.FetchMetadata)
        self.assertTrue(file1.metadata["trashed"])

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_UnTrash_File(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)
        pydrive_retry(file1.Trash)
        self.assertTrue(file1.metadata["trashed"])

        # Verify that file is trashed by downloading metadata.
        file2 = drive.CreateFile({"id": file1["id"]})
        pydrive_retry(file2.FetchMetadata)
        self.assertTrue(file2.metadata["trashed"])

        # Un-trash the file, and assert local metadata is updated correctly.
        pydrive_retry(file1.UnTrash)
        self.assertFalse(file1.metadata["trashed"])

        # Re-fetch the metadata, and assert file un-trashed on GDrive.
        pydrive_retry(file2.FetchMetadata)
        self.assertFalse(file2.metadata["trashed"])

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_UnTrash_File_Just_ID(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)
        pydrive_retry(file1.Trash)
        self.assertTrue(file1.metadata["trashed"])

        file2 = drive.CreateFile({"id": file1["id"]})
        pydrive_retry(file2.UnTrash)  # UnTrash without fetching metadata.

        pydrive_retry(file1.FetchMetadata)
        self.assertFalse(file1.metadata["trashed"])

        self.DeleteUploadedFiles(drive, [file1["id"]])

    def test_Files_Delete_File(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)
        file2 = drive.CreateFile({"id": file1["id"]})

        pydrive_retry(file1.Delete)

        try:
            pydrive_retry(file2.FetchMetadata)
            self.fail("File not deleted correctly.")
        except ApiRequestError:
            pass

    def test_Files_Delete_File_Just_ID(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)
        file2 = drive.CreateFile({"id": file1["id"]})

        pydrive_retry(file2.Delete)

        try:
            pydrive_retry(file1.FetchMetadata)
            self.fail("File not deleted correctly.")
        except ApiRequestError:
            pass

    # Tests for Permissions.
    # ======================

    def test_Files_FetchMetadata_Fields(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)

        self.assertFalse("permissions" in file1)

        pydrive_retry(file1.FetchMetadata, "permissions")
        self.assertTrue("permissions" in file1)
        pydrive_retry(file1.Delete)

    def test_Files_FetchAllMetadata_Fields(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)

        pydrive_retry(file1.FetchMetadata, fetch_all=True)
        self.assertTrue("hasThumbnail" in file1)
        self.assertTrue("thumbnailVersion" in file1)
        self.assertTrue("permissions" in file1)
        pydrive_retry(file1.Delete)

    def test_Files_Insert_Permission(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)

        # Verify only one permission before inserting permission.
        permissions = pydrive_retry(file1.GetPermissions)
        self.assertEqual(len(permissions), 1)
        self.assertEqual(len(file1["permissions"]), 1)

        # Insert the permission.
        permission = pydrive_retry(
            file1.InsertPermission,
            {"type": "anyone", "role": "reader", "allowFileDiscovery": False},
        )
        self.assertTrue(permission)
        self.assertEqual(len(file1["permissions"]), 2)
        self.assertIn("anyone", [p["type"] for p in file1["permissions"]])

        permissions = pydrive_retry(file1.GetPermissions)
        self.assertEqual(len(file1["permissions"]), 2)
        self.assertIn("anyone", [p["type"] for p in file1["permissions"]])
        self.assertIn("anyone", [p["type"] for p in permissions])

        # Verify remote changes made.
        file2 = drive.CreateFile({"id": file1["id"]})
        permissions = pydrive_retry(file2.GetPermissions)
        self.assertEqual(len(permissions), 2)
        self.assertIn("anyone", [p["type"] for p in permissions])

        pydrive_retry(file1.Delete)

    def test_Files_Get_Permissions(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)

        self.assertFalse("permissions" in file1)

        permissions = pydrive_retry(file1.GetPermissions)
        self.assertTrue(permissions is not None)
        self.assertTrue("permissions" in file1)

        pydrive_retry(file1.Delete)

    def test_Files_Delete_Permission(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)
        pydrive_retry(
            file1.InsertPermission,
            {"type": "anyone", "role": "reader", "allowFileDiscovery": False},
        )
        permissions = pydrive_retry(file1.GetPermissions)
        self.assertEqual(len(permissions), 2)
        self.assertEqual(len(file1["permissions"]), 2)

        public_id = next(p["id"] for p in permissions if p["type"] == "anyone")
        pydrive_retry(file1.DeletePermission, public_id)
        self.assertEqual(len(file1["permissions"]), 1)

        # Verify remote changes made.
        file2 = drive.CreateFile({"id": file1["id"]})
        permissions = pydrive_retry(file2.GetPermissions)
        self.assertEqual(len(permissions), 1)

        pydrive_retry(file1.Delete)

    def test_Files_Delete_Permission_Invalid(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)

        try:
            pydrive_retry(file1.DeletePermission, "invalid id")
            self.fail("Deleting invalid permission not raising exception.")
        except ApiRequestError:
            pass

        pydrive_retry(file1.Delete)

    def test_Files_Get_Revisions(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()
        pydrive_retry(file1.Upload)

        self.assertFalse("revisions" in file1)

        revisions = pydrive_retry(file1.GetRevisions)
        self.assertTrue(revisions is not None)
        self.assertTrue("revisions" in file1)

        pydrive_retry(file1.Delete)

    def test_ApiRequestError_HttpError_Propagation(self):
        file = GoogleDrive(self.ga).CreateFile()
        pydrive_retry(file.Upload)
        try:
            pydrive_retry(file.DeletePermission, "invalid id")
            self.fail("Deleting invalid permission not raising exception.")
        except ApiRequestError as exc:
            self.assertTrue(
                exc.args and isinstance(exc.args[0], errors.HttpError)
            )
            self.assertTrue(exc.error is not None)
            # Validating for HttpError 404 "Permission not found: invalid id"
            self.assertTrue(exc.error["code"] == 404)
        finally:
            pydrive_retry(file.Delete)

    def test_GFile_Conversion_Lossless_String(self):
        drive = GoogleDrive(self.ga)
        file1 = drive.CreateFile()

        # Upload a string, and convert into Google Doc format.
        test_string = "Generic, non-exhaustive ASCII test string."
        file1.SetContentString(test_string)
        file1["mimeType"] = "application/vnd.google-apps.document"
        pydrive_retry(file1.Upload, media_mime_type="text/plain")

        self.addCleanup(pydrive_retry, file1.Delete)

        # A plain-text Google Docs export includes a UTF-8 BOM.
        downloaded_string = file1.GetContentString(
            mimetype="text/plain", remove_bom=True
        )
        self.assertEqual(
            test_string, downloaded_string, "Strings do not match"
        )

        # Download content into file and ensure that file content matches original
        # content string.
        downloaded_file_name = self.getTempFile()
        self.addCleanup(Path(downloaded_file_name).unlink, missing_ok=True)
        pydrive_retry(
            file1.GetContentFile,
            downloaded_file_name,
            mimetype="text/plain",
            remove_bom=True,
        )
        downloaded_string = Path(downloaded_file_name).read_text()
        self.assertEqual(
            test_string, downloaded_string, "Strings do not match"
        )

    # Tests for GDrive conversion.
    # ============================

    def upload_test_document(self):
        content = "Generic, non-exhaustive\n ASCII test string."
        source = self.getTempFile("source", content)
        self.addCleanup(Path(source).unlink, missing_ok=True)
        file = GoogleDrive(self.ga).CreateFile(
            {"mimeType": "application/vnd.google-apps.document"}
        )
        file.SetContentFile(source)
        with file.content:
            pydrive_retry(file.Upload, media_mime_type="text/plain")
        self.addCleanup(pydrive_retry, file.Delete)
        return file, test_util.StripNewlines(content)

    def test_GFile_Conversion_Remove_BOM(self):
        file1, original_file_content = self.upload_test_document()
        downloaded_file_name = self.getTempFile("export")
        self.addCleanup(Path(downloaded_file_name).unlink, missing_ok=True)
        # Download as string.
        downloaded_content_no_bom = file1.GetContentString(
            mimetype="text/plain", remove_bom=True
        )
        downloaded_content_no_bom = test_util.StripNewlines(
            downloaded_content_no_bom
        )
        self.assertEqual(original_file_content, downloaded_content_no_bom)

        # Download as file.
        pydrive_retry(
            file1.GetContentFile, downloaded_file_name, remove_bom=True
        )
        downloaded_content = Path(downloaded_file_name).read_text()
        downloaded_content = test_util.StripNewlines(downloaded_content)
        self.assertEqual(original_file_content, downloaded_content)

    def test_Gfile_Conversion_Add_Remove_BOM(self):
        """Switch between the exported text with and without the BOM."""
        file1, _ = self.upload_test_document()
        content_bom = file1.GetContentString(mimetype="text/plain")
        content_no_bom = file1.GetContentString(
            mimetype="text/plain", remove_bom=True
        )
        content_bom_2 = file1.GetContentString(mimetype="text/plain")

        self.assertEqual(content_bom, content_bom_2)
        self.assertNotEqual(content_bom, content_no_bom)
        self.assertTrue(len(content_bom) > len(content_no_bom))

        buffer_bom = pydrive_retry(
            file1.GetContentIOBuffer,
            mimetype="text/plain",
            encoding="utf-8",
        )
        buffer_bom = "".join(iter(buffer_bom))
        self.assertEqual(content_bom, buffer_bom)

        buffer_no_bom = pydrive_retry(
            file1.GetContentIOBuffer,
            mimetype="text/plain",
            remove_bom=True,
            encoding="utf-8",
        )
        buffer_no_bom = "".join(iter(buffer_no_bom))
        self.assertEqual(content_no_bom, buffer_no_bom)

    # Setup for concurrent upload testing.
    # =====================================
    FILE_UPLOAD_COUNT = 10

    def _parallel_uploader(self, num_of_uploads, num_of_workers):
        """
        :returns: list[str] of file IDs
        """
        drive = GoogleDrive(self.ga)
        thread_pool = ThreadPoolExecutor(max_workers=num_of_workers)
        first_file = self.getTempFile("first_file", "some string")
        second_file = self.getTempFile("second_file", "another string")

        # Create list of gdrive_files.
        upload_files = []
        remote_name = test_util.CreateRandomFileName()
        for i in range(num_of_uploads):
            file_name = first_file if i % 2 == 0 else second_file
            up_file = drive.CreateFile()
            up_file["name"] = remote_name
            up_file.SetContentFile(file_name)
            upload_files.append(up_file)

        # Ensure there are no files with the random file name.
        files = pydrive_retry(
            lambda: drive.ListFile(
                param={"q": f"name = '{remote_name}' and trashed = false"}
            ).GetList()
        )
        self.assertTrue(len(files) == 0)

        # Submit upload jobs to ThreadPoolExecutor.
        futures = []
        for up_file in upload_files:
            futures.append(thread_pool.submit(pydrive_retry, up_file.Upload))

        # Ensure that all threads a) return, and b) encountered no exceptions.
        for future in as_completed(futures):
            self.assertIsNone(future.exception())
        thread_pool.shutdown()

        file_ids = [file["id"] for file in upload_files]
        self.assertEqual(len(set(file_ids)), num_of_uploads)

        def check_uploaded_files():
            # A successful upload can take time to appear in title searches.
            files = drive.ListFile(
                param={"q": f"name = '{remote_name}' and trashed = false"}
            ).GetList()
            if len(files) < num_of_uploads:
                raise test_util.PyDriveRetriableError(
                    f"Drive lists {len(files)} of {num_of_uploads} uploads"
                )
            self.assertCountEqual([file["id"] for file in files], file_ids)

        pydrive_retry(check_uploaded_files)
        return file_ids

    def _parallel_downloader(self, file_ids, num_of_workers):
        drive = GoogleDrive(self.ga)
        thread_pool = ThreadPoolExecutor(max_workers=num_of_workers)

        # Create list of gdrive_files.
        download_files = []
        for file_id in file_ids:
            file1 = drive.CreateFile({"id": file_id})
            file1["name"] = self.getTempFile()
            download_files.append(file1)

        # Ensure files don't exist yet.
        for file_obj in download_files:
            self.assertTrue(not delete_file(file_obj["name"]))

        # Submit upload jobs to ThreadPoolExecutor.
        futures = []
        for file_obj in download_files:
            futures.append(
                thread_pool.submit(
                    pydrive_retry, file_obj.GetContentFile, file_obj["name"]
                )
            )

        # Ensure that all threads a) return, and b) encountered no exceptions.
        for future in as_completed(futures):
            self.assertIsNone(future.exception())
        thread_pool.shutdown()

        # Ensure all files were downloaded.
        for file_obj in download_files:
            self.assertTrue(delete_file(file_obj["name"]))

        # Remove uploaded files.
        self.DeleteUploadedFiles(drive, file_ids)

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="timeout_decorator doesn't support Windows",
    )
    @timeout_decorator.timeout(320)
    def test_Parallel_Insert_File_Passed_HTTP(self):
        files = self._parallel_uploader(self.FILE_UPLOAD_COUNT, 10)
        self._parallel_downloader(files, 10)

    # Tests for Copy file.
    # ====================

    def test_CopyFileSameFolder(self):
        drive = GoogleDrive(self.ga)

        content = "hello world!"

        # create a temp file and set it's content to a known string
        file1 = drive.CreateFile()
        filename = self.getTempFile("copytestfile", content=content)
        file1["name"] = filename
        file1.SetContentFile(filename)
        pydrive_retry(file1.Upload)

        # copy the file
        file2 = pydrive_retry(file1.Copy, new_title="copytestfile_copy")

        self.assertIsNotNone(file2)

        pydrive_retry(file2.FetchContent)
        # assert that the content of the copied file is the same as the original (file1)
        self.assertEqual(file2.GetContentString(), content)

        self.DeleteUploadedFiles(drive, [file1["id"], file2["id"]])

    def test_CopyFileDifferentFolder(self):
        drive = GoogleDrive(self.ga)

        content = "hello world!"

        # create a temp file and set it's content to a known string
        file1 = drive.CreateFile()
        filename = self.getTempFile("copytestfile", content=content)
        file1["name"] = filename
        file1.SetContentFile(filename)
        pydrive_retry(file1.Upload)

        # create a temp directory
        temp_dir = pydrive_retry(
            drive.CreateFile,
            {
                "name": "temp_dir",
                "mimeType": "application/vnd.google-apps.folder",
                "parents": [file1["parents"][0]],
            },
        )
        pydrive_retry(temp_dir.Upload)

        # copy the file into the new folder
        file2 = pydrive_retry(
            file1.Copy, target_folder=temp_dir, new_title="copytestfile_copy"
        )

        self.assertIsNotNone(file2)

        pydrive_retry(file2.FetchContent)
        # assert that the content of the copied file is the same as the original (file1)
        self.assertEqual(file2.GetContentString(), content)

        files = pydrive_retry(
            drive.ListFile, {"q": f"'{temp_dir['id']}' in parents"}
        ).GetList()
        self.assertIn("copytestfile_copy", [f["name"] for f in files])

        self.DeleteUploadedFiles(
            drive, [file1["id"], file2["id"], temp_dir["id"]]
        )

    # Helper functions.
    # =================

    def DeleteUploadedFiles(self, drive, ids):
        for element in ids:
            tmp_file = drive.CreateFile({"id": element})
            pydrive_retry(tmp_file.Delete)


if __name__ == "__main__":
    unittest.main()
