import re

import pytest

from pydrive2.fs import GDriveFileSystem
from pydrive2.fs.spec import FOLDER_MIME_TYPE
from pydrive2.test.http_mock import query, response


def folders(count):
    return [
        {"id": f"dir-{i}", "name": "dup", "mimeType": FOLDER_MIME_TYPE}
        for i in range(count)
    ]


def child(parent):
    return {
        "id": f"file-{parent}",
        "name": parent,
        "mimeType": "text/plain",
        "parents": [parent],
    }


@pytest.mark.parametrize("count", [0, 1, 50, 51, 100, 251])
def test_ls_queries_each_duplicate_folder_once_in_batches(mock_drive, count):
    roots = folders(count)
    ids = [item["id"] for item in roots]
    batches = [ids[i : i + 50] for i in range(0, count, 50)]
    drive, requests = mock_drive(
        response({"files": roots}),
        *(response({"files": [child(i) for i in batch]}) for batch in batches),
    )
    fs = GDriveFileSystem(google_auth=drive.auth, skip_instance_cache=True)
    if count:
        assert fs.ls("root/dup") == [f"root/dup/{i}" for i in ids]
    else:
        with pytest.raises(FileNotFoundError):
            fs.ls("root/dup")
    assert "name='dup'" in query(requests[0])["q"][0]
    assert [
        re.findall(r"'([^']+)' in parents", query(request)["q"][0])
        for request in requests[1:]
    ] == batches


@pytest.mark.parametrize("complete", [True, False])
def test_ls_finishes_each_batch_before_requesting_the_next(
    mock_drive, complete
):
    roots = folders(51)
    drive, requests = mock_drive(
        response({"files": roots}),
        response({"files": [child("dir-0")], "nextPageToken": "second"}),
        response(
            {"files": [child("dir-1")], "incompleteSearch": not complete}
        ),
        response({"files": [child("dir-50")]}),
    )
    fs = GDriveFileSystem(google_auth=drive.auth, skip_instance_cache=True)
    if complete:
        assert fs.ls("root/dup") == [
            "root/dup/dir-0",
            "root/dup/dir-1",
            "root/dup/dir-50",
        ]
        assert query(requests[3])["q"] == [
            "('dir-50' in parents) and trashed=false"
        ]
    else:
        with pytest.raises(OSError, match="Incomplete Drive listing"):
            fs.ls("root/dup")
        assert len(requests) == 3
    assert query(requests[2])["q"] == query(requests[1])["q"]
    assert query(requests[2])["pageToken"] == ["second"]
