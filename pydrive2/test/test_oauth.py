import os
import time
import pytest

from pydrive2.auth import GoogleAuth
from pydrive2.test.test_util import (
    setup_credentials,
    delete_file,
    settings_file_path,
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


def CheckCredentialsFile(credentials, no_file=False):
    ga = GoogleAuth(settings_file_path("test_oauth_default.yaml"))
    ga.LoadCredentialsFile(credentials)
    assert ga.access_token_expired == no_file
