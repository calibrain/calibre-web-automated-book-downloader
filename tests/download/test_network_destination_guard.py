"""The destination guard behind the direct-download fetchers."""

from types import SimpleNamespace

import pytest

from shelfmark.core import mirrors
from shelfmark.download import network


def _resolve_to(monkeypatch, table: dict[str, str]) -> None:
    def fake_getaddrinfo(host, *_args, **_kwargs):
        if host not in table:
            raise network.socket.gaierror(-2, "Name or service not known")
        return [(None, None, None, None, (table[host], 0))]

    monkeypatch.setattr(network, "_system_getaddrinfo", fake_getaddrinfo)


@pytest.fixture(autouse=True)
def no_mirrors(monkeypatch):
    monkeypatch.setattr(mirrors, "_get_config", lambda: {})


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.1.2.3",
        "172.17.0.2",
        "192.168.1.5",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fd00::1",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
    ],
)
def test_non_public_addresses_are_refused(monkeypatch, address):
    _resolve_to(monkeypatch, {"evil.example": address})

    with pytest.raises(network.UnsafeDestinationError):
        network.ensure_public_destination("https://evil.example/x")


def test_literal_ip_hosts_are_refused(monkeypatch):
    with pytest.raises(network.UnsafeDestinationError):
        network.ensure_public_destination("http://127.0.0.1:8080/")


def test_one_private_answer_among_public_ones_is_refused(monkeypatch):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (None, None, None, None, ("93.184.216.34", 0)),
            (None, None, None, None, ("10.0.0.5", 0)),
        ]

    monkeypatch.setattr(network, "_system_getaddrinfo", fake_getaddrinfo)

    with pytest.raises(network.UnsafeDestinationError):
        network.ensure_public_destination("https://mixed.example/")


def test_public_and_unresolvable_hosts_pass(monkeypatch):
    _resolve_to(monkeypatch, {"ok.example": "93.184.216.34", "v6.example": "2606:2800:220:1::1"})

    network.ensure_public_destination("https://ok.example/book")
    network.ensure_public_destination("https://v6.example/book")
    network.ensure_public_destination("https://does-not-resolve.example/book")


def test_other_schemes_are_refused():
    with pytest.raises(network.UnsafeDestinationError):
        network.ensure_public_destination("file:///etc/passwd")


def test_configured_mirror_on_a_private_address_is_allowed(monkeypatch):
    monkeypatch.setattr(
        mirrors, "_get_config", lambda: {"OCEANOFPDF_MIRROR_URLS": ["http://lan-mirror.home"]}
    )
    _resolve_to(monkeypatch, {"lan-mirror.home": "192.168.1.20"})

    network.ensure_public_destination("http://lan-mirror.home/book")


def test_pinned_aa_base_url_on_a_private_address_is_allowed(monkeypatch):
    # The Primary Mirror setting can pin a URL that is not in the mirror list, and
    # network uses it as the active AA base, so the guard must trust it too.
    monkeypatch.setattr(
        mirrors, "_get_config", lambda: {"AA_BASE_URL": "http://aa.lan:8080", "AA_MIRROR_URLS": []}
    )
    _resolve_to(monkeypatch, {"aa.lan": "192.168.1.30", "other.lan": "192.168.1.31"})

    network.ensure_public_destination("http://aa.lan:8080/search?q=moby")
    with pytest.raises(network.UnsafeDestinationError):
        network.ensure_public_destination("http://other.lan/")


def test_auto_aa_base_url_is_not_a_trusted_host(monkeypatch):
    monkeypatch.setattr(mirrors, "_get_config", lambda: {"AA_BASE_URL": "auto"})
    _resolve_to(monkeypatch, {"auto": "192.168.1.30"})

    with pytest.raises(network.UnsafeDestinationError):
        network.ensure_public_destination("http://auto/")


def test_redirect_hook_checks_each_hop(monkeypatch):
    _resolve_to(monkeypatch, {"ok.example": "93.184.216.34", "internal.example": "10.0.0.9"})
    hook = network.REDIRECT_GUARD_HOOKS["response"][0]

    def hop(location):
        return SimpleNamespace(
            is_redirect=True, url="https://ok.example/a", headers={"Location": location}
        )

    hook(hop("/b"))
    with pytest.raises(network.UnsafeDestinationError):
        hook(hop("http://internal.example/secret"))
    with pytest.raises(network.UnsafeDestinationError):
        hook(hop("http://127.0.0.1/"))
    hook(SimpleNamespace(is_redirect=False, url="https://ok.example/a", headers={}))
