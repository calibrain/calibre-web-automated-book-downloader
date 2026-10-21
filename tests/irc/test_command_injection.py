"""A line break in any IRC argument must never become a second IRC command."""

from threading import Event
from unittest.mock import MagicMock

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.irc import handler as irc_handler
from shelfmark.release_sources.irc.client import IRCClient, IRCError


class _RecordingSocket:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)


def _client() -> tuple[IRCClient, _RecordingSocket]:
    client = IRCClient(nick="reader", server="irc.example.test", port=6697)
    sock = _RecordingSocket()
    client._socket = sock
    client._connected = True
    return client, sock


@pytest.mark.parametrize("bad", ["\r\n", "\n", "\r", "\x00", "\x07", "\x7f"])
def test_send_message_refuses_control_characters(bad):
    client, sock = _client()

    with pytest.raises(IRCError):
        client.send_message("#books", f"!bot title{bad}JOIN #evil")

    assert sock.sent == []


def test_send_message_refuses_crlf_in_target():
    client, sock = _client()

    with pytest.raises(IRCError):
        client.send_message("#books\r\nQUIT", "hi")

    assert sock.sent == []


def test_send_notice_and_join_refuse_crlf():
    client, sock = _client()

    with pytest.raises(IRCError):
        client.send_notice("nick\r\nQUIT", "x")
    with pytest.raises(IRCError):
        client.join_channel("books\r\nQUIT", wait_for_join=False)

    assert sock.sent == []


def test_send_message_still_sends_one_line_with_ctcp_and_formatting():
    client, sock = _client()

    client.send_message("#books", "!bot Title éè (2020)")
    client.send_notice("bot", "\x01VERSION 1.0\x01")

    assert sock.sent == [
        "PRIVMSG #books :!bot Title éè (2020)\r\n".encode(),
        b"NOTICE bot :\x01VERSION 1.0\x01\r\n",
    ]


def _run_handler(monkeypatch, request_line):
    client = MagicMock()
    manager = MagicMock()
    manager.get_connection.return_value = client
    monkeypatch.setattr(irc_handler, "connection_manager", manager)
    values = {"IRC_SERVER": "irc.example.test", "IRC_CHANNEL": "books", "IRC_NICK": "reader"}
    monkeypatch.setattr(irc_handler.config, "get", lambda key, default="": values.get(key, default))
    statuses: list[tuple[str, str | None]] = []
    task = DownloadTask(task_id=request_line, source="irc", title="t")

    result = irc_handler.IRCDownloadHandler().download(
        task, Event(), lambda _p: None, lambda status, msg: statuses.append((status, msg))
    )
    return result, client, manager, statuses


@pytest.mark.parametrize("line", ["!bot t\r\nPRIVMSG #other :spam", "!bot t\nJOIN #x", "!bot\x00t"])
def test_handler_rejects_request_line_with_control_characters(monkeypatch, line):
    result, client, manager, statuses = _run_handler(monkeypatch, line)

    assert result is None
    client.send_message.assert_not_called()
    manager.get_connection.assert_not_called()
    assert statuses[-1][0] == "error"
    assert "control character" in (statuses[-1][1] or "")


def test_search_refuses_query_with_control_characters(monkeypatch):
    from shelfmark.core.search_plan import build_release_search_plan
    from shelfmark.metadata_providers import BookMetadata
    from shelfmark.release_sources.irc import source as irc_source

    manager = MagicMock()
    monkeypatch.setattr(irc_source, "connection_manager", manager)
    monkeypatch.setattr(irc_source, "_config_text", lambda key: f"{key.lower()}-value")
    monkeypatch.setattr(irc_source.IRCReleaseSource, "is_available", lambda _self: True)
    book = BookMetadata(
        provider="hardcover", provider_id="1", title="T\r\nJOIN #evil", authors=["A B"]
    )

    releases = irc_source.IRCReleaseSource().search(book, build_release_search_plan(book))

    assert releases == []
    manager.get_connection.assert_not_called()
