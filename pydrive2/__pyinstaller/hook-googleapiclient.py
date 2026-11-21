from PyInstaller.utils.hooks import (  # pylint: disable=import-error
    collect_data_files,
    copy_metadata,
)

datas = copy_metadata("google-api-python-client")
datas += collect_data_files(
    "googleapiclient", excludes=["*.txt", "**/__pycache__"]
)
