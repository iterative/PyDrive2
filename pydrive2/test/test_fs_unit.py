import json
import tempfile
from urllib.parse import urlsplit

import pytest
from fsspec.callbacks import Callback
from googleapiclient.http import DEFAULT_CHUNK_SIZE

from pydrive2.fs import GDriveFileSystem
from pydrive2.fs.spec import FOLDER_MIME_TYPE
from pydrive2.fs.utils import IterStream
from pydrive2.test.http_mock import error_response, query, response


def filesystem(drive, root="root", **kwargs):
    return GDriveFileSystem(
        root, google_auth=drive.auth, skip_instance_cache=True, **kwargs
    )


@pytest.mark.parametrize("root", ["root", "appDataFolder"])
def test_find_accepts_canonical_parent_of_root_alias(mock_drive, root):
    item = {
        "id": "child",
        "name": "note",
        "mimeType": "text/plain",
        "parents": ["canonical-root-id"],
        "size": "5",
    }
    drive, requests = mock_drive(response({"files": [item]}))
    assert filesystem(drive, root).find(root) == [f"{root}/note"]
    (listing,) = requests
    assert f"'{root}' in parents" in query(listing)["q"][0]
    if root == "appDataFolder":
        assert query(listing)["spaces"] == ["appDataFolder"]


@pytest.mark.parametrize("withdirs", [True, False])
def test_find_mount_with_only_subdirectories_skips_stat(mock_drive, withdirs):
    folder = {
        "name": "sub",
        "id": "sub-id",
        "mimeType": FOLDER_MIME_TYPE,
        "parents": ["FolderID"],
    }
    listing = response({"files": [folder]})
    drive, requests = mock_drive(
        response({"driveId": "shared", "mimeType": FOLDER_MIME_TYPE}),
        listing,
        listing,
    )
    fs = filesystem(drive, "FolderID")
    for _ in range(2):
        found = fs.find("FolderID", maxdepth=1, withdirs=withdirs, detail=True)
        assert set(found) == (
            {"FolderID", "FolderID/sub"} if withdirs else set()
        )
        assert all(name == item["name"] for name, item in found.items())
    scope, *lists = requests
    assert set(query(scope)["fields"][0].split(",")) == {"driveId", "mimeType"}
    assert all("'FolderID' in parents" in query(r)["q"][0] for r in lists)


@pytest.mark.parametrize("bare", [True, False])
def test_find_file_returns_stat(mock_drive, bare):
    obj = {"id": "Blob", "name": "note", "mimeType": "text/plain", "size": "7"}
    if bare:
        drive, requests = mock_drive(
            response({"mimeType": "text/plain"}), response(obj)
        )
        fs, path = filesystem(drive, "Blob"), "Blob"
    else:
        named = response({"files": [obj]})
        drive, requests = mock_drive(named, response(obj))
        fs, path = filesystem(drive), "root/note"
    assert fs.find(path, withdirs=True, detail=True) == {
        path: {"name": path, "type": "file", "size": 7, "checksum": None}
    }
    if bare:
        assert all("q" not in query(r) for r in requests)


def test_find_empty_folder_checks_trash_when_including_start(mock_drive):
    empty = response({"files": []})
    drive, requests = mock_drive(
        response({"mimeType": FOLDER_MIME_TYPE}),
        empty,
        empty,
        response({"mimeType": FOLDER_MIME_TYPE, "trashed": True}),
    )
    fs = filesystem(drive, "FolderID")
    assert fs.find("FolderID") == []
    assert len(requests) == 2
    assert fs.find("FolderID", withdirs=True) == []


def test_ls_deduplicates_a_file_shared_across_parent_batches(mock_drive):
    parents = [f"parent-{i}" for i in range(51)]
    shared = {
        "id": "shared",
        "name": "same",
        "mimeType": "text/plain",
        "parents": [parents[0], parents[-1]],
        "size": "1",
    }
    other = dict(shared, id="distinct", parents=[parents[-1]], size="2")
    drive, requests = mock_drive(
        response({"files": [shared]}), response({"files": [shared, other]})
    )
    fs = filesystem(drive)
    fs._cache_path_id("duplicate", *parents)
    assert [
        (item["name"], item["size"])
        for item in fs.ls("root/duplicate", detail=True)
    ] == [("root/duplicate/same", 1), ("root/duplicate/same", 2)]
    first, second = requests
    assert query(first)["q"][0].count(" in parents") == 50
    assert query(second)["q"][0].count(" in parents") == 1


def test_find_matches_legacy_parent_before_assuming_root(mock_drive):
    folder = {
        "id": "dir",
        "name": "sub",
        "mimeType": FOLDER_MIME_TYPE,
        "parents": ["canonical-root"],
    }
    file = {
        "id": "file",
        "name": "child",
        "mimeType": "text/plain",
        "parents": ["elsewhere", "dir"],
    }
    drive, _ = mock_drive(
        response({"files": [folder]}), response({"files": [file]})
    )
    fs = filesystem(drive)
    assert fs.exists("root/sub")
    assert fs.find("root") == ["root/sub/child"]


@pytest.mark.parametrize("trash_only", [False, True])
def test_shared_drive_delete_permission_error(mock_drive, trash_only):
    file = {"id": "file", "name": "note", "mimeType": "text/plain"}
    drive, _ = mock_drive(
        response({"driveId": "shared"}),
        response({"files": [file]}),
        error_response(403, "insufficientFilePermissions"),
    )
    with pytest.raises(PermissionError, match="Manager"):
        filesystem(drive, "shared", trash_only=trash_only).rm_file(
            "shared/note"
        )


def test_copy_creates_missing_directories_and_copies_on_drive(mock_drive):
    source = {"name": "payload", "id": "source", "mimeType": "text/plain"}
    drive, requests = mock_drive(
        response({"files": [source]}),
        response({"files": []}),
        response({"id": "new"}),
        response({"files": []}),
        response({"id": "nested"}),
        response({"id": "copy"}),
    )
    filesystem(drive).cp_file("root/payload", "root/new/nested/copy")
    (
        source_lookup,
        new_lookup,
        new_folder,
        nested_lookup,
        nested_folder,
        copy,
    ) = requests
    assert [r[1] for r in requests] == [
        "GET",
        "GET",
        "POST",
        "GET",
        "POST",
        "POST",
    ]
    assert "name='payload'" in query(source_lookup)["q"][0]
    assert "'root' in parents" in query(new_lookup)["q"][0]
    assert json.loads(new_folder[2])["parents"] == ["root"]
    assert "'new' in parents" in query(nested_lookup)["q"][0]
    assert json.loads(nested_folder[2])["parents"] == ["new"]
    assert urlsplit(copy[0]).path == "/drive/v3/files/source/copy"
    assert json.loads(copy[2]) == {"name": "copy", "parents": ["nested"]}


@pytest.mark.parametrize(
    ("code", "reason"), [(503, "backendError"), (403, "rateLimitExceeded")]
)
def test_copy_retries_only_copy_request(mock_drive, code, reason):
    source = {"name": "payload", "id": "source", "mimeType": "text/plain"}
    drive, requests = mock_drive(
        response({"files": [source]}),
        error_response(code, reason),
        response({"id": "copy"}),
    )
    filesystem(drive).cp_file("root/payload", "root/copy")
    lookup, first, retry = requests
    assert lookup[1] == "GET"
    assert first[:3] == retry[:3]
    assert urlsplit(retry[0]).path.endswith("/source/copy")


def test_info_and_listing_request_only_required_fields(mock_drive):
    item = {
        "name": "payload",
        "id": "blob",
        "mimeType": "text/plain",
        "size": "3",
        "md5Checksum": "abc",
    }
    drive, requests = mock_drive(response({"files": [item]}), response(item))
    assert filesystem(drive).info("root/payload") == {
        "name": "root/payload",
        "type": "file",
        "size": 3,
        "checksum": "abc",
    }
    lookup, get = requests
    assert query(lookup)["fields"] == [
        "nextPageToken,files(id,name,parents,mimeType,size,md5Checksum)"
    ]
    assert query(get)["fields"] == ["mimeType,size,md5Checksum"]


@pytest.mark.parametrize(
    ("media", "expected"),
    [
        (
            [
                ({"status": "206", "content-range": "bytes 0-1/3"}, b"xy"),
                ({"status": "206", "content-range": "bytes 2-2/3"}, b"z"),
            ],
            [(3, 0), (3, 2), (3, 3)],
        ),
        ([({"status": "200"}, b"xyz")], [(None, 3)]),
    ],
    ids=["two-chunks", "unknown-total"],
)
def test_download_progress_uses_media_responses(
    mock_drive, tmp_path, media, expected
):
    source = {
        "name": "payload",
        "id": "file",
        "mimeType": "application/octet-stream",
    }
    drive, requests = mock_drive(response({"files": [source]}), *media)
    updates = []
    callback = Callback(
        hooks={"record": lambda size, value: updates.append((size, value))}
    )
    dest = tmp_path / "result"
    filesystem(drive).get_file(
        "root/payload", dest, callback=callback, block_size=2
    )
    lookup, *downloads = requests
    assert query(lookup)["q"]
    assert len(downloads) == len(media)
    assert all(query(request)["alt"] == ["media"] for request in downloads)
    assert updates == expected
    assert dest.read_bytes() == b"xyz"


def test_partial_reads_and_peek_across_chunks():
    stream = IterStream(iter([b"abc", b"", b"de", b"fghi"]))
    assert stream.read1(0) == b""
    assert stream.readinto(bytearray()) == 0
    assert stream.read1(1) == b"a"
    assert stream.peek(5) == b"bcdef"
    buf = bytearray(2)
    assert stream.readinto1(buf) == 2
    assert buf == b"bc"
    assert stream.peek(2) == b"de"
    assert stream.read() == b"defghi"
    assert stream.read1() == b""
    assert stream.peek(4) == b""


def test_peek_preserves_buffer_if_next_chunk_fails():
    first = bytearray(b"ab")
    last = bytearray(b"cd")

    def chunks():
        yield memoryview(first)
        yield memoryview(last)
        raise OSError("download failed")

    with IterStream(chunks()) as stream:
        with pytest.raises(OSError) as error:
            stream.peek(5)
        # The caller can reuse both source buffers while the traceback is alive.
        first.clear()
        last.clear()
        assert str(error.value) == "download failed"
        assert stream.read1() == b"abcd"
    with pytest.raises(ValueError, match="closed"):
        stream.read1()
    with pytest.raises(ValueError, match="closed"):
        stream.readinto(b"")


def test_readinto_releases_destination_on_download_error():
    def chunks():
        yield b"x"
        raise OSError("download failed")

    dest = bytearray(1)
    with IterStream(chunks()) as stream:
        stream.readinto(dest)
        with pytest.raises(OSError) as error:
            stream.readinto(dest)
        # Keep the download traceback alive while the caller resizes its buffer.
        dest.clear()
        assert str(error.value) == "download failed"


def test_open_uses_requested_download_block_size(mock_drive):
    drive, requests = mock_drive(
        ({"status": "206", "content-range": "bytes 0-1/3"}, b"xy"),
        ({"status": "206", "content-range": "bytes 2-2/3"}, b"z"),
    )
    with filesystem(drive, "FileID").open("FileID", block_size=2) as stream:
        assert stream.read() == b"xyz"
    assert [r[3]["range"] for r in requests] == ["bytes=0-1", "bytes=2-3"]


@pytest.mark.parametrize("block_size", [None, "default"])
def test_open_uses_default_download_block_size(mock_drive, block_size):
    drive, requests = mock_drive(({"status": "200"}, b"data"))
    fs = filesystem(drive, "FileID")
    with fs.open("FileID", block_size=block_size) as stream:
        assert stream.read() == b"data"
    (request,) = requests
    assert request[3]["range"] == f"bytes=0-{DEFAULT_CHUNK_SIZE - 1}"


@pytest.mark.parametrize("mode", ["wb", "wt"])
def test_flush_then_write_uploads_once_on_close(mock_drive, monkeypatch, mode):
    drive, _ = mock_drive()
    fs = filesystem(drive)
    monkeypatch.setattr(fs, "blocksize", 5)
    rollover_positions = []
    rollover = tempfile.SpooledTemporaryFile.rollover

    def record_rollover(spool):
        rollover_positions.append(spool.tell())
        return rollover(spool)

    monkeypatch.setattr(
        tempfile.SpooledTemporaryFile, "rollover", record_rollover
    )
    uploads = []

    def upload(body, path):
        uploads.append((path, body.read()))

    monkeypatch.setattr(fs, "upload_fobj", upload)
    with fs.open("root/file", mode) as writer:
        value = b"abc" if mode == "wb" else "abc"
        written = writer.write(value)
        assert written == 3
        writer.flush()
        assert not writer.closed
        assert not uploads
        assert not rollover_positions
        written = writer.write(value)
        assert written == 3
    assert rollover_positions == [3]
    writer.close()
    assert uploads == [("root/file", b"abcabc")]


def test_failed_close_releases_spool_and_propagates_error(
    mock_drive, monkeypatch
):
    drive, _ = mock_drive()
    fs = filesystem(drive)
    monkeypatch.setattr(fs, "blocksize", 2)
    attempts = []

    def fail(body, path):
        attempts.append(body)
        assert body.read() == b"data"
        raise OSError("upload failed")

    monkeypatch.setattr(fs, "upload_fobj", fail)
    writer = fs.open("root/file", "wb")
    writer.write(b"data")
    with pytest.raises(OSError, match="upload failed"):
        writer.close()
    assert writer.closed
    writer.close()
    (body,) = attempts
    assert body.closed


def test_large_write_after_close_does_not_open_a_tempfile(mock_drive, mocker):
    drive, _ = mock_drive()
    fs = filesystem(drive)
    mocker.patch.object(fs, "blocksize", 2)
    mocker.patch.object(fs, "upload_fobj")
    backing_file = mocker.spy(tempfile, "TemporaryFile")
    with fs.open("root/file", "wb") as writer:
        writer.write(b"x")
    with pytest.raises(ValueError, match="closed"):
        writer.write(b"too large")
    backing_file.assert_not_called()
