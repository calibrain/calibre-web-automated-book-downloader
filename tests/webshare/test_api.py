"""Tests for the Webshare API client: password hashing, XML parsing, token retry."""

import hashlib
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.release_sources.webshare import api
from shelfmark.release_sources.webshare.api import WebshareClient, WebshareError


def _xml(body: str, status: str = "OK") -> bytes:
    return f"<?xml version='1.0' encoding='UTF-8'?><response><status>{status}</status>{body}</response>".encode()


def _response(content: bytes) -> MagicMock:
    response = MagicMock()
    response.content = content
    response.raise_for_status.return_value = None
    return response


@pytest.fixture(autouse=True)
def _clear_tokens():
    WebshareClient._tokens.clear()
    yield
    WebshareClient._tokens.clear()


@pytest.mark.parametrize(
    ("password", "salt", "expected"),
    [
        # Vectors from `openssl passwd -1 -salt <salt> <password>`.
        ("password", "saltsalt", "$1$saltsalt$qjXMvbEw8oaL.CzflDtaK/"),
        ("heslo123ľ", "UtvP0QwB", "$1$UtvP0QwB$BNPMnAafFtaDfoqW.SVSy/"),
        ("x" * 40, "$1$abc$", "$1$abc$b/oSMeDNO7QqHD3W/TXaY0"),
    ],
)
def test_md5_crypt_matches_openssl(password, salt, expected):
    assert api.md5_crypt(password, salt) == expected


def test_hash_password_is_sha1_of_md5crypt_and_md5_digest():
    password_hash, digest = api.hash_password("user", "password", "saltsalt")
    expected_hash = hashlib.sha1(b"$1$saltsalt$qjXMvbEw8oaL.CzflDtaK/").hexdigest()
    assert password_hash == expected_hash
    assert digest == hashlib.md5(f"user:Webshare:{expected_hash}".encode()).hexdigest()


def test_search_parses_files_and_sends_token():
    calls = []

    def post(url, data, **kwargs):
        calls.append((url, dict(data)))
        if url.endswith("/salt/"):
            return _response(_xml("<salt>saltsalt</salt>"))
        if url.endswith("/login/"):
            return _response(_xml("<token>TOKEN1</token>"))
        return _response(
            _xml(
                "<total>2</total>"
                "<file><ident>abc123</ident><name>Kniha (CZ).epub</name><type>epub</type>"
                "<size>123456</size><positive_votes>3</positive_votes>"
                "<negative_votes>1</negative_votes><password>0</password></file>"
                "<file><ident></ident><name>broken.epub</name></file>"
            )
        )

    with patch.object(api.requests, "post", side_effect=post):
        files = WebshareClient("user", "pw").search("kniha", limit=10)

    assert [f.ident for f in files] == ["abc123"]
    assert files[0].size_bytes == 123456
    assert files[0].positive_votes == 3
    assert files[0].info_url == "https://webshare.cz/#/file/abc123"
    search_url, search_data = calls[-1]
    assert search_url == "https://webshare.cz/api/search/"
    assert search_data["wst"] == "TOKEN1"
    assert search_data["what"] == "kniha"


def test_token_is_reused_between_calls():
    logins = []

    def post(url, data, **kwargs):
        if url.endswith("/salt/"):
            return _response(_xml("<salt>s</salt>"))
        if url.endswith("/login/"):
            logins.append(1)
            return _response(_xml("<token>T</token>"))
        return _response(_xml("<link>https://dl.example/file.epub</link>"))

    with patch.object(api.requests, "post", side_effect=post):
        WebshareClient("user", "pw").file_link("a")
        WebshareClient("user", "pw").file_link("b")

    assert len(logins) == 1


def test_expired_token_triggers_one_relogin():
    tokens = iter(["OLD", "NEW"])
    seen_tokens = []

    def post(url, data, **kwargs):
        if url.endswith("/salt/"):
            return _response(_xml("<salt>s</salt>"))
        if url.endswith("/login/"):
            return _response(_xml(f"<token>{next(tokens)}</token>"))
        seen_tokens.append(data["wst"])
        if data["wst"] == "OLD":
            return _response(
                _xml("<code>SEARCH_FATAL_1</code><message>Bad token</message>", "FATAL")
            )
        return _response(_xml("<total>0</total>"))

    with patch.object(api.requests, "post", side_effect=post):
        assert WebshareClient("user", "pw").search("x") == []

    assert seen_tokens == ["OLD", "NEW"]


def test_file_errors_are_not_retried():
    link_calls = []

    def post(url, data, **kwargs):
        if url.endswith("/salt/"):
            return _response(_xml("<salt>s</salt>"))
        if url.endswith("/login/"):
            return _response(_xml("<token>T</token>"))
        link_calls.append(1)
        return _response(
            _xml("<code>FILE_LINK_FATAL_1</code><message>File not found.</message>", "FATAL")
        )

    with (
        patch.object(api.requests, "post", side_effect=post),
        pytest.raises(WebshareError) as exc_info,
    ):
        WebshareClient("user", "pw").file_link("gone")

    assert exc_info.value.code == "FILE_LINK_FATAL_1"
    assert str(exc_info.value) == "File not found."
    assert len(link_calls) == 1


def test_login_requires_credentials():
    with pytest.raises(WebshareError):
        WebshareClient("", "").login()


def test_unreadable_response_raises():
    with (
        patch.object(api.requests, "post", return_value=_response(b"<html>nope")),
        pytest.raises(WebshareError),
    ):
        WebshareClient("user", "pw").login()


def test_changed_password_forces_new_login():
    logins = []

    def post(url, data, **kwargs):
        if url.endswith("/salt/"):
            return _response(_xml("<salt>s</salt>"))
        if url.endswith("/login/"):
            logins.append(data["password"])
            return _response(_xml(f"<token>T{len(logins)}</token>"))
        return _response(_xml("<link>https://dl.example/file.epub</link>"))

    with patch.object(api.requests, "post", side_effect=post):
        WebshareClient("user", "old").file_link("a")
        WebshareClient("user", "new").file_link("a")

    assert len(logins) == 2
    assert logins[0] != logins[1]
