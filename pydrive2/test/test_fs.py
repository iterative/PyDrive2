from io import StringIO
import os
import posixpath
import secrets
import uuid
from concurrent import futures

import pytest
import fsspec
from pydrive2.auth import GoogleAuth
from pydrive2.fs import GDriveFileSystem
from pydrive2.test.test_util import settings_file_path, setup_credentials
from pydrive2.test.test_util import GDRIVE_USER_CREDENTIALS_DATA

TEST_GDRIVE_REPO_BUCKET = "root"


@pytest.fixture(scope="module")
def base_remote_dir():
    path = TEST_GDRIVE_REPO_BUCKET + "/" + str(uuid.uuid4())
    return path


@pytest.fixture
def remote_dir(base_remote_dir):
    return base_remote_dir + "/" + str(uuid.uuid4())


@pytest.fixture(scope="module")
def service_auth(tmp_path_factory):
    setup_credentials()
    tmpdir = tmp_path_factory.mktemp("settings")
    auth = GoogleAuth(settings_file_path("default.yaml", wkdir=tmpdir))
    auth.ServiceAuth()
    return auth


@pytest.fixture(scope="module")
def fs_factory(base_remote_dir, service_auth):
    base_item = None
    GDriveFileSystem.cachable = False

    def _create_fs():
        nonlocal base_item
        _, base = base_remote_dir.split("/", 1)
        fs = GDriveFileSystem(base_remote_dir, service_auth)
        if base_item is None:
            base_item = fs._gdrive_create_dir("root", base)

        return fs, base_item

    yield _create_fs

    GDriveFileSystem.cachable = True
    fs = GDriveFileSystem(base_remote_dir, service_auth)
    fs.rm_file(base_remote_dir)


@pytest.fixture
def fs(fs_factory):
    return fs_factory()[0]


@pytest.mark.manual
def test_fs_oauth(base_remote_dir):
    GDriveFileSystem(
        base_remote_dir,
        client_id="47794215776-cd9ssb6a4vv5otkq6n0iadpgc4efgjb1.apps.googleusercontent.com",  # noqa: E501
        client_secret="i2gerGA7uBjZbR08HqSOSt9Z",
    )


def test_fs_service_json_file(base_remote_dir):
    creds = "credentials/fs.dat"
    setup_credentials(creds)
    GDriveFileSystem(
        base_remote_dir,
        use_service_account=True,
        client_json_file_path=creds,
    )


def test_fs_service_json(base_remote_dir):
    creds = os.environ[GDRIVE_USER_CREDENTIALS_DATA]
    GDriveFileSystem(
        base_remote_dir,
        use_service_account=True,
        client_json=creds,
    )


def test_info(fs, remote_dir):
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
    _, base = fs.split_path(remote_dir + "/dir/")
    fs._path_to_item_ids(base, create=True)
    assert fs.ls(remote_dir + "/dir/") == []

    files = set()
    for no in range(8):
        file = remote_dir + f"/dir/test_{no}"
        fs.touch(file)
        files.add(file)

    assert set(fs.ls(remote_dir + "/dir/")) == files

    dirs = fs.ls(remote_dir + "/dir/", detail=True)
    expected = [fs.info(file) for file in files]

    def by_name(details):
        return details["name"]

    dirs.sort(key=by_name)
    expected.sort(key=by_name)

    assert dirs == expected


def test_basic_ops_caching(fs_factory, remote_dir, mocker):
    # Internally we have to derefence names into IDs to call GDrive APIs
    # we are trying hard to cache those and make sure that operations like
    # exists, ls, find, etc. don't hit the API more than once per path

    # ListFile (_gdrive_list) is the main operation that we use to retieve file
    # metadata in all operations like find/ls/exist - etc. It should be fine as
    # a basic benchmark to count those.
    # Note: we can't count direct API calls since we have retries, also can't
    # count even direct calls to the GDrive client - for the same reason
    fs, _ = fs_factory()
    spy = mocker.spy(fs, "_gdrive_list")

    dir_path = remote_dir + "/a/b/c/"
    file_path = dir_path + "test.txt"
    fs.touch(file_path)

    assert spy.call_count == 5
    spy.reset_mock()

    fs.exists(file_path)
    assert spy.call_count == 1
    spy.reset_mock()

    fs.ls(remote_dir)
    assert spy.call_count == 1
    spy.reset_mock()

    fs.ls(dir_path)
    assert spy.call_count == 1
    spy.reset_mock()

    fs.find(dir_path)
    assert spy.call_count == 1
    spy.reset_mock()

    fs.find(remote_dir)
    assert spy.call_count == 1
    spy.reset_mock()


def test_ops_work_with_duplicate_names(fs_factory, remote_dir):
    fs, base_item = fs_factory()

    remote_dir_item = fs._gdrive_create_dir(
        base_item["id"], remote_dir.split("/")[-1]
    )
    dir_name = str(uuid.uuid4())
    dir1 = fs._gdrive_create_dir(remote_dir_item["id"], dir_name)
    dir2 = fs._gdrive_create_dir(remote_dir_item["id"], dir_name)

    # Two directories were created with the same name
    assert dir1["id"] != dir2["id"]

    dir_path = remote_dir + "/" + dir_name + "/"

    # ls returns both of them, even though the names are the same
    test_fs = fs
    result = test_fs.ls(remote_dir)
    assert len(result) == 2
    assert set(result) == {dir_path}

    # ls returns both of them, even though the names are the same
    test_fs, _ = fs_factory()
    result = test_fs.ls(remote_dir)
    assert len(result) == 2
    assert set(result) == {dir_path}

    for test_fs in [fs, fs_factory()[0]]:
        # find by default doesn't return dirs at all
        result = test_fs.find(remote_dir)
        assert len(result) == 0

    fs._gdrive_upload_fobj("a.txt", dir1["id"], StringIO(""))
    fs._gdrive_upload_fobj("b.txt", dir2["id"], StringIO(""))

    for test_fs in [fs, fs_factory()[0]]:
        # now we should have both files
        result = test_fs.find(remote_dir)
        assert len(result) == 2
        assert set(result) == {dir_path + file for file in ["a.txt", "b.txt"]}


def test_ls_non_existing_dir(fs, remote_dir):
    with pytest.raises(FileNotFoundError):
        fs.ls(remote_dir + "dir/")


def test_find(fs, fs_factory, remote_dir):
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

    for test_fs in [fs, fs_factory()[0]]:
        # Test for https://github.com/iterative/PyDrive2/issues/229
        # It must go first, so that we test with a cache miss as well
        assert set(test_fs.find(remote_dir + "/dir/c/d/")) == set(
            [
                file
                for file in files
                if file.startswith(remote_dir + "/dir/c/d/")
            ]
        )

        # General find test
        assert set(test_fs.find(remote_dir)) == set(files)

        find_results = test_fs.find(remote_dir, detail=True)
        info_results = [test_fs.info(file) for file in files]
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


def test_concurrent_operations(fs, fs_factory, remote_dir):
    # Include an extra dir name to force upload operations creating it
    # this way we can also test that only a single directory is created
    # enven if multiple threads are uploading files into the same dir
    dir_name = secrets.token_hex(16)

    def create_random_file():
        name = secrets.token_hex(16)
        with fs.open(remote_dir + f"/{dir_name}/" + name, "w") as stream:
            stream.write(name)
        return name

    def read_random_file(name):
        with fs.open(remote_dir + f"/{dir_name}/" + name, "r") as stream:
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

    # Test that only a single dir is cretead
    for test_fs in [fs, fs_factory()[0]]:
        results = test_fs.ls(remote_dir)
        assert results == [remote_dir + f"/{dir_name}/"]


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
