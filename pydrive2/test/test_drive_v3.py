import json
from urllib.parse import parse_qs, urlsplit

import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.http import HttpMockSequence

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import FileNotDownloadableError
from pydrive2.fs import GDriveFileSystem


def mock_drive(*responses):
    http = HttpMockSequence(list(responses))
    auth = GoogleAuth(settings_file=None)
    auth.credentials = Credentials("test-token")
    auth.Authorize()
    auth.thread_local.http = http
    return GoogleDrive(auth), http.request_sequence


def response(data):
    return {"status": "200"}, json.dumps(data)


@pytest.mark.parametrize("kind", ["folder", "shortcut"])
def test_get_content_of_non_downloadable_file(kind):
    drive, requests = mock_drive(
        response(
            {"id": "file", "mimeType": f"application/vnd.google-apps.{kind}"}
        )
    )
    with pytest.raises(FileNotDownloadableError):
        drive.CreateFile({"id": "file"}).GetContentString()
    assert len(requests) == 1


@pytest.mark.parametrize("root", ["root", "appDataFolder"])
def test_find_accepts_canonical_parent_of_root_alias(root):
    item = {
        "id": "child",
        "name": "note",
        "mimeType": "text/plain",
        "parents": ["canonical-root-id"],
        "size": "5",
    }
    drive, requests = mock_drive(response({"files": [item]}))
    fs = GDriveFileSystem(
        root, google_auth=drive.auth, skip_instance_cache=True
    )
    assert fs.find(root) == [f"{root}/note"]
    params = parse_qs(urlsplit(requests[0][0]).query)
    assert f"'{root}' in parents" in params["q"][0]
    if root == "appDataFolder":
        assert params["spaces"] == ["appDataFolder"]


def test_v3_list_and_about():
    drive, requests = mock_drive(
        response({"nextPageToken": "more", "files": []}),
        response({"files": [{"name": "second"}]}),
        response({"user": {"displayName": "Drive User"}}),
    )
    files = drive.ListFile({"fields": "files(name)"})
    assert [item["name"] for item in files.GetList()] == ["second"]
    assert (
        drive.GetAbout(fields="user/displayName")["user"]["displayName"]
        == "Drive User"
    )
    first, second, about = (urlsplit(request[0]) for request in requests)
    assert first.path == second.path == "/drive/v3/files"
    assert parse_qs(first.query)["fields"] == ["nextPageToken,files(name)"]
    assert parse_qs(second.query)["pageToken"] == ["more"]
    assert about.path == "/drive/v3/about"
    assert parse_qs(about.query)["fields"] == ["user/displayName"]


def test_v3_create_move_trash_and_copy():
    drive, requests = mock_drive(
        response({"id": "new", "parents": ["old"]}),
        response({"id": "new", "name": "renamed", "parents": ["new-parent"]}),
        response({"trashed": True}),
        response({"trashed": False}),
        response({"id": "copy"}),
    )
    file = drive.CreateFile({"name": "test", "parents": ["old"]})
    file.Upload()
    file["name"] = "renamed"
    file["parents"] = ["new-parent"]
    file.Upload()
    file.Trash()
    assert file["trashed"] is True
    file.UnTrash()
    assert file["trashed"] is False
    copy = file.Copy(target_folder={"id": "destination"}, new_title="second")
    assert copy.uploaded and copy["id"] == "copy"
    assert [r[1] for r in requests] == [
        "POST",
        "PATCH",
        "PATCH",
        "PATCH",
        "POST",
    ]
    assert json.loads(requests[0][2]) == {"name": "test", "parents": ["old"]}
    assert json.loads(requests[1][2]) == {"name": "renamed"}
    move = parse_qs(urlsplit(requests[1][0]).query)
    assert move["removeParents"] == ["old"] and move["addParents"] == [
        "new-parent"
    ]
    assert json.loads(requests[2][2]) == {"trashed": True}
    assert json.loads(requests[3][2]) == {"trashed": False}
    assert json.loads(requests[4][2]) == {
        "name": "second",
        "parents": ["destination"],
    }


def test_v3_workspace_import():
    target = "application/vnd.google-apps.spreadsheet"
    drive, requests = mock_drive(
        ({"status": "200", "location": "https://example.test/session"}, ""),
        response({"id": "sheet", "mimeType": target}),
    )
    file = drive.CreateFile({"name": "sales", "mimeType": target})
    file.SetContentString("year,total\n2026,10")
    file.Upload(media_mime_type="text/csv")
    create, media = requests
    assert urlsplit(create[0]).path == "/upload/drive/v3/files"
    assert json.loads(create[2])["mimeType"] == target
    assert {k.lower(): v for k, v in create[3].items()}[
        "x-upload-content-type"
    ] == "text/csv"
    assert media[1] == "PUT"
    assert file["id"] == "sheet" and file.content is None


def test_v3_permissions_and_revisions():
    owner = {"id": "owner", "role": "owner"}
    reader = {"id": "reader", "role": "reader", "type": "anyone"}
    drive, requests = mock_drive(
        response(reader),
        response({"permissions": [reader], "nextPageToken": "p2"}),
        response({"permissions": [owner]}),
        ({"status": "204"}, ""),
        response({"revisions": [{"id": "1"}], "nextPageToken": "r2"}),
        response({"revisions": [{"id": "2"}]}),
    )
    file = drive.CreateFile({"id": "file"})
    assert (
        file.InsertPermission({"type": "anyone", "role": "reader"}) == reader
    )
    assert file["permissions"] == [reader, owner]
    file.DeletePermission(reader["id"])
    assert file["permissions"] == [owner]
    assert file.GetRevisions() == [{"id": "1"}, {"id": "2"}]
    assert [urlsplit(r[0]).path for r in requests] == [
        "/drive/v3/files/file/permissions",
        "/drive/v3/files/file/permissions",
        "/drive/v3/files/file/permissions",
        "/drive/v3/files/file/permissions/reader",
        "/drive/v3/files/file/revisions",
        "/drive/v3/files/file/revisions",
    ]
    assert parse_qs(urlsplit(requests[2][0]).query)["pageToken"] == ["p2"]
    assert parse_qs(urlsplit(requests[5][0]).query)["pageToken"] == ["r2"]
