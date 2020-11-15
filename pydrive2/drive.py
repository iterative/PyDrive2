from .apiattr import ApiAttributeMixin
from .files import GoogleDriveFile
from .files import GoogleDriveFileList
from .auth import LoadAuth


class GoogleDrive(ApiAttributeMixin, object):
    """Main Google Drive class."""

    def __init__(self, auth=None):
        """Create an instance of GoogleDrive.

    :param auth: authorized GoogleAuth instance.
    :type auth: pydrive2.auth.GoogleAuth.
    """
        ApiAttributeMixin.__init__(self)
        self.auth = auth

    def CreateFile(self, metadata=None):
        """Create an instance of GoogleDriveFile with auth of this instance.

    This method would not upload a file to GoogleDrive.

    :param metadata: file resource to initialize GoogleDriveFile with.
    :type metadata: dict.
    :returns: pydrive2.files.GoogleDriveFile -- initialized with auth of this
              instance.
    """
        return GoogleDriveFile(auth=self.auth, metadata=metadata)

    def ListFile(self, param=None):
        """Create an instance of GoogleDriveFileList with auth of this instance.

    This method will not fetch from Files.List().

    :param param: parameter to be sent to Files.List().
    :type param: dict.
    :returns: pydrive2.files.GoogleDriveFileList -- initialized with auth of
              this instance.
    """
        return GoogleDriveFileList(auth=self.auth, param=param)

    @LoadAuth
    def GetAbout(self):
        """Return information about the Google Drive of the auth instance.

    :returns: A dictionary of Google Drive information like user, usage, quota etc.
    """
        return self.auth.service.about().get().execute(http=self.http)
