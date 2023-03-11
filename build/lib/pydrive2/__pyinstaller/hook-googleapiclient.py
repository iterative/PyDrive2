from PyInstaller.utils.hooks import (  # pylint: disable=import-error
    copy_metadata,
    collect_data_files,
)

datas = copy_metadata("google-api-python-client")
datas += collect_data_files(
    "googleapiclient", excludes=["*.txt", "**/__pycache__"]
)
