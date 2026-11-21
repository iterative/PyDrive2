import io
import json
import mimetypes
import os
from functools import wraps

from googleapiclient import errors
from googleapiclient.http import (
    DEFAULT_CHUNK_SIZE,
    MediaIoBaseDownload,
    MediaIoBaseUpload,
)

from .apiattr import (
    ApiAttribute,
    ApiAttributeMixin,
    ApiResource,
    ApiResourceList,
)
from .auth import LoadAuth

DEFAULT_FILE_FIELDS = (
    "id,name,mimeType,description,parents,trashed,size,md5Checksum,"
    "resourceKey,webViewLink,createdTime,modifiedTime"
)

BLOCK_SIZE = 1024
# Usage: MIME_TYPE_TO_BOM['<Google Drive mime type>']['<download mimetype>'].
MIME_TYPE_TO_BOM = {
    "application/vnd.google-apps.document": {"text/plain": "\ufeff".encode()}
}


class FileNotUploadedError(RuntimeError):
    """Error trying to access metadata of file that is not uploaded."""


class ApiRequestError(IOError):
    def __init__(self, http_error):
        assert isinstance(http_error, errors.HttpError)
        content = json.loads(http_error.content.decode("utf-8"))
        self.error = content.get("error", {}) if content else {}

        # Initialize args for backward compatibility
        super().__init__(http_error)

    def GetField(self, field):
        """Returns the `field` from the first error"""
        return self.error.get("errors", [{}])[0].get(field, "")


class FileNotDownloadableError(RuntimeError):
    """Error trying to download file that is not downloadable."""


def LoadMetadata(decoratee):
    """Decorator to check if the file has metadata and fetches it if not.

    :raises: ApiRequestError, FileNotUploadedError
    """

    @wraps(decoratee)
    def _decorated(self, *args, **kwargs):
        if not self.uploaded:
            self.FetchMetadata()
        return decoratee(self, *args, **kwargs)

    return _decorated


class GoogleDriveFileList(ApiResourceList):
    """Google Drive FileList instance.

    Equivalent to Files.list() in Drive APIs.
    """

    def __init__(self, auth=None, param=None):
        """Create an instance of GoogleDriveFileList."""
        super().__init__(auth=auth, metadata=param)

    @LoadAuth
    def _GetList(self):
        """Overwritten method which actually makes API call to list files.

        :returns: list -- list of pydrive2.files.GoogleDriveFile.
        """
        params = dict(
            self, supportsAllDrives=True, includeItemsFromAllDrives=True
        )
        fields = params.get("fields") or f"files({DEFAULT_FILE_FIELDS})"
        # Keep the cursor even when the caller selects only file fields.
        if fields.strip() != "*" and "nextPageToken" not in fields.replace(
            " ", ""
        ).split(","):
            fields = f"nextPageToken,{fields}"
        params["fields"] = fields
        try:
            self.metadata = (
                self.auth.service.files()
                .list(**params)
                .execute(http=self.http)
            )
        except errors.HttpError as error:
            raise ApiRequestError(error)

        return [
            GoogleDriveFile(self.auth, metadata, uploaded=True)
            for metadata in self.metadata.get("files", [])
        ]


class IoBuffer:
    """Lightweight retention of one chunk."""

    def __init__(self, encoding):
        self.encoding = encoding
        self.chunk = None

    def write(self, chunk):
        self.chunk = chunk

    def read(self):
        return (
            self.chunk.decode(self.encoding)
            if self.chunk and self.encoding
            else self.chunk
        )


class MediaIoReadable:
    def __init__(
        self,
        request,
        encoding=None,
        pre_buffer=True,
        remove_prefix=b"",
        chunksize=DEFAULT_CHUNK_SIZE,
    ):
        """File-like wrapper around MediaIoBaseDownload.

        :param pre_buffer: Whether to read one chunk into an internal buffer
        immediately in order to raise any potential errors.
        :param remove_prefix: Bytes prefix to remove from internal pre_buffer.
        :raises: ApiRequestError
        """
        self.done = False
        self._fd = IoBuffer(encoding)
        self.downloader = MediaIoBaseDownload(
            self._fd, request, chunksize=chunksize
        )
        self.size = None
        self._pre_buffer = False
        if pre_buffer:
            self.read()
            if remove_prefix:
                chunk = io.BytesIO(self._fd.chunk or b"")
                GoogleDriveFile._RemovePrefix(chunk, remove_prefix)
                self._fd.chunk = chunk.getvalue()
            self._pre_buffer = True

    def read(self):
        """
        :returns: bytes or str -- chunk (or None if done)
        :raises: ApiRequestError
        """
        if self._pre_buffer:
            self._pre_buffer = False
            return self._fd.read()
        if self.done:
            return None
        try:
            status, self.done = self.downloader.next_chunk()
            self.size = status.total_size
        except errors.HttpError as error:
            raise ApiRequestError(error)
        return self._fd.read()

    def __iter__(self):
        """
        :raises: ApiRequestError
        """
        while True:
            chunk = self.read()
            if chunk is None:
                break
            yield chunk

    def __len__(self):
        return self.size


class GoogleDriveFile(ApiAttributeMixin, ApiResource):
    """Google Drive File instance.

    Inherits ApiResource which inherits dict.
    Can access and modify metadata like dictionary.
    """

    content = ApiAttribute("content")
    uploaded = ApiAttribute("uploaded")
    metadata = ApiAttribute("metadata")

    def __init__(self, auth=None, metadata=None, uploaded=False):
        """Create an instance of GoogleDriveFile.

        :param auth: authorized GoogleAuth instance.
        :type auth: pydrive2.auth.GoogleAuth
        :param metadata: file resource to initialize GoogleDriveFile with.
        :type metadata: dict.
        :param uploaded: True if this file is confirmed to be uploaded.
        :type uploaded: bool.
        """
        ApiAttributeMixin.__init__(self)
        ApiResource.__init__(self)
        self.metadata = {}
        self.dirty = {"content": False}
        self.auth = auth
        self.uploaded = uploaded
        if uploaded:
            self.UpdateMetadata(metadata)
        elif metadata:
            self.update(metadata)
        self.has_bom = True
        self._export_mimetype = None

    def __getitem__(self, key):
        """Overwrites manner of accessing Files resource.

        If this file instance is not uploaded and id is specified,
        it will try to look for metadata with Files.get().

        :param key: key of dictionary query.
        :type key: str.
        :returns: value of Files resource
        :raises: KeyError, FileNotUploadedError
        """
        try:
            return dict.__getitem__(self, key)
        except KeyError as e:
            if self.uploaded:
                raise KeyError(e)
            if self.get("id"):
                self.FetchMetadata()
                return dict.__getitem__(self, key)
            else:
                raise FileNotUploadedError()

    def SetContentString(self, content, encoding="utf-8"):
        """Set content of this file to be a string.

        Creates io.BytesIO instance of utf-8 encoded string.
        Sets mimeType to be 'text/plain' if not specified and file id is not
        set (means that we are uploading this file for the first time).

        :param encoding: The encoding to use when setting the content of this file.
        :type encoding: str
        :param content: content of the file in string.
        :type content: str
        """
        self._export_mimetype = None
        self.has_bom = True
        self.content = io.BytesIO(content.encode(encoding))
        if self.get("mimeType") is None and self.get("id") is None:
            self["mimeType"] = "text/plain"

    def SetContentFile(self, filename):
        """Set content of this file from a file.

        Opens the file specified by this method.
        Will be read, uploaded, and closed by Upload() method.
        Sets metadata 'name' and 'mimeType' automatically if not specified and
        the file is uploaded for the first time (id is not set).

        :param filename: name of the file to be uploaded.
        :type filename: str.
        """
        self.content = open(filename, "rb")  # noqa: SIM115 - retained for upload
        if self.get("id") is None:
            if self.get("name") is None:
                self["name"] = os.path.basename(filename)
            if self.get("mimeType") is None:
                self["mimeType"] = (
                    mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                )

    def GetContentString(
        self, mimetype=None, encoding="utf-8", remove_bom=False
    ):
        """Get content of this file as a string.

        :param mimetype: The mimetype of the content string.
        :type mimetype: str

        :param encoding: The encoding to use when decoding the byte string.
        :type encoding: str

        :param remove_bom: Whether to strip a known BOM.
        :type remove_bom: bool

        :returns: str -- utf-8 decoded content of the file
        :raises: ApiRequestError, FileNotUploadedError, FileNotDownloadableError
        """
        if (
            self.content is None
            or not isinstance(self.content, io.BytesIO)
            or (self.has_bom == remove_bom and not self.dirty["content"])
            or (
                self._export_mimetype != mimetype and not self.dirty["content"]
            )
        ):
            self.FetchContent(mimetype, remove_bom)
        return self.content.getvalue().decode(encoding)

    @LoadAuth
    def GetContentFile(
        self,
        filename,
        mimetype=None,
        remove_bom=False,
        callback=None,
        chunksize=DEFAULT_CHUNK_SIZE,
        acknowledge_abuse=False,
    ):
        """Save content of this file as a local file.

        :param filename: name of the file to write to.
        :type filename: str
        :param mimetype: mimeType of the file.
        :type mimetype: str
        :param remove_bom: Whether to remove the byte order marking.
        :type remove_bom: bool
        :param callback: passed two arguments: (total transferred, file size).
        :type param: callable
        :param chunksize: chunksize in bytes (standard 100 MB(1024*1024*100))
        :type chunksize: int
        :param acknowledge_abuse: Acknowledging the risk and download file
            identified as abusive.
        :type acknowledge_abuse: bool
        :raises: ApiRequestError, FileNotUploadedError
        """
        files = self.auth.service.files()
        file_id = self.metadata.get("id") or self.get("id")
        if not file_id:
            raise FileNotUploadedError()

        def download(fd, request):
            downloader = MediaIoBaseDownload(
                fd, self._WrapRequest(request), chunksize=chunksize
            )
            done = False
            while done is False:
                status, done = downloader.next_chunk()
                if callback:
                    callback(status.resumable_progress, status.total_size)

        with open(filename, mode="w+b") as fd:
            # Should use files.export_media instead of files.get_media if
            # metadata["mimeType"].startswith("application/vnd.google-apps.").
            # But that would first require a slow call to FetchMetadata().
            # We prefer to try-except for speed.
            try:
                download(
                    fd,
                    files.get_media(
                        fileId=file_id,
                        acknowledgeAbuse=acknowledge_abuse,
                        supportsAllDrives=True,
                    ),
                )
            except errors.HttpError as error:
                exc = ApiRequestError(error)
                if (
                    exc.error["code"] != 403
                    or exc.GetField("reason") != "fileNotDownloadable"
                ):
                    raise exc
                mimetype = mimetype or "text/plain"
                fd.seek(0)  # just in case `download()` modified `fd`
                try:
                    download(
                        fd,
                        files.export_media(fileId=file_id, mimeType=mimetype),
                    )
                except errors.HttpError as error:
                    raise ApiRequestError(error)

            if mimetype == "text/plain" and remove_bom:
                fd.seek(0)
                bom = self._GetBOM(mimetype)
                if bom:
                    self._RemovePrefix(fd, bom)

    @LoadAuth
    def GetContentIOBuffer(
        self,
        mimetype=None,
        encoding=None,
        remove_bom=False,
        chunksize=DEFAULT_CHUNK_SIZE,
        acknowledge_abuse=False,
    ):
        """Get a file-like object which has a buffered read() method.

        :param mimetype: mimeType of the file.
        :type mimetype: str
        :param encoding: The encoding to use when decoding the byte string.
        :type encoding: str
        :param remove_bom: Whether to remove the byte order marking.
        :type remove_bom: bool
        :param chunksize: default read()/iter() chunksize.
        :type chunksize: int
        :param acknowledge_abuse: Acknowledging the risk and download file
            identified as abusive.
        :type acknowledge_abuse: bool
        :returns: MediaIoReadable -- file-like object.
        :raises: ApiRequestError, FileNotUploadedError
        """
        files = self.auth.service.files()
        file_id = self.metadata.get("id") or self.get("id")
        if not file_id:
            raise FileNotUploadedError()

        # Should use files.export_media instead of files.get_media if
        # metadata["mimeType"].startswith("application/vnd.google-apps.").
        # But that would first require a slow call to FetchMetadata().
        # We prefer to try-except for speed.
        try:
            request = self._WrapRequest(
                files.get_media(
                    fileId=file_id,
                    acknowledgeAbuse=acknowledge_abuse,
                    supportsAllDrives=True,
                )
            )
            return MediaIoReadable(
                request, encoding=encoding, chunksize=chunksize
            )
        except ApiRequestError as exc:
            if (
                exc.error["code"] != 403
                or exc.GetField("reason") != "fileNotDownloadable"
            ):
                raise
            mimetype = mimetype or "text/plain"
            request = self._WrapRequest(
                files.export_media(fileId=file_id, mimeType=mimetype)
            )
            remove_prefix = (
                self._GetBOM(mimetype)
                if mimetype == "text/plain" and remove_bom
                else b""
            )
            return MediaIoReadable(
                request,
                encoding=encoding,
                remove_prefix=remove_prefix,
                chunksize=chunksize,
            )

    @LoadAuth
    def FetchMetadata(self, fields=None, fetch_all=False):
        """Download file's metadata from id using Files.get().

        :param fields: The fields to include, as one string, each entry separated
                       by commas, e.g. 'id,name,trashed'.
        :type fields: str

        :param fetch_all: Whether to fetch all fields.
        :type fetch_all: bool

        :raises: ApiRequestError, FileNotUploadedError
        """
        file_id = self.metadata.get("id") or self.get("id")

        if fetch_all:
            fields = "*"

        if file_id:
            try:
                request = self.auth.service.files().get(
                    fileId=file_id,
                    fields=fields or DEFAULT_FILE_FIELDS,
                    # Teamdrive support
                    supportsAllDrives=True,
                )
                request = self._AddResourceKeyHeaders(request)
                metadata = request.execute(http=self.http)
            except errors.HttpError as error:
                raise ApiRequestError(error)
            else:
                self.uploaded = True
                self.UpdateMetadata(metadata)
        else:
            raise FileNotUploadedError()

    @LoadMetadata
    def FetchContent(self, mimetype=None, remove_bom=False):
        """Download file content or a Workspace export into memory."""
        if self.get("mimeType") in {
            "application/vnd.google-apps.folder",
            "application/vnd.google-apps.shortcut",
        }:
            raise FileNotDownloadableError(
                "Folders and shortcuts have no content"
            )
        stream = self.GetContentIOBuffer(
            mimetype=mimetype, remove_bom=remove_bom
        )
        self.content = io.BytesIO(b"".join(stream))
        self.dirty["content"] = False
        self.has_bom = not remove_bom
        self._export_mimetype = mimetype

    @LoadAuth
    def Upload(self, param=None, *, media_mime_type=None):
        """Create or update a file, optionally importing uploaded media.

        For a Workspace import, set the destination MIME type in the file
        metadata and pass the source MIME type as media_mime_type.
        """
        params = dict(param or {}, supportsAllDrives=True)
        params.setdefault("fields", DEFAULT_FILE_FIELDS)
        body = self.GetChanges()
        files = self.auth.service.files()
        file_id = self.get("id")
        try:
            if self.dirty["content"]:
                source_type = (
                    media_mime_type
                    or self.get("mimeType")
                    or "application/octet-stream"
                )
                params["media_body"] = MediaIoBaseUpload(
                    self.content, source_type, resumable=True
                )
            if file_id:
                body.pop("id", None)
                if "parents" in body:
                    self._MoveParents(body.pop("parents"), params)
                request = files.update(fileId=file_id, body=body, **params)
            else:
                # Retain the ID so subsequent Upload calls update this file.
                fields = params["fields"]
                if fields != "*" and "id" not in fields.replace(" ", "").split(
                    ","
                ):
                    params["fields"] = f"id,{fields}"
                request = files.create(body=body, **params)
            metadata = self._AddResourceKeyHeaders(request).execute(
                http=self.http
            )
        except errors.HttpError as error:
            raise ApiRequestError(error) from error

        if self.dirty["content"] and (self.get("mimeType") or "").startswith(
            "application/vnd.google-apps."
        ):
            # Read back the converted document on the next content request.
            self.content = None
        self.uploaded = True
        self.dirty["content"] = False
        self.UpdateMetadata(metadata)

    def _MoveParents(self, parents, params):
        """Translate a changed parent list into Drive v3 update parameters."""
        if len(parents) > 1:
            raise ValueError("A Drive v3 file can have only one parent")
        previous = self.metadata.get("parents")
        if previous is None:
            request = self.auth.service.files().get(
                fileId=self["id"], fields="parents", supportsAllDrives=True
            )
            previous = (
                self._WrapRequest(request)
                .execute(http=self.http)
                .get("parents", [])
            )
        if parents and parents[0] not in previous:
            params["addParents"] = parents[0]
        removed = [parent for parent in previous if parent not in parents]
        if removed:
            params["removeParents"] = ",".join(removed)

    def Trash(self, param=None):
        """Move a file to the trash.

        :raises: ApiRequestError
        """
        self._SetTrashed(True, param)

    def UnTrash(self, param=None):
        """Move a file out of the trash.
        :param param: Additional parameter to file.
        :type param: dict.
        :raises: ApiRequestError
        """
        self._SetTrashed(False, param)

    def Delete(self, param=None):
        """Hard-delete a file.

        :param param: additional parameter to file.
        :type param: dict.
        :raises: ApiRequestError
        """
        self._FilesDelete(param=param)

    @LoadAuth
    def Copy(self, target_folder=None, new_title=None, param=None):
        """Creates a copy of this file. Folders cannot be copied.

        :param target_folder: Folder where the file will be copied.
        :type target_folder: GoogleDriveFile, optional
        :param new_title: Name of the new file.
        :type new_title: str, optional
        :param param: addition parameters to pass.
        :type param: dict, optional
        :raises: ApiRequestError
        :return: the copied file
        :rtype: GoogleDriveFile
        """

        params = dict(param or {}, supportsAllDrives=True)
        params.setdefault("fields", DEFAULT_FILE_FIELDS)
        body = {}
        if target_folder is not None:
            body["parents"] = [target_folder["id"]]
        if new_title is not None:
            body["name"] = new_title
        try:
            request = self.auth.service.files().copy(
                fileId=self["id"], body=body, **params
            )
            metadata = self._AddResourceKeyHeaders(request).execute(
                http=self.http
            )
        except errors.HttpError as error:
            raise ApiRequestError(error) from error
        return GoogleDriveFile(self.auth, metadata, uploaded=True)

    @LoadAuth
    def InsertPermission(self, new_permission, param=None):
        """Insert a new permission. Re-fetches all permissions after call.

        :param new_permission: The new permission to insert, please see the
                               official Google Drive API guide on permissions.create
                               for details.
        :type new_permission: object

        :param param: addition parameters to pass
        :type param: dict

        :return: The permission object.
        :rtype: object
        """
        param = dict(param or {})
        param["fileId"] = self.metadata.get("id") or self["id"]
        param["body"] = new_permission
        # Teamdrive support
        param["supportsAllDrives"] = True

        try:
            request = self.auth.service.permissions().create(**param)
            permission = self._AddResourceKeyHeaders(request).execute(
                http=self.http
            )
        except errors.HttpError as error:
            raise ApiRequestError(error)
        else:
            self.GetPermissions()  # Update permissions field.

        return permission

    @LoadAuth
    def GetPermissions(self):
        """Return all permissions for the file, including shared drive files."""
        return self._ListChildren(
            self.auth.service.permissions(),
            "permissions",
            supportsAllDrives=True,
        )

    def DeletePermission(self, permission_id):
        """Deletes the permission specified by the permission_id.

        :param permission_id: The permission id.
        :type permission_id: str
        :return: True if it succeeds.
        :rtype: bool
        """
        return self._DeletePermission(permission_id)

    @LoadAuth
    def GetRevisions(self):
        """Return all revisions visible to the authenticated user."""
        return self._ListChildren(self.auth.service.revisions(), "revisions")

    def _ListChildren(self, resource, key, **params):
        params.update(fileId=self["id"], fields=f"nextPageToken,{key}(*)")
        items = []
        while True:
            request = resource.list(**params)
            page = self._AddResourceKeyHeaders(request).execute(http=self.http)
            items.extend(page.get(key, []))
            if not page.get("nextPageToken"):
                break
            params["pageToken"] = page["nextPageToken"]
        self[key] = self.metadata[key] = items
        return items

    def _WrapRequest(self, request):
        """Replaces request.http with self.http.

        Ensures thread safety. Similar to other places where we call
        `.execute(http=self.http)` to pass a client from the thread local storage.
        """
        if self.http:
            request.http = self.http
        request = self._AddResourceKeyHeaders(request)
        return request

    def _AddResourceKeyHeaders(self, request):
        """Add resourceKey headers to request if file is secured with resourceKey and
        its available (from a list for example).

        :param request: request to add headers to.
        :type request: googleapiclient.http.HttpRequest
        """
        file_id = self.metadata.get("id") or self.get("id")
        if file_id:
            resourceKey = self.get("resourceKey")
            if resourceKey:
                request.headers["X-Goog-Drive-Resource-Keys"] = (
                    f"{file_id}/{resourceKey}"
                )
        return request

    @LoadAuth
    def _SetTrashed(self, trashed, param):
        params = dict(param or {}, supportsAllDrives=True)
        params.setdefault("fields", "trashed")
        request = self.auth.service.files().update(
            fileId=self["id"], body={"trashed": trashed}, **params
        )
        try:
            self._AddResourceKeyHeaders(request).execute(http=self.http)
        except errors.HttpError as error:
            raise ApiRequestError(error) from error
        self["trashed"] = self.metadata["trashed"] = trashed

    @LoadAuth
    def _FilesDelete(self, param=None):
        """Delete a file using Files.Delete()
        (WARNING: deleting permanently deletes the file!)

        :param param: additional parameter to file.
        :type param: dict.
        :raises: ApiRequestError
        """
        if param is None:
            param = {}
        param["fileId"] = self.metadata.get("id") or self["id"]

        # Teamdrive support
        param["supportsAllDrives"] = True

        try:
            self._WrapRequest(
                self.auth.service.files().delete(**param)
            ).execute(http=self.http)
        except errors.HttpError as error:
            raise ApiRequestError(error)
        else:
            return True

    @LoadAuth
    def _DeletePermission(self, permission_id):
        """Deletes the permission remotely, and from the file object itself.

        :param permission_id: The ID of the permission.
        :type permission_id: str

        :return: The permission
        :rtype: object
        """
        file_id = self.metadata.get("id") or self["id"]
        try:
            request = self.auth.service.permissions().delete(
                fileId=file_id,
                permissionId=permission_id,
                supportsAllDrives=True,
            )
            self._AddResourceKeyHeaders(request).execute(http=self.http)
        except errors.HttpError as error:
            raise ApiRequestError(error)
        else:
            if "permissions" in self and "permissions" in self.metadata:
                permissions = [
                    permission
                    for permission in self["permissions"]
                    if permission["id"] != permission_id
                ]
                self["permissions"] = permissions
                self.metadata["permissions"] = permissions
            return True

    @staticmethod
    def _GetBOM(mimetype):
        """Based on download mime type (ignores Google Drive mime type)"""
        for bom in MIME_TYPE_TO_BOM.values():
            if mimetype in bom:
                return bom[mimetype]

    @staticmethod
    def _RemovePrefix(file_object, prefix, block_size=BLOCK_SIZE):
        """Deletes passed prefix by shifting content of passed file object by to
        the left. Operation is in-place.

        Args:
          file_object (obj): The file object to manipulate.
          prefix (str): The prefix to insert.
          block_size (int): The size of the blocks which are moved one at a time.
        """
        prefix_length = len(prefix)
        # Detect if prefix exists in file.
        content_start = file_object.read(prefix_length)

        if content_start == prefix:
            # Shift content left by prefix length, by copying 1KiB at a time.
            block_to_write = file_object.read(block_size)
            current_block_length = len(block_to_write)

            # Read and write location in separate variables for simplicity.
            read_location = prefix_length + current_block_length
            write_location = 0

            while current_block_length > 0:
                # Write next block.
                file_object.seek(write_location)
                file_object.write(block_to_write)
                # Set write location to the next block.
                write_location += len(block_to_write)

                # Read next block of input.
                file_object.seek(read_location)
                block_to_write = file_object.read(block_size)
                # Update the current block length and read_location.
                current_block_length = len(block_to_write)
                read_location += current_block_length

            # Truncate the file to its, now shorter, length.
            file_object.truncate(read_location - prefix_length)

    @staticmethod
    def _InsertPrefix(file_object, prefix, block_size=BLOCK_SIZE):
        """Inserts the passed prefix in the beginning of the file, operation is
        in-place.

        Args:
          file_object (obj): The file object to manipulate.
          prefix (str): The prefix to insert.
        """
        # Read the first two blocks.
        first_block = file_object.read(block_size)
        second_block = file_object.read(block_size)
        # Pointer to the first byte of the next block to be read.
        read_location = block_size * 2

        # Write BOM.
        file_object.seek(0)
        file_object.write(prefix)
        # {read|write}_location separated for readability.
        write_location = len(prefix)

        # Write and read block alternatingly.
        while len(first_block):
            # Write first block.
            file_object.seek(write_location)
            file_object.write(first_block)
            # Increment write_location.
            write_location += block_size

            # Move second block into first variable.
            first_block = second_block

            # Read in the next block.
            file_object.seek(read_location)
            second_block = file_object.read(block_size)
            # Increment read_location.
            read_location += block_size
