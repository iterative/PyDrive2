import re
from unittest.mock import Mock

import pytest

from pydrive2.fs import GDriveFileSystem


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
