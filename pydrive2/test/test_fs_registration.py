import subprocess
import sys
from urllib.parse import urlsplit

import fsspec
import pytest

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
                "cls = fsspec.get_filesystem_class('pydrive2')\n"
                "from pydrive2.fs import GDriveFileSystem\n"
                "assert cls is GDriveFileSystem"
            ),
        ],
        check=True,
    )


def test_filesystem_factory(mock_drive):
    with pytest.raises(ValueError, match="Specify credentials"):
        fsspec.filesystem("pydrive2")
    drive, requests = mock_drive()
    fs = fsspec.filesystem("pydrive2", google_auth=drive.auth)
    assert (fs.root, fs.base) == ("root", "")
    mounted = GDriveFileSystem("FoLdEr/sub", google_auth=drive.auth)
    assert (mounted.root, mounted.base) == ("FoLdEr", "sub")
    assert not requests


def test_open_by_file_id(mock_drive):
    drive, requests = mock_drive(({"status": "200"}, b"contents"))
    with fsspec.open("pydrive2://FiLe_ID", google_auth=drive.auth) as stream:
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
    prefix = "pydrive2://FoLdEr/sub"
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
        "pydrive2://Missing", google_auth=drive.auth
    )
    assert not fs.exists(path)
    assert len(requests) == 1
    with pytest.raises(ValueError, match="folder ID and name"):
        fs.open(path, "wb")
    with pytest.raises(ValueError, match="outside Drive root"):
        fs.ls("pydrive2://Other/sub")


@pytest.mark.parametrize("mode", ["wb", "wt"])
def test_open_writes_once_on_close(mock_drive, monkeypatch, mode):
    drive, _ = mock_drive()
    uploads = []

    def upload(fs, stream, path):
        uploads.append((path, stream.read()))

    monkeypatch.setattr(GDriveFileSystem, "upload_fobj", upload)
    monkeypatch.setattr(GDriveFileSystem, "blocksize", 3)
    with fsspec.open(
        "pydrive2://root/new", mode, google_auth=drive.auth
    ) as writer:
        value = b"data" if mode == "wb" else "data"
        assert writer.write(value) == 4
        writer.flush()
        assert not uploads
        assert writer.write(value) == 4
    assert uploads == [("root/new", b"datadata")]
