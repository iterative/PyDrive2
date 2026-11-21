import os
import sys

assert sys.platform == "win32"

import msvcrt

import pywintypes
import win32con
import win32file
import win32security
import winerror

from ._credentials import InvalidCredentialsError


def _fileno(handle, flags):
    fd = msvcrt.open_osfhandle(int(handle), flags)
    handle.Detach()  # The CRT descriptor now owns the Windows handle.
    return fd


def open_for_read(path):
    try:
        handle = win32file.CreateFile(
            str(path),
            win32con.GENERIC_READ,
            win32con.FILE_SHARE_READ | win32con.FILE_SHARE_DELETE,
            None,
            win32con.OPEN_EXISTING,
            win32file.FILE_FLAG_OPEN_REPARSE_POINT,
            None,
        )
    except pywintypes.error as exc:
        if exc.winerror in (
            winerror.ERROR_FILE_NOT_FOUND,
            winerror.ERROR_PATH_NOT_FOUND,
        ):
            raise FileNotFoundError(path) from exc
        raise
    attributes = win32file.GetFileInformationByHandle(handle)[0]
    if attributes & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
        handle.Close()
        raise InvalidCredentialsError(
            "Credentials file cannot be symbolic link"
        )
    return _fileno(handle, os.O_RDONLY)


def create_for_replace(path, temporary):
    # Apply the DACL at creation, before another process can open the file.
    if path.exists():
        descriptor = win32security.GetFileSecurity(
            str(path), win32security.DACL_SECURITY_INFORMATION
        )
        protected = win32security.SE_DACL_PROTECTED
        descriptor.SetSecurityDescriptorControl(protected, protected)
    else:
        descriptor = (
            win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
                "D:P(A;;FA;;;OW)", win32security.SDDL_REVISION_1
            )
        )
    security = pywintypes.SECURITY_ATTRIBUTES()
    security.SECURITY_DESCRIPTOR = descriptor
    security.bInheritHandle = False
    handle = win32file.CreateFile(
        str(temporary),
        win32con.GENERIC_WRITE,
        0,
        security,
        win32con.CREATE_NEW,
        win32con.FILE_ATTRIBUTE_NORMAL,
        None,
    )
    return _fileno(handle, os.O_WRONLY)
