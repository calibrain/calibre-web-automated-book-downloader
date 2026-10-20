"""The IRC TLS context must not allow anything older than TLS 1.2."""

import ssl

import pytest

from shelfmark.release_sources.irc.client import IRCClient, IRCConnectionError


def test_tls_context_refuses_tls_1_0_and_1_1(monkeypatch: pytest.MonkeyPatch) -> None:
    contexts: list[ssl.SSLContext] = []
    real_create = ssl.create_default_context

    def capture(*args, **kwargs) -> ssl.SSLContext:
        context = real_create(*args, **kwargs)
        # Allow everything first, so the test fails if connect() does not raise the floor
        context.minimum_version = ssl.TLSVersion.MINIMUM_SUPPORTED

        def refuse(*_args, **_kwargs):
            raise OSError("no network in tests")

        context.wrap_socket = refuse  # type: ignore[method-assign]
        contexts.append(context)
        return context

    monkeypatch.setattr(ssl, "create_default_context", capture)
    client = IRCClient(nick="reader", server="irc.example.test", port=6697, use_tls=True)

    with pytest.raises(IRCConnectionError):
        client.connect()

    assert len(contexts) == 1
    assert contexts[0].minimum_version == ssl.TLSVersion.TLSv1_2
