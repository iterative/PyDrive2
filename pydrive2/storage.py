import os
import json
import warnings
import threading


_SYM_LINK_MESSAGE = "File: {0}: Is a symbolic link."
_IS_DIR_MESSAGE = "{0}: Is a directory"
_MISSING_FILE_MESSAGE = "Cannot access {0}: No such file or directory"


def validate_file(filename):
    if os.path.islink(filename):
        raise IOError(_SYM_LINK_MESSAGE.format(filename))
    elif os.path.isdir(filename):
        raise IOError(_IS_DIR_MESSAGE.format(filename))
    elif not os.path.isfile(filename):
        warnings.warn(_MISSING_FILE_MESSAGE.format(filename))


class CredentialBackend(object):
    """Adapter that provides a consistent interface to read and write credentials"""

    def __init__(self, thread_lock=None):
        self._thread_lock = thread_lock

    def _read_credentials(self, **kwargs):
        """Specific implementation of how credentials are retrieved from backend"""
        return NotImplementedError

    def _store_credentials(self, credential, **kwargs):
        """Specific implementation of how credentials are written to backend"""
        return NotImplementedError

    def _delete_credentials(self, **kwargs):
        """Specific implementation of how credentials are deleted from backend"""
        return NotImplementedError

    def read_credentials(self, **kwargs):
        """Reads a credential config from the backend and
        returns the config as a dictionary
        :return: A dictionary of the credentials
        """
        return self._read_credentials(**kwargs)

    def store_credentials(self, credential, **kwargs):
        """Write a credential to the backend as a json file"""
        self._store_credentials(credential, **kwargs)

    def delete_credentials(self, **kwargs):
        """Delete credential.
        Frees any resources associated with storing the credential
        """
        self._delete_credentials(**kwargs)


class FileBackend(CredentialBackend):
    """Read and write credential to a specific file backend with Thread-locking"""

    def __init__(self, filename):
        self._filename = filename
        self._thread_lock = threading.Lock()

    def _create_file_if_needed(self, filename):
        """Create an empty file if necessary.
        This method will not initialize the file. Instead it implements a
        simple version of "touch" to ensure the file has been created.
        """
        if not os.path.exists(filename):
            old_umask = os.umask(0o177)
            try:
                open(filename, "a+b").close()
            finally:
                os.umask(old_umask)

    def _read_credentials(self, **kwargs):
        """Reads a local json file and parses the information into a info dictionary."""
        with self._thread_lock:
            validate_file(self._filename)
            with open(self._filename, "r") as json_file:
                return json.load(json_file)

    def _store_credentials(self, credentials, **kwargs):
        """Writes current credentials to a local json file."""
        with self._thread_lock:
            # write new credentials to the temp file
            dirname, filename = os.path.split(self._filename)
            temp_path = os.path.join(dirname, "temp_{}".format(filename))
            self._create_file_if_needed(temp_path)

            with open(temp_path, "w") as json_file:
                json_file.write(credentials.to_json())

            # replace the existing credential file
            os.replace(temp_path, self._filename)

    def _delete_credentials(self, **kwargs):
        """Delete credentials file."""
        with self._thread_lock:
            os.unlink(self._filename)


class DictionaryBackend(CredentialBackend):
    """Read and write credentials to a dictionary backend"""

    def __init__(self, dictionary, thread_lock=None):
        super().__init__(thread_lock=thread_lock)
        self._dictionary = dictionary

    def _read_credentials(self, key):
        """Reads a local json file and parses the information into a info dictionary."""
        return self._dictionary.get(key)

    def _store_credentials(self, credentials, key):
        """Writes current credentials to a local json file."""
        self._dictionary[key] = credentials.to_json()

    def _delete_credentials(self, key):
        """Delete Credentials file."""
        self._dictionary.pop(key, None)
