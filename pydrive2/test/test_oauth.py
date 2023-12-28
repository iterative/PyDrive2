import json
import os
import re
import time
import pytest

from pydrive2.auth import AuthenticationError, GoogleAuth
from pydrive2.test.test_util import (
    setup_credentials,
    delete_file,
    settings_file_path,
    GDRIVE_USER_CREDENTIALS_DATA,
)
from oauth2client.file import Storage


def setup_module(module):
    setup_credentials()


@pytest.mark.manual
def test_01_LocalWebserverAuthWithClientConfigFromFile():
    # Delete old credentials file
    delete_file("credentials/1.dat")
    # Test if authentication works with config read from file
    ga = GoogleAuth(settings_file_path("test_oauth_test_01.yaml"))
    ga.LocalWebserverAuth()
    assert not ga.access_token_expired
    # Test if correct credentials file is created
    CheckCredentialsFile("credentials/1.dat")
    time.sleep(1)


@pytest.mark.manual
def test_02_LocalWebserverAuthWithClientConfigFromSettings():
    # Delete old credentials file
    delete_file("credentials/2.dat")
    # Test if authentication works with config read from settings
    ga = GoogleAuth(settings_file_path("test_oauth_test_02.yaml"))
    ga.LocalWebserverAuth()
    assert not ga.access_token_expired
    # Test if correct credentials file is created
    CheckCredentialsFile("credentials/2.dat")
    time.sleep(1)


@pytest.mark.manual
def test_03_LocalWebServerAuthWithNoCredentialsSaving():
    # Delete old credentials file
    delete_file("credentials/3.dat")
    ga = GoogleAuth(settings_file_path("test_oauth_test_03.yaml"))
    assert not ga.settings["save_credentials"]
    ga.LocalWebserverAuth()
    assert not ga.access_token_expired
    time.sleep(1)


@pytest.mark.manual
def test_04_CommandLineAuthWithClientConfigFromFile():
    # Delete old credentials file
    delete_file("credentials/4.dat")
    # Test if authentication works with config read from file
    ga = GoogleAuth(settings_file_path("test_oauth_test_04.yaml"))
    ga.CommandLineAuth()
    assert not ga.access_token_expired
    # Test if correct credentials file is created
    CheckCredentialsFile("credentials/4.dat")
    time.sleep(1)


@pytest.mark.manual
def test_05_ConfigFromSettingsWithoutOauthScope():
    # Test if authentication works without oauth_scope
    ga = GoogleAuth(settings_file_path("test_oauth_test_05.yaml"))
    ga.LocalWebserverAuth()
    assert not ga.access_token_expired
    time.sleep(1)


@pytest.mark.skip(reason="P12 authentication is deprecated")
def test_06_ServiceAuthFromSavedCredentialsP12File():
    setup_credentials("credentials/6.dat")
    ga = GoogleAuth(settings_file_path("test_oauth_test_06.yaml"))
    ga.ServiceAuth()
    assert not ga.access_token_expired
    time.sleep(1)


def test_07_ServiceAuthFromSavedCredentialsJsonFile():
    # Have an initial auth so that credentials/7.dat gets saved
    ga = GoogleAuth(settings_file_path("test_oauth_test_07.yaml"))
    credentials_file = ga.settings["save_credentials_file"]
    # Delete old credentials file
    delete_file(credentials_file)
    assert not os.path.exists(credentials_file)
    ga.ServiceAuth()
    assert os.path.exists(credentials_file)
    # Secondary auth should be made only using the previously saved
    # login info
    ga = GoogleAuth(settings_file_path("test_oauth_test_07.yaml"))
    ga.ServiceAuth()
    assert not ga.access_token_expired
    time.sleep(1)


def test_08_ServiceAuthFromJsonFileNoCredentialsSaving():
    # Test that no credentials are saved and API is still functional
    # We are testing that there are no exceptions at least
    ga = GoogleAuth(settings_file_path("test_oauth_test_08.yaml"))
    assert not ga.settings["save_credentials"]
    ga.ServiceAuth()
    time.sleep(1)


def test_09_SaveLoadCredentialsUsesDefaultStorage(mocker):
    # Test fix for https://github.com/iterative/PyDrive2/issues/163
    # Make sure that Load and Save credentials by default reuse the
    # same Storage (since it defined lock which make it TS)
    ga = GoogleAuth(settings_file_path("test_oauth_test_09.yaml"))
    credentials_file = ga.settings["save_credentials_file"]
    # Delete old credentials file
    delete_file(credentials_file)
    assert not os.path.exists(credentials_file)
    spy = mocker.spy(Storage, "__init__")
    ga.ServiceAuth()
    ga.LoadCredentials()
    ga.SaveCredentials()
    assert spy.call_count == 0


def test_10_ServiceAuthFromSavedCredentialsDictionary():
    creds_dict = {}
    settings = {
        "client_config_backend": "service",
        "service_config": {
            "client_json_file_path": "/tmp/pydrive2/credentials.json",
        },
        "oauth_scope": ["https://www.googleapis.com/auth/drive"],
        "save_credentials": True,
        "save_credentials_backend": "dictionary",
        "save_credentials_dict": creds_dict,
        "save_credentials_key": "creds",
    }
    ga = GoogleAuth(settings=settings)
    ga.ServiceAuth()
    assert not ga.access_token_expired
    assert creds_dict
    first_creds_dict = creds_dict.copy()
    # Secondary auth should be made only using the previously saved
    # login info
    ga = GoogleAuth(settings=settings)
    ga.ServiceAuth()
    assert not ga.access_token_expired
    assert creds_dict == first_creds_dict
    time.sleep(1)


def test_11_ServiceAuthFromJsonNoCredentialsSaving():
    client_json = os.environ[GDRIVE_USER_CREDENTIALS_DATA]
    settings = {
        "client_config_backend": "service",
        "service_config": {
            "client_json": client_json,
        },
        "oauth_scope": ["https://www.googleapis.com/auth/drive"],
    }
    # Test that no credentials are saved and API is still functional
    # We are testing that there are no exceptions at least
    ga = GoogleAuth(settings=settings)
    assert not ga.settings["save_credentials"]
    ga.ServiceAuth()
    time.sleep(1)


def test_12_ServiceAuthFromJsonDictNoCredentialsSaving():
    client_json_dict = json.loads(os.environ[GDRIVE_USER_CREDENTIALS_DATA])
    settings = {
        "client_config_backend": "service",
        "service_config": {
            "client_json_dict": client_json_dict,
        },
        "oauth_scope": ["https://www.googleapis.com/auth/drive"],
    }
    # Test that no credentials are saved and API is still functional
    # We are testing that there are no exceptions at least
    ga = GoogleAuth(settings=settings)
    assert not ga.settings["save_credentials"]
    ga.ServiceAuth()
    time.sleep(1)


def test_13_LocalWebServerAuthNonInterativeRaises(monkeypatch):
    settings = {
        "client_config_backend": "file",
        "client_config_file": "client_secrets.json",
        "oauth_scope": ["https://www.googleapis.com/auth/drive"],
    }
    ga = GoogleAuth(settings=settings)

    monkeypatch.setenv("GDRIVE_NON_INTERACTIVE", "true")
    # Test that exception is raised on trying to do browser auth if
    # we are running in a non interactive environment.
    with pytest.raises(
        AuthenticationError,
        match=re.escape(
            "Non interactive mode (GDRIVE_NON_INTERACTIVE env) is enabled"
        ),
    ):
        ga.LocalWebserverAuth()


def CheckCredentialsFile(credentials, no_file=False):
    ga = GoogleAuth(settings_file_path("test_oauth_default.yaml"))
    ga.LoadCredentialsFile(credentials)
    assert ga.access_token_expired == no_file
