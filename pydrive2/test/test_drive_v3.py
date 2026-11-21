import json
from codecs import BOM_UTF8
from urllib.parse import parse_qs, urlsplit

import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.http import HttpMockSequence

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.files import (
    ApiRequestError,
    FileNotDownloadableError,
    GoogleDriveFile,
)
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


@pytest.mark.parametrize("stream", [True, False])
@pytest.mark.parametrize("known_type", [True, False])
def test_workspace_export_requests(stream, known_type, tmp_path):
    body = BOM_UTF8 + b"text"
    responses = [({"status": "200", "content-length": str(len(body))}, body)]
    if not known_type:
        responses.insert(
            0,
            (
                {"status": "403"},
                json.dumps(
                    {
                        "error": {
                            "code": 403,
                            "errors": [{"reason": "fileNotDownloadable"}],
                        }
                    }
                ),
            ),
        )
    drive, requests = mock_drive(*responses)
    metadata = {"id": "doc", "resourceKey": "key"}
    if known_type:
        metadata["mimeType"] = "application/vnd.google-apps.document"
    file = GoogleDriveFile(drive.auth, metadata, uploaded=True)

    if stream:
        assert b"".join(file.GetContentIOBuffer(remove_bom=True)) == b"text"
    else:
        path = tmp_path / "document"
        file.GetContentFile(path, remove_bom=True)
        assert path.read_bytes() == b"text"

    assert len(requests) == (1 if known_type else 2)
    assert urlsplit(requests[-1][0]).path.endswith("/doc/export")
    assert all(
        r[3]["X-Goog-Drive-Resource-Keys"] == "doc/key" for r in requests
    )


def test_download_permission_error_does_not_trigger_export():
    drive, requests = mock_drive(
        (
            {"status": "403"},
            json.dumps(
                {
                    "error": {
                        "code": 403,
                        "errors": [{"reason": "insufficientPermissions"}],
                    }
                }
            ),
        )
    )
    with pytest.raises(ApiRequestError):
        drive.CreateFile({"id": "private"}).GetContentIOBuffer()
    assert len(requests) == 1


def test_filesystem_copy_uses_drive_and_creates_parents(mocker):
    drive, requests = mock_drive(response({"id": "new-copy"}))
    fs = GDriveFileSystem(
        "root", google_auth=drive.auth, skip_instance_cache=True
    )
    lookup = mocker.patch.object(
        fs, "_get_item_id", side_effect=["source", "dest-dir"]
    )
    fs.cp_file("root/payload", "root/new/nested/copy")

    assert lookup.call_args_list == [
        mocker.call("root/payload"),
        mocker.call("root/new/nested", create=True),
    ]
    ((url, method, body, _),) = requests
    assert urlsplit(url).path == "/drive/v3/files/source/copy"
    assert method == "POST"
    assert json.loads(body) == {"name": "copy", "parents": ["dest-dir"]}


def test_filesystem_download_progress_uses_media_response(mocker, tmp_path):
    drive, requests = mock_drive(
        ({"status": "200", "content-length": "3"}, b"xyz")
    )
    fs = GDriveFileSystem(
        "root", google_auth=drive.auth, skip_instance_cache=True
    )
    mocker.patch.object(fs, "_get_item_id", return_value="file")
    callback = mocker.Mock()
    fs.get_file("root/payload", tmp_path / "payload", callback=callback)
    callback.set_size.assert_called_once_with(3)
    callback.absolute_update.assert_called_once_with(3)
    assert (tmp_path / "payload").read_bytes() == b"xyz"
    assert len(requests) == 1
    assert parse_qs(urlsplit(requests[0][0]).query)["alt"] == ["media"]
