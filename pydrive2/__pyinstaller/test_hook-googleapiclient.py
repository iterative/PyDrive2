import subprocess

from PyInstaller import __main__ as pyi_main


_APP_SOURCE = """import importlib.resources

import pydrive2.files


cache_files = importlib.resources.contents(
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
