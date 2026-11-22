import pytest
from google.oauth2.credentials import Credentials
from googleapiclient.http import HttpMockSequence

from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive


class DriveHttpMock(HttpMockSequence):
    def __init__(self, responses, on_request):
        super().__init__(list(responses))
        self.on_request = on_request

    def request(self, uri, method="GET", body=None, headers=None, **kwargs):
        if self.on_request is not None:
            self.on_request((uri, method, body, headers))
        return super().request(
            uri, method, body=body, headers=headers, **kwargs
        )


@pytest.fixture
def mock_drive():
    def build(*responses, on_request=None):
        http = DriveHttpMock(responses, on_request)
        auth = GoogleAuth(settings_file=None)
        auth.credentials = Credentials("test-token")
        auth.Authorize()
        auth.thread_local.http = http
        return GoogleDrive(auth), http.request_sequence

    return build
