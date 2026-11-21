import importlib.metadata

import fsspec
import pytest

from pydrive2.fs import GDriveFileSystem


def test_protocol_is_gdrive():
    assert GDriveFileSystem.protocol == "gdrive"


def test_fsspec_entry_point_registered():
    entry_points = importlib.metadata.entry_points(group="fsspec")
    gdrive = [ep for ep in entry_points if ep.name == "gdrive"]
    assert len(gdrive) == 1
    assert gdrive[0].value == "pydrive2.fs:GDriveFileSystem"


def test_registry_resolves_gdrive():
    assert fsspec.registry.get_filesystem_class("gdrive") is GDriveFileSystem


def test_filesystem_without_credentials_reports_them():
    with pytest.raises(ValueError, match="Specify credentials"):
        fsspec.filesystem("gdrive")


def test_path_defaults_to_drive_root():
    with pytest.raises(ValueError, match="Specify credentials"):
        GDriveFileSystem()
