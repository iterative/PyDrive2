import pytest
import os
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive
from pydrive2.test.test_util import (
    settings_file_path,
    setup_credentials,
    delete_file,
)


@pytest.fixture
def googleauth_preauth():
    setup_credentials()
    # Delete old credentials file
    delete_file("credentials/default_user.dat")
    ga = GoogleAuth(settings_file_path("default_user.yaml"))

    return ga


@pytest.mark.manual
def test_01_CustomAuthWithSavingOfCredentials(googleauth_preauth):

    credentials_file = googleauth_preauth.settings["save_credentials_file"]

    assert not os.path.exists(credentials_file)

    auth_url, state = googleauth_preauth.GetAuthUrl()
    print("please visit this url: {}".format(auth_url))

    googleauth_preauth.Authenticate(input("Please enter the auth code: "))

    # credentials have been loaded
    assert googleauth_preauth.credentials
    # check that credentials file has been saved
    assert os.path.exists(credentials_file)

    gdrive = GoogleDrive(googleauth_preauth)

    about_object = gdrive.GetAbout()
    assert about_object is not None
