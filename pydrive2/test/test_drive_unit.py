from urllib.parse import urlsplit

import pytest

from pydrive2.test.http_mock import query, response


def test_list_retains_next_page_token(mock_drive):
    drive, requests = mock_drive(
        response({"nextPageToken": "more", "files": []}),
        response({"files": [{"name": "second"}]}),
    )
    listing = drive.ListFile({"fields": "files(name)"})
    assert [item["name"] for item in listing.GetList()] == ["second"]
    first, second = requests
    assert (
        urlsplit(first[0]).path
        == urlsplit(second[0]).path
        == "/drive/v3/files"
    )
    assert query(first)["fields"] == ["nextPageToken,files(name)"]
    assert query(second)["pageToken"] == ["more"]


def test_list_wildcard_includes_page_token(mock_drive):
    drive, requests = mock_drive(response({"files": []}))
    assert drive.ListFile({"fields": " * "}).GetList() == []
    assert query(requests[0])["fields"] == ["*"]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [(None, "*"), ("user/displayName", "user/displayName")],
)
def test_about_fields(mock_drive, fields, expected):
    about = {"user": {"displayName": "Drive User"}}
    drive, requests = mock_drive(response(about))
    assert drive.GetAbout(fields=fields) == about
    (request,) = requests
    assert urlsplit(request[0]).path == "/drive/v3/about"
    assert query(request)["fields"] == [expected]
