import subprocess

from PyInstaller import __main__ as pyi_main


# NOTE: importlib.resources.contents is available in py3.7+, but due to how
# pyinstaller handles importlib, we need to use the importlib_resources
# backport if there are any resources methods that are not available in a given
# python version, which ends up being py<3.10
_APP_SOURCE = """
import sys
if sys.version_info >= (3, 10):
    from importlib.resources import contents
else:
    from importlib_resources import contents

import pydrive2.files


cache_files = contents(
    "googleapiclient.discovery_cache.documents"
)
assert len(cache_files) > 0
"""


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
