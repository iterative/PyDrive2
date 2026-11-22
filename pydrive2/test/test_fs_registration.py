import errno
import io
import json
import subprocess
import sys
from urllib.parse import urlsplit

import fsspec
import pytest

from pydrive2.files import ApiRequestError
from pydrive2.fs import GDriveFileSystem
from pydrive2.fs.spec import FOLDER_MIME_TYPE
from pydrive2.test.http_mock import error_response, query, response


def test_fsspec_discovers_backend_without_eager_import():
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import fsspec, sys\n"
                "assert 'pydrive2.fs' not in sys.modules\n"
                "assert 'googleapiclient.discovery' not in sys.modules\n"
                "cls = fsspec.get_filesystem_class('gdrive')\n"
                "from pydrive2.fs import GDriveFileSystem\n"
                "assert cls is GDriveFileSystem"
            ),
        ],
        check=True,
    )


def test_filesystem_factory(mock_drive):
    with pytest.raises(ValueError, match="Specify credentials"):
        fsspec.filesystem("gdrive")
    drive, requests = mock_drive()
    fs = fsspec.filesystem("gdrive", google_auth=drive.auth)
    assert (fs.root, fs.base) == ("root", "")
    mounted = GDriveFileSystem("FoLdEr/sub", google_auth=drive.auth)
    assert (mounted.root, mounted.base) == ("FoLdEr", "sub")
    assert not requests


def test_open_by_file_id(mock_drive):
    drive, requests = mock_drive(({"status": "200"}, b"contents"))
    with fsspec.open("gdrive://FiLe_ID", google_auth=drive.auth) as stream:
        assert stream.read() == b"contents"
    (download,) = requests
    assert urlsplit(download[0]).path == "/drive/v3/files/FiLe_ID"
    assert query(download)["alt"] == ["media"]


def test_open_files_reuses_folder_lookup(mock_drive):
    def entry(name, item_id, mime_type="text/plain"):
        return response(
            {"files": [{"name": name, "id": item_id, "mimeType": mime_type}]}
        )

    drive, requests = mock_drive(
        response({"driveId": "shared"}),
        entry("sub", "sub-ID", FOLDER_MIME_TYPE),
        entry("a", "file-a"),
        ({"status": "200"}, b"one"),
        entry("b", "file-b"),
        ({"status": "200"}, b"two"),
        response({"files": []}),
    )
    prefix = "gdrive://FoLdEr/sub"
    options = {"google_auth": drive.auth}
    first, second = fsspec.open_files(
        [prefix + "/a", prefix + "/b"], **options
    )
    assert fsspec.open(prefix + "/b", **options).fs is first.fs is second.fs
    assert not requests
    with first as a, second as b:
        assert (a.read(), b.read()) == (b"one", b"two")
    assert first.fs.ls(prefix) == []
    shared, sub, a_lookup, _, b_lookup, _, listing = requests
    assert urlsplit(shared[0]).path.endswith("/FoLdEr")
    assert "'FoLdEr' in parents" in query(sub)["q"][0]
    assert all(
        "'sub-ID' in parents" in query(r)["q"][0]
        for r in (a_lookup, b_lookup, listing)
    )
    assert query(listing)["driveId"] == ["shared"]


def test_missing_file_id(mock_drive):
    drive, requests = mock_drive(error_response(404, "notFound"))
    fs, path = fsspec.core.url_to_fs(
        "gdrive://Missing", google_auth=drive.auth
    )
    assert not fs.exists(path)
    assert len(requests) == 1
    with pytest.raises(ValueError, match="folder ID and name"):
        fs.open(path, "wb")
    with pytest.raises(ValueError, match="outside Drive root"):
        fs.ls("gdrive://Other/sub")


@pytest.mark.parametrize(
    "url",
    [
        "gdrive://",
        "gdrive://root",
        "gdrive://FoLdEr/",
        "gdrive://appDataFolder",
    ],
)
def test_writes_require_filename(mock_drive, tmp_path, url):
    drive, requests = mock_drive()
    fs, path = fsspec.core.url_to_fs(url, google_auth=drive.auth)
    local = tmp_path / "input"
    local.write_bytes(b"content")
    for destination in (url, path):
        with pytest.raises(ValueError, match="folder ID and name"):
            fs.open(destination, "wb")
        with pytest.raises(ValueError, match="folder ID and name"):
            fs.upload_fobj(io.BytesIO(), destination)
        with pytest.raises(ValueError, match="folder ID and name"):
            fs.put_file(local, destination)
        with pytest.raises(ValueError, match="folder ID and name"):
            fs.cp_file(f"{fs.root}/source", destination)
    assert not requests


def test_copy_to_implicit_root(mock_drive):
    source = {"name": "source", "id": "source-id", "mimeType": "text/plain"}
    drive, requests = mock_drive(
        response({"files": [source]}), response({"id": "copy"})
    )
    fs = fsspec.filesystem("gdrive", google_auth=drive.auth)
    fs.cp_file("root/source", "gdrive:///copy")
    _, copy = requests
    assert json.loads(copy[2]) == {"name": "copy", "parents": ["root"]}


@pytest.mark.parametrize("method", ["info", "open", "get_file"])
@pytest.mark.parametrize(
    "status,reason", [(404, "notFound"), (403, "forbidden")]
)
def test_file_id_errors(mock_drive, tmp_path, method, status, reason):
    drive, requests = mock_drive(error_response(status, reason))
    fs, path = fsspec.core.url_to_fs("gdrive://FiLe", google_auth=drive.auth)
    args = {"info": (), "open": ("rb",), "get_file": (tmp_path / "dest",)}
    expected = FileNotFoundError if status == 404 else ApiRequestError
    with pytest.raises(expected) as error:
        getattr(fs, method)(path, *args[method])
    if status == 404:
        assert error.value.errno == errno.ENOENT
        assert error.value.filename == path
        assert isinstance(error.value.__cause__, ApiRequestError)
    else:
        assert error.value.error["code"] == status
    assert len(requests) == 1
    assert not (tmp_path / "dest").exists()


def test_trashed_file_id_is_absent_from_metadata(mock_drive):
    trashed = {"mimeType": "text/plain", "trashed": True}
    drive, requests = mock_drive(
        response({"trashed": True}), response(trashed), response(trashed)
    )
    fs, path = fsspec.core.url_to_fs("gdrive://FiLe", google_auth=drive.auth)
    fs.rm_file(path)
    assert not fs.exists(path)
    with pytest.raises(FileNotFoundError) as error:
        fs.info(path)
    assert error.value.filename == path
    trash, *metadata = requests
    assert trash[1] == "PATCH"
    assert json.loads(trash[2]) == {"trashed": True}
    assert all(
        "trashed" in query(req)["fields"][0].split(",") for req in metadata
    )
