import pytest

from pydrive2.fs import GDriveFileSystem
from pydrive2.fs.spec import FOLDER_MIME_TYPE
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
