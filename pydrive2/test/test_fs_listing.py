import re
from unittest.mock import Mock

import pytest

from pydrive2.fs import GDriveFileSystem
from pydrive2.fs.spec import FOLDER_MIME_TYPE


@pytest.fixture
def listing_fs():
    return GDriveFileSystem(
        "root", google_auth=Mock(), skip_instance_cache=True
    )


@pytest.mark.parametrize("count", [0, 1, 50, 51, 100, 251])
@pytest.mark.parametrize("container", [list, dict.fromkeys, iter])
def test_list_ids_batches(listing_fs, monkeypatch, count, container):
    ids = [f"parent-{index}" for index in range(count)]
    listed = []

    def list_query(query):
        parents = re.findall(r"'([^']+)' in parents", query)
        # Model an API that rejects overly complex parent queries.
        assert 0 < len(parents) <= 50
        assert query.endswith(") and trashed=false")
        listed.extend(parents)
        return ({"id": parent} for parent in parents)

    monkeypatch.setattr(listing_fs, "_gdrive_list", list_query)

    assert [
        item["id"] for item in listing_fs._gdrive_list_ids(container(ids))
    ] == ids
    assert listed == ids


def test_list_ids_lazy(listing_fs, monkeypatch):
    consumed = []

    def parents():
        for index in range(51):
            consumed.append(index)
            yield str(index)

    list_query = Mock(
        side_effect=[
            iter([{"id": "page-1"}, {"id": "page-2"}]),
            iter([{"id": "last"}]),
        ]
    )
    monkeypatch.setattr(listing_fs, "_gdrive_list", list_query)
    result = listing_fs._gdrive_list_ids(parents())
    assert consumed == []
    list_query.assert_not_called()

    assert next(result)["id"] == "page-1"
    assert len(consumed) == 50
    assert next(result)["id"] == "page-2"
    assert list_query.call_count == 1
    assert list(result) == [{"id": "last"}]
    assert len(consumed) == 51
    assert list_query.call_count == 2


def test_ls_repeated_parent_across_batches(listing_fs, monkeypatch):
    parents = [f"id-{i}" for i in range(51)]
    monkeypatch.setattr(
        listing_fs, "_path_to_item_ids", lambda path: [*parents, parents[0]]
    )
    batches = []

    def list_file(params):
        batch = re.findall(r"'([^']+)' in parents", params["q"])
        batches.append(batch)
        # Distinct children with the same name must all remain in the listing.
        children = [
            {
                "id": f"child-{parent}",
                "name": "file",
                "mimeType": "text/plain",
            }
            for parent in dict.fromkeys(batch)
        ]
        return iter([children[:1], [], children[1:]])

    monkeypatch.setattr(listing_fs.client, "ListFile", list_file)
    assert listing_fs.ls("root/duplicate") == ["root/duplicate/file"] * 51
    assert batches == [parents[:50], parents[50:]]


def test_ls_uses_parents_cached_at_start(listing_fs, monkeypatch):
    parents = [f"id-{i}" for i in range(51)]
    listing_fs._cache_path_id("dir", *parents)

    def list_query(query):
        if "'id-0' in parents" in query:
            # Simulate another caller caching a parent during the first batch.
            listing_fs._cache_path_id("dir", "late-parent")
        if "'late-parent' in parents" in query:
            return [
                {
                    "id": "late-file",
                    "name": "late-file",
                    "mimeType": "text/plain",
                }
            ]
        return []

    monkeypatch.setattr(listing_fs, "_gdrive_list", list_query)
    assert listing_fs.ls("root/dir") == []
    assert listing_fs.ls("root/dir") == ["root/dir/late-file"]


def test_path_lookup_batches_duplicate_folder_names(listing_fs, monkeypatch):
    parents = [f"id-{i}" for i in range(51)]
    listing_fs._cache_path_id("duplicate", *parents)
    name = r"File's\name"
    batches = []

    def list_file(params):
        query = params["q"]
        batch = re.findall(r"'([^']+)' in parents", query)
        assert 0 < len(batch) <= 50
        assert r"name='File\'s\\name'" in query
        assert "trashed=false" in query
        batches.append(batch)
        if parents[-1] in batch:
            item_id, item_name = "found", name
        else:
            item_id, item_name = "wrong-case", name.lower()
        item = {
            "id": item_id,
            "name": item_name,
            "mimeType": FOLDER_MIME_TYPE,
        }
        return iter([[item]])

    monkeypatch.setattr(listing_fs.client, "ListFile", list_file)
    assert listing_fs.exists(f"root/duplicate/{name}")
    assert listing_fs._ids_cache["dirs"][f"duplicate/{name}"] == ["found"]
    assert batches == [parents[:50], parents[50:]]


@pytest.mark.parametrize("detail", [False, True])
def test_find_wide_directory(listing_fs, monkeypatch, detail):
    folders = [f"folder-{index}" for index in range(251)]

    def list_query(query):
        parents = re.findall(r"'([^']+)' in parents", query)
        assert 0 < len(parents) <= 50
        if parents == ["root"]:
            return iter(
                {
                    "id": folder,
                    "name": folder,
                    "mimeType": FOLDER_MIME_TYPE,
                    "parents": ["root"],
                }
                for folder in folders
            )
        return iter(
            {
                "id": f"data-{parent}",
                "name": "data",
                "mimeType": "application/octet-stream",
                "parents": [parent],
                "size": "7",
                "md5Checksum": "checksum",
            }
            for parent in parents
        )

    monkeypatch.setattr(listing_fs, "_gdrive_list", list_query)
    files = listing_fs.find("root", detail=detail)
    expected = [f"root/{folder}/data" for folder in folders]
    assert list(files) == expected
    if detail:
        assert all(
            item
            == {
                "name": path,
                "type": "file",
                "size": 7,
                "checksum": "checksum",
            }
            for path, item in files.items()
        )


@pytest.mark.parametrize("operation", ["trash", "move"])
def test_directory_mutation_invalidates_subtree(listing_fs, mocker, operation):
    fs = listing_fs
    fs._cache_path_id("dir", "first", "second")
    fs._cache_path_id("dir/nested", "nested")
    fs._cache_path_id("directory", "unrelated")
    mocker.patch.object(fs, "_gdrive_list_ids", return_value=[])
    if operation == "trash":
        delete = mocker.patch.object(fs, "_gdrive_delete_file")
        fs.rm_file("root/dir")
        delete.assert_called_once_with("first")
    else:
        source = mocker.patch.object(fs.client, "CreateFile")
        source.return_value.get.return_value = FOLDER_MIME_TYPE
        fs.mv("root/dir", "root/renamed")
        source.assert_called_once_with({"id": "first"})
        source.return_value.Upload.assert_called_once_with()
    assert not fs.exists("root/dir/nested")
    assert not fs.exists("root/dir")
    assert fs.exists("root/directory")
    assert set(fs._ids_cache["ids"]) == {"root", "unrelated"}


def test_invalidation_during_lookup(listing_fs, monkeypatch):
    fs = listing_fs

    def listing(*args, **kwargs):
        fs.invalidate_cache("root/dir")
        return [{"name": "dir", "id": "stale", "mimeType": FOLDER_MIME_TYPE}]

    monkeypatch.setattr(fs, "_gdrive_list_ids", listing)
    assert fs.exists("root/dir")
    assert "stale" not in fs._ids_cache["ids"]
    fs.invalidate_cache()
    assert fs._ids_cache["ids"] == {"root": ""}


def test_invalidating_parent_reloads_mount_on_next_lookup(mocker):
    fs = GDriveFileSystem(
        "root/parent/mount", google_auth=Mock(), skip_instance_cache=True
    )
    parent = {
        "id": "parent-id",
        "name": "parent",
        "mimeType": FOLDER_MIME_TYPE,
    }
    mount = {"id": "original", "name": "mount", "mimeType": FOLDER_MIME_TYPE}
    listing = mocker.patch.object(
        fs,
        "_gdrive_list_ids",
        side_effect=[
            [parent],
            [mount],
            [parent],
            [dict(mount, id="replacement")],
        ],
    )

    fs.invalidate_cache("root/parent")
    listing.assert_not_called()
    assert fs._get_item_id(fs.path) == "original"
    fs.invalidate_cache("root/parent")
    assert listing.call_count == 2
    assert fs._get_item_id(fs.path) == "replacement"


def test_cache_deduplicates_directory_ids(listing_fs):
    listing_fs._cache_path_id("dir", "a", "b", "a")
    listing_fs._cache_path_id("dir", "a")
    assert listing_fs._get_cached_item_ids("dir", True) == ["a", "b"]


def test_empty_path_invalidates_folder_mount(mocker):
    fs = GDriveFileSystem(
        "FolderID", google_auth=Mock(), skip_instance_cache=True
    )
    fs._cache_path_id("dir", "stale")
    fs.invalidate_cache(fs._parent("FolderID"))
    assert not fs._get_cached_item_ids("dir", True)


@pytest.mark.parametrize(
    "src,dst",
    [
        ("root/dir", "gdrive:///renamed"),
        ("root/sub/dir", "gdrive://"),
    ],
)
def test_move_directory_to_implicit_root(listing_fs, mocker, src, dst):
    fs = listing_fs
    _, base = fs.split_path(src)
    fs._cache_path_id(base, "moved-id")
    mocker.patch.object(fs, "_gdrive_list_ids", return_value=[])
    source = mocker.patch.object(fs.client, "CreateFile").return_value
    source.get.return_value = FOLDER_MIME_TYPE
    fs.mv(src, dst)
    source.Upload.assert_called_once_with()
    assert not fs._get_cached_item_ids(base, True)


def test_move_invalidates_cached_destination_even_after_source_eviction(
    listing_fs, mocker
):
    fs = listing_fs
    fs._cache_path_id("dir", "moving")
    fs._cache_path_id("dst", "destination")
    fs._cache_path_id("dst/dir", "sibling")
    fs._cache_path_id("dst/dir/child", "nested")
    source = mocker.patch.object(fs.client, "CreateFile").return_value
    source.Upload.side_effect = lambda: fs.invalidate_cache("root/dir")
    source.get.return_value = FOLDER_MIME_TYPE
    fs.mv("root/dir", "root/dst")
    assert fs._ids_cache["ids"] == {"root": "", "destination": "dst"}


@pytest.mark.parametrize("op", ["move", "remove"])
def test_file_mutations_preserve_directory_cache(listing_fs, mocker, op):
    fs = listing_fs
    cached = fs._ids_cache
    file = {"id": "blob", "name": "file", "mimeType": "text/plain"}
    mocker.patch.object(fs, "_gdrive_list_ids", return_value=[file])
    if op == "move":
        source = mocker.patch.object(fs.client, "CreateFile").return_value
        source.get.return_value = file["mimeType"]
        fs.mv("root/file", "root/renamed")
    else:
        mocker.patch.object(fs, "_gdrive_delete_file")
        fs.rm_file("root/file")
    assert fs._ids_cache is cached


def test_failed_removal_preserves_cache(listing_fs, mocker):
    listing_fs._cache_path_id("dir", "folder")
    mocker.patch.object(
        listing_fs, "_gdrive_delete_file", side_effect=PermissionError
    )
    with pytest.raises(PermissionError):
        listing_fs.rm_file("root/dir")
    assert listing_fs.exists("root/dir")
