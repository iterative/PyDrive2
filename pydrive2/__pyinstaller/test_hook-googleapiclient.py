import subprocess
import sys

import pytest
from PyInstaller import __main__ as pyi_main

_APP_SOURCE = """
from importlib.resources import files

import pydrive2.files


documents = files("googleapiclient.discovery_cache.documents")
assert documents.joinpath("drive.v3.json").is_file()
"""


# TODO: Remove this skip once the PyInstaller fix is verified in #399.
@pytest.mark.skipif(
    sys.platform == "win32" and sys.version_info[:2] == (3, 15),
    reason="PyInstaller omits python3t.dll on Windows with Python 3.15",
)
def test_pyi_hook_google_api_client(tmp_path):
    app_name = "userapp"
    workpath = tmp_path / "build"
    distpath = tmp_path / "dist"
    app = tmp_path / f"{app_name}.py"
    app.write_text(_APP_SOURCE)
    pyi_main.run(
        [
            "--workpath",
            str(workpath),
            "--distpath",
            str(distpath),
            "--specpath",
            str(tmp_path),
            str(app),
        ],
    )
    subprocess.run([str(distpath / app_name / app_name)], check=True)
