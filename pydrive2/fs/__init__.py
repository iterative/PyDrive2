try:
    from pydrive2.fs.spec import GDriveFileSystem
except ModuleNotFoundError as exc:
    if exc.name != "fsspec":
        raise
    raise ImportError(
        "Install filesystem support with pip install 'PyDrive2[fsspec]'."
    ) from exc

__all__ = ["GDriveFileSystem"]
