import json
import os
import re
import time

import pytest
from google.oauth2.credentials import Credentials

from pydrive2._credentials import FileStorage
from pydrive2.auth import AuthenticationError, GoogleAuth
from pydrive2.test.test_util import (
    GDRIVE_USER_CREDENTIALS_DATA,
    delete_file,
    settings_file_path,
    setup_credentials,
)


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


@pytest.mark.drive
def test_08_ServiceAuthFromJsonFileNoCredentialsSaving():
    # Test that no credentials are saved and API is still functional
    # We are testing that there are no exceptions at least
    ga = GoogleAuth(settings_file_path("test_oauth_test_08.yaml"))
    assert not ga.settings["save_credentials"]
    ga.ServiceAuth()
    time.sleep(1)


def test_09_SaveLoadCredentialsUsesDefaultStorage(mocker, tmp_path):
    # Regression for #163: use the same storage lock for loads and saves.
    auth = GoogleAuth(
        settings={
            "client_config_backend": "file",
            "save_credentials": True,
            "save_credentials_backend": "file",
            "save_credentials_file": str(tmp_path / "user.json"),
        }
    )
    auth.credentials = Credentials(
        "token", client_id="id", client_secret="secret"
    )
    spy = mocker.spy(FileStorage, "__init__")
    auth.SaveCredentials()
    auth.LoadCredentials()
    assert auth.credentials.token == "token"
    assert spy.call_count == 0


@pytest.mark.drive
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


@pytest.mark.drive
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
