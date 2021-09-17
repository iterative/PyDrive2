import posixpath
import secrets
import uuid
from concurrent import futures

import pytest
import fsspec
from pydrive2.auth import GoogleAuth
from pydrive2.fs import GDriveFileSystem
from pydrive2.test.test_util import settings_file_path, setup_credentials

TEST_GDRIVE_REPO_BUCKET = "root"


@pytest.fixture(scope="module")
def base_remote_dir():
    path = TEST_GDRIVE_REPO_BUCKET + "/" + str(uuid.uuid4())
    return path


@pytest.fixture
def remote_dir(base_remote_dir):
    return base_remote_dir + "/" + str(uuid.uuid4())


@pytest.fixture
def fs(tmpdir, base_remote_dir):
    setup_credentials()
    auth = GoogleAuth(settings_file_path("default.yaml", tmpdir / ""))
    auth.ServiceAuth()

    bucket, base = base_remote_dir.split("/", 1)
    fs = GDriveFileSystem(base_remote_dir, auth)
    fs._gdrive_create_dir("root", base)

    return fs


def test_info(fs, tmpdir, remote_dir):
    fs.touch(remote_dir + "/info/a.txt")
    fs.touch(remote_dir + "/info/b.txt")
    details = fs.info(remote_dir + "/info/a.txt")
    assert details["type"] == "file"
    assert details["name"] == remote_dir + "/info/a.txt"
    assert details["size"] == 0
    assert (
        details["checksum"] == fs.info(remote_dir + "/info/b.txt")["checksum"]
    )

    details = fs.info(remote_dir + "/info")
    assert details["type"] == "directory"
    assert details["name"] == remote_dir + "/info/"
    assert "checksum" not in details

    details = fs.info(remote_dir + "/info/")
    assert details["type"] == "directory"
    assert details["name"] == remote_dir + "/info/"


def test_move(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    initial_info = fs.info(remote_dir + "/a.txt")

    fs.move(remote_dir + "/a.txt", remote_dir + "/b.txt")
    secondary_info = fs.info(remote_dir + "/b.txt")

    assert not fs.exists(remote_dir + "/a.txt")
    assert fs.exists(remote_dir + "/b.txt")

    initial_info.pop("name")
    secondary_info.pop("name")
    assert initial_info == secondary_info


def test_rm(fs, remote_dir):
    fs.touch(remote_dir + "/a.txt")
    fs.rm(remote_dir + "/a.txt")
    assert not fs.exists(remote_dir + "/a.txt")

    fs.mkdir(remote_dir + "/dir")
    fs.touch(remote_dir + "/dir/a")
    fs.touch(remote_dir + "/dir/b")
    fs.mkdir(remote_dir + "/dir/c/")
    fs.touch(remote_dir + "/dir/c/a")
    fs.rm(remote_dir + "/dir", recursive=True)
    assert not fs.exists(remote_dir + "/dir/c/a")


def test_ls(fs, remote_dir):
    fs.mkdir(remote_dir + "dir/")
    files = set()
    for no in range(8):
        file = remote_dir + f"dir/test_{no}"
        fs.touch(file)
        files.add(file)

    assert set(fs.ls(remote_dir + "dir/")) == files

    dirs = fs.ls(remote_dir + "dir/", detail=True)
    expected = [fs.info(file) for file in files]

    def by_name(details):
        return details["name"]

    dirs.sort(key=by_name)
    expected.sort(key=by_name)

    assert dirs == expected


def test_find(fs, remote_dir):
    fs.mkdir(remote_dir + "/dir")

    files = [
        "a",
        "b",
        "c/a",
        "c/b",
        "c/d/a",
        "c/d/b",
        "c/d/c",
        "c/d/f/a",
        "c/d/f/b",
    ]
    files = [remote_dir + "/dir/" + file for file in files]
    dirnames = {posixpath.dirname(file) for file in files}

    for dirname in dirnames:
        fs.mkdir(dirname)

    for file in files:
        fs.touch(file)

    assert set(fs.find(remote_dir)) == set(files)

    find_results = fs.find(remote_dir, detail=True)
    info_results = [fs.info(file) for file in files]
    info_results = {content["name"]: content for content in info_results}
    assert find_results == info_results


def test_exceptions(fs, tmpdir, remote_dir):
    with pytest.raises(FileNotFoundError):
        with fs.open(remote_dir + "/a.txt"):
            ...

    with pytest.raises(FileNotFoundError):
        fs.copy(remote_dir + "/u.txt", remote_dir + "/y.txt")

    with pytest.raises(FileNotFoundError):
        fs.get_file(remote_dir + "/c.txt", tmpdir / "c.txt")


def test_open_rw(fs, remote_dir):
    data = b"dvc.org"

    with fs.open(remote_dir + "/a.txt", "wb") as stream:
        stream.write(data)

    with fs.open(remote_dir + "/a.txt") as stream:
        assert stream.read() == data


def test_concurrent_operations(fs, remote_dir):
    def create_random_file():
        name = secrets.token_hex(16)
        with fs.open(remote_dir + "/" + name, "w") as stream:
            stream.write(name)
        return name

    def read_random_file(name):
        with fs.open(remote_dir + "/" + name, "r") as stream:
            return stream.read()

    with futures.ThreadPoolExecutor() as executor:

        write_futures, _ = futures.wait(
            [executor.submit(create_random_file) for _ in range(64)],
            return_when=futures.ALL_COMPLETED,
        )
        write_names = {future.result() for future in write_futures}

        read_futures, _ = futures.wait(
            [executor.submit(read_random_file, name) for name in write_names],
            return_when=futures.ALL_COMPLETED,
        )
        read_names = {future.result() for future in read_futures}

        assert write_names == read_names


def test_put_file(fs, tmpdir, remote_dir):
    src_file = tmpdir / "a.txt"
    with open(src_file, "wb") as file:
        file.write(b"data")

    fs.put_file(src_file, remote_dir + "/a.txt")

    with fs.open(remote_dir + "/a.txt") as stream:
        assert stream.read() == b"data"


def test_get_file(fs, tmpdir, remote_dir):
    src_file = tmpdir / "a.txt"
    dest_file = tmpdir / "b.txt"

    with open(src_file, "wb") as file:
        file.write(b"data")

    fs.put_file(src_file, remote_dir + "/a.txt")
    fs.get_file(remote_dir + "/a.txt", dest_file)
    assert dest_file.read() == "data"


def test_get_file_callback(fs, tmpdir, remote_dir):
    src_file = tmpdir / "a.txt"
    dest_file = tmpdir / "b.txt"

    with open(src_file, "wb") as file:
        file.write(b"data" * 10)

    fs.put_file(src_file, remote_dir + "/a.txt")
    callback = fsspec.Callback()
    fs.get_file(
        remote_dir + "/a.txt", dest_file, callback=callback, block_size=10
    )
    assert dest_file.read() == "data" * 10

    assert callback.size == 40
    assert callback.value == 40
