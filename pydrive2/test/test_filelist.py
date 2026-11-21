import os
import unittest

import pytest

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.test import test_util
from pydrive2.test.test_util import (
    pydrive_list_item,
    pydrive_retry,
    settings_file_path,
    setup_credentials,
)


@pytest.mark.drive
class GoogleDriveFileListTest(unittest.TestCase):
    """Tests operations of files.GoogleDriveFileList class.
    Equivalent to Files.list in Google Drive API.
    """

    @classmethod
    def setUpClass(cls):
        setup_credentials()

        cls.ga = GoogleAuth(settings_file_path("default.yaml"))
        cls.ga.ServiceAuth()
        cls.drive = GoogleDrive(cls.ga)
        cls.title = test_util.CreateRandomFileName()
        cls.file_list = []
        for _ in range(10):
            file = cls.drive.CreateFile({"title": cls.title})
            pydrive_retry(file.Upload)
            cls.addClassCleanup(pydrive_retry, file.Delete)
            cls.file_list.append(file)

    def test_01_Files_List_GetList(self):
        drive = GoogleDrive(self.ga)
        query = f"title = '{self.title}' and trashed = false"
        for file1 in pydrive_list_item(drive, query):
            found = False
            for file2 in pydrive_list_item(drive, query):
                if file1["id"] == file2["id"]:
                    found = True
            self.assertEqual(found, True)

    def test_02_Files_List_ForLoop(self):
        drive = GoogleDrive(self.ga)
        query = f"title = '{self.title}' and trashed = false"
        files = list(pydrive_list_item(drive, query, 2))
        for file1 in self.file_list:
            found = False
            for file2 in files:
                if file1["id"] == file2["id"]:
                    found = True
            self.assertEqual(found, True)

    def test_03_Files_List_GetList_Iterate(self):
        drive = GoogleDrive(self.ga)
        flist = drive.ListFile(
            {
                "q": f"title = '{self.title}' and trashed = false",
                "maxResults": 2,
            }
        )
        files = []
        while True:
            try:
                x = pydrive_retry(flist.GetList)
                self.assertTrue(len(x) <= 2)
                files.extend(x)
            except StopIteration:
                break
        for file1 in self.file_list:
            found = False
            for file2 in files:
                if file1["id"] == file2["id"]:
                    found = True
            self.assertEqual(found, True)

    def test_File_List_Folders(self):
        drive = GoogleDrive(self.ga)
        folder1 = drive.CreateFile(
            {
                "mimeType": "application/vnd.google-apps.folder",
                "title": self.title,
            }
        )
        pydrive_retry(folder1.Upload)
        self.addCleanup(pydrive_retry, folder1.Delete)
        query = f"title = '{self.title}' and trashed = false"
        self.assertCountEqual(
            [file["id"] for file in pydrive_list_item(drive, query)],
            [file["id"] for file in self.file_list] + [folder1["id"]],
        )

    def DeleteOldFile(self, file_name):
        try:
            os.remove(file_name)
        except OSError:
            pass


if __name__ == "__main__":
    unittest.main()
