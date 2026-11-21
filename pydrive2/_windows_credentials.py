import os
import sys

assert sys.platform == "win32"

import msvcrt

import pywintypes
import win32con
import win32file
import win32security


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
    fd = msvcrt.open_osfhandle(int(handle), os.O_WRONLY)
    handle.Detach()  # The CRT descriptor now owns the Windows handle.
    return fd
