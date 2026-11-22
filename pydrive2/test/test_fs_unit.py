import json
import re
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


def folder(name, item_id):
    return {"name": name, "id": item_id, "mimeType": FOLDER_MIME_TYPE}


def listing(*items):
    return response({"files": items})


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


@pytest.mark.parametrize("warm", [False, True])
def test_find_at_depth_one_only_lists_the_requested_directory(
    mock_drive, warm
):
    sub = dict(folder("sub", "S"), parents=["D"])
    file = {
        "name": "file",
        "id": "F",
        "mimeType": "text/plain",
        "parents": ["D"],
    }
    warmup = [listing(sub), listing(folder("dir2", "other"))] if warm else []
    drive, requests = mock_drive(
        listing(folder("dir", "D")), *warmup, listing(sub, file)
    )
    fs = filesystem(drive)
    if warm:
        assert fs.exists("root/dir/sub")
        assert fs.exists("root/dir2")
    found = fs.find("root/dir", 1, True, True)
    assert set(found) == {"root/dir", "root/dir/sub", "root/dir/file"}
    assert re.findall(
        r"'([^']+)' in parents", query(requests[-1])["q"][0]
    ) == ["D"]


def test_find_legacy_positional_detail_and_invalid_depth(mock_drive):
    item = {
        "name": "file",
        "id": "F",
        "mimeType": "text/plain",
        "parents": ["root"],
    }
    page = response({"files": [item]})
    drive, _ = mock_drive(page, page)
    fs = filesystem(drive)
    assert fs.find("root", True) == fs.find("root", detail=True)
    with pytest.raises(ValueError, match="maxdepth"):
        fs.find("root", maxdepth=-1)


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
    folders = [folder("duplicate", item_id) for item_id in parents]
    drive, requests = mock_drive(
        response({"files": folders, "nextPageToken": "next"}),
        listing(folders[0]),
        listing(shared),
        listing(shared, other),
    )
    fs = filesystem(drive)
    assert [
        (item["name"], item["size"])
        for item in fs.ls("root/duplicate", detail=True)
    ] == [("root/duplicate/same", 1), ("root/duplicate/same", 2)]
    _, page, first, second = requests
    assert query(page)["pageToken"] == ["next"]
    assert [
        re.findall(r"'([^']+)' in parents", query(req)["q"][0])
        for req in (first, second)
    ] == [parents[:50], parents[50:]]


def test_ls_finishes_listing_parents_resolved_before_a_rename(mock_drive):
    parents = [folder("dir", f"id-{i}") for i in range(51)]
    renamed = dict(parents[-1], name="renamed")
    child = {"id": "child", "name": "last-file", "mimeType": "text/plain"}

    def discover_rename(request):
        if "'id-0' in parents" in query(request)["q"][0]:
            assert fs.exists("root/renamed")

    drive, requests = mock_drive(
        listing(*parents),
        listing(renamed),
        listing(),
        listing(child),
        listing(),
        on_request=discover_rename,
    )
    fs = filesystem(drive)
    assert fs.exists("root/dir")
    assert fs.ls("root/dir") == ["root/dir/last-file"]
    assert "'id-50' in parents" in query(requests[-1])["q"][0]
    assert fs.ls("root/dir") == []
    assert "'id-50' in parents" not in query(requests[-1])["q"][0]


def test_ls_reuses_exact_case_folder_across_parent_batches(mock_drive):
    parents = [folder("duplicate", f"id-{i}") for i in range(51)]
    name = r"File's\name"
    child = {"id": "child", "name": "contents", "mimeType": "text/plain"}
    drive, requests = mock_drive(
        listing(*parents),
        listing(folder(name.lower(), "wrong-case")),
        listing(folder(name, "exact")),
        listing(child),
    )
    fs = filesystem(drive)
    path = f"root/duplicate/{name}"
    assert fs.exists(path)
    assert fs.ls(path) == [path + "/contents"]

    _, first, second, children = requests
    for req, batch in ((first, parents[:50]), (second, parents[50:])):
        q = query(req)["q"][0]
        assert r"name='File\'s\\name'" in q
        assert "trashed=false" in q
        assert re.findall(r"'([^']+)' in parents", q) == [
            p["id"] for p in batch
        ]
    assert re.findall(r"'([^']+)' in parents", query(children)["q"][0]) == [
        "exact"
    ]


def test_nested_file_operations_reuse_resolved_directories(mock_drive):
    sub = dict(folder("b", "B"), parents=["A"])
    file = {
        "name": "data",
        "id": "file",
        "mimeType": "text/plain",
        "parents": ["B"],
    }
    drive, requests = mock_drive(
        listing(folder("mount", "M")),
        listing(),
        response({"id": "A"}),
        listing(),
        response({"id": "B"}),
        ({"status": "200", "location": "https://example.test/upload"}, ""),
        response({"id": "file"}),
        listing(file),
        listing(sub),
        listing(file),
        listing(file),
        listing(sub, file),
    )
    fs = filesystem(drive, "root/mount")
    parent = "root/mount/a"
    leaf = parent + "/b"
    path = leaf + "/data"
    fs.touch(path)
    assert sum("q" in query(req) for req in requests) == 3

    for operation, target, expected in (
        (fs.exists, path, True),
        (fs.ls, parent, [leaf + "/"]),
        (fs.ls, leaf, [path]),
        (fs.find, leaf, [path]),
        (fs.find, parent, [path]),
    ):
        before = len(requests)
        assert operation(target) == expected
        (request,) = requests[before:]
        assert "q" in query(request)


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


@pytest.mark.parametrize("operation", ["trash", "rename"])
def test_directory_mutation_evicts_descendants_only(mock_drive, operation):
    moved = folder("renamed", "dir-id")
    mutation = (
        [listing(), response(moved)]
        if operation == "rename"
        else [response({})]
    )
    drive, requests = mock_drive(
        listing(folder("dir", "dir-id")),
        listing(folder("child", "child-id")),
        listing(folder("directory", "other-id")),
        *mutation,
        listing(),
    )
    fs = filesystem(drive, trash_only=True)
    assert fs.exists("root/dir/child")
    assert fs.exists("root/directory")
    if operation == "rename":
        fs.mv("root/dir", "gdrive:///renamed")
    else:
        fs.rm_file("root/dir")

    assert not fs.exists("root/dir/child")
    assert "name='dir'" in query(requests[-1])["q"][0]
    assert fs.exists("root/directory")


def test_move_directory_to_implicit_root(mock_drive):
    moved = folder("dir", "dir-id")
    drive, requests = mock_drive(
        listing(folder("sub", "sub-id")),
        listing(moved),
        response({"parents": ["sub-id"]}),
        response({"id": "my-drive"}),
        response(moved),
        listing(),
    )
    fs = filesystem(drive)
    fs.mv("root/sub/dir", "gdrive://")
    assert not fs.exists("root/sub/dir")
    (update,) = [r for r in requests if r[1] == "PATCH"]
    assert query(update)["addParents"] == ["my-drive"]
    assert query(update)["removeParents"] == ["sub-id"]


def test_invalidated_lookup_does_not_recache_its_response(mock_drive):
    def invalidate(request):
        fs.invalidate_cache("root/dir")

    drive, _ = mock_drive(
        listing(folder("dir", "old")), listing(), on_request=invalidate
    )
    fs = filesystem(drive)
    assert fs.exists("root/dir")
    assert not fs.exists("root/dir")


def test_copy_into_parent_invalidated_during_lookup(mock_drive):
    def invalidate(request):
        if "name='child'" in query(request).get("q", [""])[0]:
            fs.invalidate_cache("root/dir")

    source = {"name": "source", "id": "source", "mimeType": "text/plain"}
    drive, requests = mock_drive(
        listing(folder("dir", "old-parent")),
        listing(source),
        listing(),
        response({"id": "new-child"}),
        response({"id": "copy"}),
        listing(),
        on_request=invalidate,
    )
    fs = filesystem(drive)
    assert fs.exists("root/dir")
    fs.cp_file("root/source", "root/dir/child/copy")
    assert not fs.exists("root/dir/child")
    assert [
        json.loads(r[2])["parents"] for r in requests if r[1] == "POST"
    ] == [["old-parent"], ["new-child"]]


def test_move_refreshes_cached_destination_after_source_eviction(mock_drive):
    def invalidate(request):
        if request[1] == "PATCH":
            fs.invalidate_cache("root/dir")

    old = folder("dir", "already-there")
    moved = folder("dir", "moved")
    drive, requests = mock_drive(
        listing(folder("dst", "destination")),
        listing(old),
        listing(folder("child", "old-child")),
        listing(moved),
        response({"parents": ["root"]}),
        response(moved),
        listing(old, moved),
        listing(folder("child", "old-child"), folder("child", "new-child")),
        listing({"id": "file", "name": "arrived", "mimeType": "text/plain"}),
        on_request=invalidate,
    )
    fs = filesystem(drive)
    assert fs.exists("root/dst/dir/child")
    fs.mv("root/dir", "root/dst")
    assert fs.ls("root/dst/dir/child") == ["root/dst/dir/child/arrived"]
    parents = query(requests[-1])["q"][0]
    assert "'old-child' in parents" in parents
    assert "'new-child' in parents" in parents


def test_invalidating_an_unopened_mount_needs_no_drive_request(mock_drive):
    drive, requests = mock_drive()
    filesystem(drive, "root/parent/mount").invalidate_cache("root/parent")
    assert requests == []


def test_rediscovered_directory_id_leaves_its_old_path(mock_drive):
    drive, _ = mock_drive(
        listing(folder("old", "same-id")),
        listing(folder("new", "same-id")),
        listing(),
    )
    fs = filesystem(drive)
    assert fs.exists("root/old")
    assert fs.exists("root/new")
    assert not fs.exists("root/old")


def test_empty_root_marker_refreshes_a_folder_id_mount(mock_drive):
    drive, _ = mock_drive(
        response({"driveId": "shared"}),
        listing(folder("dir", "old")),
        listing(),
    )
    fs = filesystem(drive, "FolderID")
    assert fs.exists("FolderID/dir")
    fs.invalidate_cache("")
    assert not fs.exists("FolderID/dir")


@pytest.mark.parametrize("operation", ["rename", "delete"])
def test_file_mutation_reuses_cached_parent(mock_drive, operation):
    file = {"name": "file", "id": "blob", "mimeType": "text/plain"}
    mutation = (
        [listing(), listing(file), response(file)]
        if operation == "rename"
        else [listing(file), response({})]
    )
    drive, requests = mock_drive(listing(folder("dir", "parent")), *mutation)
    fs = filesystem(drive)
    assert fs.exists("root/dir")
    if operation == "rename":
        fs.mv("root/dir/file", "root/dir/renamed")
    else:
        fs.rm_file("root/dir/file")
    before = len(requests)
    assert fs.exists("root/dir")
    assert len(requests) == before


@pytest.mark.parametrize("trash_only", [False, True])
def test_shared_drive_delete_permission_error(mock_drive, trash_only):
    drive, requests = mock_drive(
        response({"driveId": "shared"}),
        listing(folder("dir", "folder-id")),
        error_response(403, "insufficientFilePermissions"),
    )
    fs = filesystem(drive, "shared", trash_only=trash_only)
    with pytest.raises(PermissionError, match="Manager"):
        fs.rm_file("shared/dir")
    assert fs.exists("shared/dir")
    assert len(requests) == 3


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
