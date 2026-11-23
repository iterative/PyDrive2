import gc
import io
import json
import weakref
from codecs import BOM_UTF8
from urllib.parse import urlsplit

import pytest
from googleapiclient.http import HttpMockSequence

from pydrive2.files import (
    DEFAULT_FILE_FIELDS,
    ApiRequestError,
    FileNotDownloadableError,
    FileNotUploadedError,
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


def test_cached_workspace_content_uses_export(mock_drive):
    metadata = {
        "id": "doc",
        "mimeType": "application/vnd.google-apps.document",
    }
    drive, requests = mock_drive(
        response({"files": [metadata]}), ({"status": "200"}, b"document")
    )
    (file,) = drive.ListFile().GetList()
    assert file.GetContentString() == "document"
    listing, export = requests
    assert urlsplit(listing[0]).path.endswith("/files")
    assert urlsplit(export[0]).path.endswith("/files/doc/export")


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


def test_upload_rejects_listed_file_without_id(mock_drive):
    drive, requests = mock_drive(
        response({"files": [{"name": "original"}]}),
        response({"id": "duplicate"}),
    )
    (file,) = drive.ListFile({"fields": "files(name)"}).GetList()
    file["name"] = "renamed"
    with pytest.raises(FileNotUploadedError, match="Drive file ID"):
        file.Upload()
    assert [request[1] for request in requests] == ["GET"]


def test_upload_updates_listed_file_with_id(mock_drive):
    drive, requests = mock_drive(
        response({"files": [{"id": "existing", "name": "original"}]}),
        response({"id": "existing", "name": "renamed"}),
    )
    (file,) = drive.ListFile({"fields": "files(id,name)"}).GetList()
    file["name"] = "renamed"
    file.Upload()
    listing, update = requests
    assert listing[1] == "GET"
    assert update[1] == "PATCH"
    assert urlsplit(update[0]).path == "/drive/v3/files/existing"
    assert json.loads(update[2]) == {"name": "renamed"}


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


@pytest.mark.parametrize(
    "metadata", [{"name": "new"}, {"id": "existing"}], ids=["create", "update"]
)
def test_upload_rejects_multiple_parents(mock_drive, metadata):
    drive, requests = mock_drive()
    with pytest.raises(ValueError, match="one parent"):
        drive.CreateFile(dict(metadata, parents=["A", "B"])).Upload()
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


def read_stream(file, tmp_path, **kwargs):
    return b"".join(file.GetContentIOBuffer(**kwargs))


def read_disk(file, tmp_path, **kwargs):
    dest = tmp_path / "document"
    file.GetContentFile(dest, **kwargs)
    return dest.read_bytes()


def read_string(file, tmp_path, **kwargs):
    return file.GetContentString(**kwargs).encode()


@pytest.mark.parametrize("read", [read_disk, read_stream, read_string])
@pytest.mark.parametrize(
    ("source", "target"),
    [
        ("document", "text/plain"),
        ("spreadsheet", "text/csv"),
        ("presentation", "text/plain"),
        ("drawing", "image/svg+xml"),
    ],
)
def test_supplied_workspace_type_goes_directly_to_export(
    mock_drive, tmp_path, read, source, target
):
    drive, requests = mock_drive(({"status": "200"}, b"export"))
    file = drive.CreateFile(
        {"id": "doc", "mimeType": f"application/vnd.google-apps.{source}"}
    )
    assert read(file, tmp_path, mimetype=target) == b"export"
    (export,) = requests
    assert urlsplit(export[0]).path == "/drive/v3/files/doc/export"
    assert query(export)["mimeType"] == [target]


@pytest.mark.parametrize("read", [read_stream, read_disk])
@pytest.mark.parametrize("known_type", [True, False], ids=["known", "id-only"])
def test_workspace_export_requests(mock_drive, read, known_type, tmp_path):
    body = BOM_UTF8 + b"text"
    responses = [({"status": "200", "content-length": str(len(body))}, body)]
    metadata = {"id": "doc", "resourceKey": "key"}
    if known_type:
        metadata["mimeType"] = "application/vnd.google-apps.document"
    else:
        responses.insert(0, error_response(403, "fileNotDownloadable"))
    drive, requests = mock_drive(*responses)
    assert (
        read(drive.CreateFile(metadata), tmp_path, remove_bom=True) == b"text"
    )
    assert len(requests) == (1 if known_type else 2)
    assert urlsplit(requests[-1][0]).path.endswith("/doc/export")
    assert all(
        r[3]["X-Goog-Drive-Resource-Keys"] == "doc/key" for r in requests
    )


@pytest.mark.parametrize("read", [read_stream, read_disk])
def test_plain_text_media_removes_bom_across_chunks(
    mock_drive, read, tmp_path
):
    drive, _ = mock_drive(
        ({"status": "206", "content-range": "bytes 0-1/5"}, BOM_UTF8[:2]),
        (
            {"status": "206", "content-range": "bytes 2-3/5"},
            BOM_UTF8[2:] + b"x",
        ),
        ({"status": "206", "content-range": "bytes 4-4/5"}, b"y"),
    )
    file = drive.CreateFile({"id": "blob"})
    assert (
        read(
            file, tmp_path, mimetype="text/plain", remove_bom=True, chunksize=2
        )
        == b"xy"
    )


def test_stream_decodes_utf8_across_chunks(mock_drive):
    drive, _ = mock_drive(
        ({"status": "206", "content-range": "bytes 0-0/2"}, b"\xc3"),
        ({"status": "206", "content-range": "bytes 1-1/2"}, b"\xa9"),
    )
    file = drive.CreateFile({"id": "blob"})
    assert (
        "".join(file.GetContentIOBuffer(encoding="utf-8", chunksize=1)) == "é"
    )


@pytest.mark.parametrize(
    "denied",
    [
        error_response(403, "insufficientFilePermissions"),
        ({"status": "403"}, '{"error":{"errors":[]}}'),
        ({"status": "503"}, "unavailable"),
    ],
)
def test_first_download_error_preserves_existing_file(
    mock_drive, tmp_path, denied
):
    drive, requests = mock_drive(denied)
    dest = tmp_path / "existing"
    dest.write_bytes(b"previous contents")
    with pytest.raises(ApiRequestError) as caught:
        drive.CreateFile({"id": "private"}).GetContentFile(dest)
    assert caught.value.error["code"] == int(denied[0]["status"])
    assert dest.read_bytes() == b"previous contents"
    assert len(requests) == 1


def test_later_download_error_leaves_completed_chunks(mock_drive, tmp_path):
    drive, _ = mock_drive(
        ({"status": "206", "content-range": "bytes 0-1/4"}, b"ab"),
        error_response(503, "backendError"),
    )
    dest = tmp_path / "existing"
    dest.write_bytes(b"previous contents")
    with pytest.raises(ApiRequestError):
        drive.CreateFile({"id": "file"}).GetContentFile(dest, chunksize=2)
    assert dest.read_bytes() == b"ab"


def test_disk_download_releases_each_chunk_before_requesting_the_next(
    mock_drive, tmp_path, monkeypatch
):
    class Chunk(bytearray):
        """A response body whose lifetime can be observed with a weak reference."""

    first = Chunk(b"ab")
    previous = weakref.ref(first)
    drive, _ = mock_drive(
        ({"status": "206", "content-range": "bytes 0-1/4"}, first),
        ({"status": "206", "content-range": "bytes 2-3/4"}, b"cd"),
    )
    del first
    transport = drive.auth.thread_local.http
    request = transport.request

    def check_request(uri, method, **kwargs):
        if kwargs["headers"]["range"] == "bytes=2-3":
            gc.collect()
            assert previous() is None
        return request(uri, method, **kwargs)

    monkeypatch.setattr(transport, "request", check_request)
    dest = tmp_path / "download"
    drive.CreateFile({"id": "file"}).GetContentFile(dest, chunksize=2)
    assert dest.read_bytes() == b"abcd"


@pytest.mark.parametrize("existing", [False, True], ids=["create", "update"])
def test_path_upload_uses_one_session(mock_drive, tmp_path, existing):
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    drive, requests = mock_drive(
        ({"status": "200", "location": "https://example.test/session"}, ""),
        response({"id": "file"}),
    )
    file = drive.CreateFile({"id": "file"} if existing else {})
    file.SetContentFile(source)
    opened = file.content
    assert not requests
    file.Upload()
    assert opened.closed
    start, body = requests
    assert start[1] == ("PATCH" if existing else "POST")
    path = "/upload/drive/v3/files" + ("/file" if existing else "")
    assert urlsplit(start[0]).path == path
    assert body[:2] == ("https://example.test/session", "PUT")


def test_path_upload_closes_file_and_can_retry(
    mock_drive, tmp_path, monkeypatch
):
    source = tmp_path / "source"
    source.write_bytes(b'{"id":"from-retried-upload"}')
    drive, requests = mock_drive(
        error_response(503, "backendError"),
        ({"status": "200", "location": "https://example.test/session"}, ""),
        ({"status": "200"}, "echo_request_body"),
        response({"id": "from-retried-upload"}),
    )
    file = drive.CreateFile()
    monkeypatch.chdir(tmp_path)
    file.SetContentFile("source")
    opened = file.content
    assert not requests
    with pytest.raises(ApiRequestError):
        file.Upload()
    assert opened.closed
    (failed_create,) = requests
    assert failed_create[1] == "POST"
    monkeypatch.chdir(tmp_path.parent)
    file.Upload()
    assert file["id"] == "from-retried-upload"
    assert file.content is None
    retry, upload = requests[1:]
    assert retry[:2] == failed_create[:2]
    assert upload[:2] == ("https://example.test/session", "PUT")
    source.unlink()
    file["name"] = "renamed"
    file.Upload()
    (update,) = requests[3:]
    assert update[1] == "PATCH"
    assert urlsplit(update[0]).path == "/drive/v3/files/from-retried-upload"
    assert json.loads(update[2]) == {"name": "renamed"}


def test_replacing_upload_content_respects_ownership(mock_drive, tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"payload")
    drive, requests = mock_drive(
        ({"status": "200", "location": "https://example.test/session"}, ""),
        response({"id": "uploaded"}),
    )
    file = drive.CreateFile()
    file.SetContentFile(source)
    owned = file.content
    external = io.BytesIO(b"external")
    file.content = external
    assert owned.closed
    assert not requests
    file.Upload()
    file.SetContentString("replacement")
    assert external.getvalue() == b"external"
    assert [r[1] for r in requests] == ["POST", "PUT"]
