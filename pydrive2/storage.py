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
    """Adapter that provides a consistent interface to read and write credential files"""

    def __init__(self, thread_lock=None):
        self._thread_lock = thread_lock

    def _read_credentials(self, rpath):
        """Specific implementation of how the storage object should retrieve a file."""
        return NotImplementedError

    def _store_credentials(self, credential, rpath):
        """Specific implementation of how the storage object should write a file"""
        return NotImplementedError

    def _delete_credentials(self, rpath):
        """Specific implementation of how the storage object should delete a file."""
        return NotImplementedError

    def read_credentials(self, rpath):
        """Reads a credential config file and returns the config as a dictionary
        :param fname: host name of the local web server.
        :type host_name: str.`
        :return: A credential file
        """
        return self._read_credentials(rpath)

    def store_credentials(self, credential, rpath):
        """Write a credential to
        The Storage lock must be held when this is called.
        Args:
            credentials: Credentials, the credentials to store.
        """
        self._store_credentials(credential, rpath)

    def delete_credentials(self, rpath):
        """Delete credential.
        Frees any resources associated with storing the credential.
        The Storage lock must *not* be held when this is called.

        Returns:
            None
        """
        self._delete_credentials(rpath)


class FileBackend(CredentialBackend):
    """Read and write credentials to a file backend with Thread-locking"""

    def __init__(self):
        super().__init__(thread_lock=threading.Lock())

    def _create_file_if_needed(self, rpath):
        """Create an empty file if necessary.
        This method will not initialize the file. Instead it implements a
        simple version of "touch" to ensure the file has been created.
        """
        if not os.path.exists(rpath):
            old_umask = os.umask(0o177)
            try:
                open(rpath, "a+b").close()
            finally:
                os.umask(old_umask)

    def _read_credentials(self, rpath):
        """Reads a local json file and parses the information into a info dictionary.
        Returns:
        Raises:
        """
        with self._thread_lock:
            validate_file(rpath)
            with open(rpath, "r") as json_file:
                return json.load(json_file)

    def _store_credentials(self, credentials, rpath):
        """Writes current credentials to a local json file.
        Args:
        Raises:
        """
        with self._thread_lock:
            # write new credentials to the temp file
            dirname, filename = os.path.split(rpath)
            temp_path = os.path.join(dirname, "temp_{}".format(filename))
            self._create_file_if_needed(temp_path)

            with open(temp_path, "w") as json_file:
                json_file.write(credentials.to_json())

            # replace the existing credential file
            os.replace(temp_path, rpath)

    def _delete_credentials(self, rpath):
        """Delete Credentials file.
        Args:
            credentials: Credentials, the credentials to store.
        """
        with self._thread_lock:
            os.unlink(rpath)


class DictionaryBackend(CredentialBackend):
    """Read and write credentials to a dictionary backend"""

    def __init__(self, dictionary, thread_lock=None):
        super().__init__(thread_lock=thread_lock)
        self._dictionary = dictionary

    def _read_credentials(self, rpath):
        """Reads a local json file and parses the information into a info dictionary.
        Returns:
        Raises:
        """
        return self._dictionary.get(rpath)

    def _store_credentials(self, credentials, rpath):
        """Writes current credentials to a local json file.
        Args:
        Raises:
        """
        self._dictionary[rpath] = credentials.to_json()

    def _delete_credentials(self, rpath):
        """Delete Credentials file.
        Args:
            credentials: Credentials, the credentials to store.
        """
        self._dictionary.pop(rpath, None)
