import pytest
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.test.test_util import (
    settings_file_path,
    setup_credentials,
    pydrive_retry,
    delete_file,
)
from google.auth.exceptions import RefreshError


@pytest.fixture
def googleauth_refresh():
    setup_credentials()
    # Delete old credentials file
    delete_file("credentials/default_user.dat")
    ga = GoogleAuth(settings_file_path("default_user.yaml"))
    ga.LocalWebserverAuth()

    return ga


@pytest.fixture
def googleauth_no_refresh():
    setup_credentials()
    # Delete old credentials file
    delete_file("credentials/default_user_no_refresh.dat")
    ga = GoogleAuth(settings_file_path("default_user_no_refresh.yaml"))
    ga.LocalWebserverAuth()

    return ga


@pytest.mark.manual
def test_01_TokenExpiryWithRefreshToken(googleauth_refresh):
    gdrive = GoogleDrive(googleauth_refresh)

    about_object = pydrive_retry(gdrive.GetAbout)
    assert about_object is not None

    # save the first access token for comparison
    token1 = gdrive.auth.credentials.token

    # simulate token expiry by deleting the underlying token
    gdrive.auth.credentials.token = None

    # credential object should still exist but access token expired
    assert gdrive.auth.credentials
    assert gdrive.auth.access_token_expired

    about_object = pydrive_retry(gdrive.GetAbout)
    assert about_object is not None

    # save the second access token for comparison
    token2 = gdrive.auth.credentials.token

    assert token1 != token2


@pytest.mark.manual
def test_02_TokenExpiryWithoutRefreshToken(googleauth_no_refresh):
    gdrive = GoogleDrive(googleauth_no_refresh)

    about_object = pydrive_retry(gdrive.GetAbout)
    assert about_object is not None

    # simulate token expiry by deleting the underlying token
    gdrive.auth.credentials.token = None

    # credential object should still exist but access token expired
    assert gdrive.auth.credentials
    assert gdrive.auth.access_token_expired

    # as credentials have no refresh token, this would fail
    with pytest.raises(RefreshError) as e_info:
        about_object = pydrive_retry(gdrive.GetAbout)
