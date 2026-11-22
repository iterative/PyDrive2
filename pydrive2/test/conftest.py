import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.http import HttpMockSequence

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive


@pytest.fixture
def mock_drive():
    def build(*responses):
        http = HttpMockSequence(list(responses))
        auth = GoogleAuth(settings_file=None)
        auth.credentials = Credentials("test-token")
        auth.Authorize()
        auth.thread_local.http = http
        return GoogleDrive(auth), http.request_sequence

    return build
