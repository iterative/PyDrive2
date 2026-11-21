import json
from urllib.parse import urlsplit

import pytest
from googleapiclient.http import HttpMockSequence

from pydrive2.files import (
    DEFAULT_FILE_FIELDS,
    ApiRequestError,
    FileNotDownloadableError,
)
from pydrive2.test.http_mock import error_response, query, response


@pytest.mark.parametrize("kind", ["folder", "shortcut"])
def test_get_content_of_non_downloadable_file(mock_drive, kind):
    drive, requests = mock_drive(
        response(
            {"id": "file", "mimeType": f"application/vnd.google-apps.{kind}"}
        )
    )
    with pytest.raises(FileNotDownloadableError):
        drive.CreateFile({"id": "file"}).GetContentString()
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("code", "reason", "exception"),
    [
        (403, "fileNotExportable", FileNotDownloadableError),
        (400, "conversionUnsupported", FileNotDownloadableError),
        (403, "insufficientFilePermissions", ApiRequestError),
    ],
)
def test_export_errors(mock_drive, code, reason, exception):
    drive, _ = mock_drive(
        response(
            {"id": "form", "mimeType": "application/vnd.google-apps.form"}
        ),
        error_response(403, "fileNotDownloadable"),
        error_response(code, reason),
    )
    with pytest.raises(exception):
        drive.CreateFile({"id": "form"}).GetContentString()


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ("permissions(role,id,type)", "id,permissions(role,id,type)"),
        (
            "permissions(permissionDetails(role,permissionType),id), id",
            "permissions(permissionDetails(role,permissionType),id), id",
        ),
        (" * ", "*"),
        (None, DEFAULT_FILE_FIELDS),
    ],
)
def test_create_retains_id_for_next_upload(mock_drive, fields, expected):
    drive, requests = mock_drive(
        response({"id": "new"}), response({"id": "new"})
    )
    file = drive.CreateFile({"name": "first", "parents": ["folder"]})
    file.Upload({"fields": fields})
    file["name"] = "second"
    file.Upload()
    create, update = requests
    assert create[1] == "POST" and query(create)["fields"] == [expected]
    assert json.loads(create[2]) == {"name": "first", "parents": ["folder"]}
    assert update[1] == "PATCH" and urlsplit(update[0]).path.endswith(
        "/files/new"
    )
    assert json.loads(update[2]) == {"name": "second"}


def test_move_fetches_current_parent_after_projected_update(mock_drive):
    drive, requests = mock_drive(
        response({"parents": ["A"]}),
        response({"id": "file"}),
        response({"parents": ["B"]}),
        response({"parents": ["C"]}),
    )
    file = drive.CreateFile({"id": "file"})
    file.FetchMetadata()
    file.Upload({"addParents": "B", "removeParents": "A", "fields": "id"})
    file["parents"] = ["C"]
    file.Upload()
    _, _, fetch_parents, move = requests
    assert query(fetch_parents)["fields"] == ["parents"]
    assert query(move)["removeParents"] == ["B"]
    assert query(move)["addParents"] == ["C"]
    assert "parents" not in json.loads(move[2])


def test_move_to_existing_root_by_alias(mock_drive):
    drive, requests = mock_drive(
        response({"parents": ["canonical-root"]}),
        response({"id": "canonical-root"}),
        response({}),
    )
    file = drive.CreateFile({"id": "file", "parents": ["root"]})
    file.Upload()
    fetch, root, update = requests
    assert query(fetch)["fields"] == ["parents"]
    assert urlsplit(root[0]).path.endswith("/files/root")
    assert not {"addParents", "removeParents"}.intersection(query(update))


def test_move_rejects_multiple_parents(mock_drive):
    drive, requests = mock_drive()
    with pytest.raises(ValueError, match="one parent"):
        drive.CreateFile({"id": "file", "parents": ["A", "B"]}).Upload()
    assert not requests


def test_custom_transport_is_used_without_mutating_params(mock_drive):
    drive, default_requests = mock_drive()
    http = HttpMockSequence(
        [
            response({"id": "new"}),
            response({}),
            response({}),
            response({}),
            ({"status": "204"}, ""),
        ]
    )
    params = {"http": http}
    file = drive.CreateFile({"name": "test"})
    file.Upload(params)
    file.Upload(param=params)
    file.Trash(param=params)
    assert file["trashed"] is True
    file.UnTrash(params)
    assert file["trashed"] is False
    file.Delete(params)
    create, update, trash, untrash, delete = http.request_sequence
    assert [r[1] for r in (create, update, trash, untrash, delete)] == [
        "POST",
        "PATCH",
        "PATCH",
        "PATCH",
        "DELETE",
    ]
    assert json.loads(trash[2]) == {"trashed": True}
    assert json.loads(untrash[2]) == {"trashed": False}
    assert params == {"http": http} and not default_requests


def test_copy_sets_name_and_parent(mock_drive):
    drive, requests = mock_drive(response({"id": "copy"}))
    copy = drive.CreateFile({"id": "source"}).Copy(
        target_folder={"id": "destination"}, new_title="second"
    )
    assert copy.uploaded and copy["id"] == "copy"
    (request,) = requests
    assert urlsplit(request[0]).path.endswith("/files/source/copy")
    assert json.loads(request[2]) == {
        "name": "second",
        "parents": ["destination"],
    }


def test_workspace_import_uses_source_media_type(mock_drive):
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


def test_id_only_workspace_update_discards_source_bytes(mock_drive):
    target = "application/vnd.google-apps.document"
    drive, requests = mock_drive(
        ({"status": "200", "location": "https://example.test/session"}, ""),
        response({"id": "doc", "mimeType": target}),
    )
    file = drive.CreateFile({"id": "doc"})
    file.SetContentString("source")
    file.Upload({"fields": "id"}, media_mime_type="text/plain")
    assert file.content is None
    assert query(requests[0])["fields"] == ["mimeType,id"]


def test_insert_permission_respects_projection_and_refreshes_cache(mock_drive):
    permission = {
        "role": "reader",
        "type": "user",
        "emailAddress": "reader@example.test",
    }
    saved = dict(permission, id="reader")
    drive, requests = mock_drive(
        response(saved), response({"permissions": [saved]})
    )
    file = drive.CreateFile({"id": "file"})
    assert file.InsertPermission(permission) == saved
    assert file["permissions"] == [saved]
    create, refresh = requests
    assert query(create)["fields"] == ["*"]
    assert json.loads(create[2]) == permission
    assert refresh[1] == "GET"


def test_permissions_paginate_and_delete_from_cache(mock_drive):
    reader, owner = {"id": "reader"}, {"id": "owner"}
    drive, requests = mock_drive(
        response({"permissions": [reader], "nextPageToken": "more"}),
        response({"permissions": [owner]}),
        ({"status": "204"}, ""),
    )
    file = drive.CreateFile({"id": "file"})
    assert file.GetPermissions() == [reader, owner]
    assert file.DeletePermission("reader")
    assert file["permissions"] == [owner]
    _, next_page, delete = requests
    assert query(next_page)["pageToken"] == ["more"]
    assert urlsplit(delete[0]).path.endswith("/permissions/reader")


def test_revisions_pagination(mock_drive):
    drive, requests = mock_drive(
        response({"revisions": [{"id": "1"}], "nextPageToken": "more"}),
        response({"revisions": [{"id": "2"}]}),
    )
    assert drive.CreateFile({"id": "file"}).GetRevisions() == [
        {"id": "1"},
        {"id": "2"},
    ]
    first, second = requests
    assert urlsplit(first[0]).path.endswith("/files/file/revisions")
    assert query(second)["pageToken"] == ["more"]


@pytest.mark.parametrize("operation", ["GetPermissions", "GetRevisions"])
def test_child_list_errors_retain_drive_reason(mock_drive, operation):
    drive, _ = mock_drive(error_response(403, "rateLimitExceeded"))
    with pytest.raises(ApiRequestError) as caught:
        getattr(drive.CreateFile({"id": "file"}), operation)()
    assert caught.value.GetField("reason") == "rateLimitExceeded"
