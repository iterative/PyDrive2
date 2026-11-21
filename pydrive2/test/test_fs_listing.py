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
        return iter(parents)

    monkeypatch.setattr(listing_fs, "_gdrive_list", list_query)

    assert list(listing_fs._gdrive_list_ids(container(ids))) == ids
    assert listed == ids


def test_list_ids_lazy(listing_fs, monkeypatch):
    consumed = []

    def parents():
        for index in range(51):
            consumed.append(index)
            yield str(index)

    list_query = Mock(side_effect=[iter(["page-1", "page-2"]), iter(["last"])])
    monkeypatch.setattr(listing_fs, "_gdrive_list", list_query)
    result = listing_fs._gdrive_list_ids(parents())
    assert consumed == []
    list_query.assert_not_called()

    assert next(result) == "page-1"
    assert len(consumed) == 50
    assert next(result) == "page-2"
    assert list_query.call_count == 1
    assert list(result) == ["last"]
    assert len(consumed) == 51
    assert list_query.call_count == 2


def test_ls_repeated_cached_parent_across_batches(listing_fs, monkeypatch):
    parents = [f"id-{i}" for i in range(51)]
    listing_fs._cache_path_id("duplicate", *parents, parents[0])
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
            return [{"name": "late-file", "mimeType": "text/plain"}]
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
