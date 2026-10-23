"""Tests for proxy selection and NO_PROXY bypass handling."""


def _set_proxy_config(monkeypatch, **values):
    import shelfmark.download.network as network

    def fake_get(key, default=""):
        return values.get(key, default)

    monkeypatch.setattr(network.app_config, "get", fake_get)
    return network


def test_get_proxies_bypasses_exact_and_wildcard_no_proxy_hosts(monkeypatch):
    network = _set_proxy_config(
        monkeypatch,
        PROXY_MODE="http",
        HTTP_PROXY="http://proxy.local:8080",
        HTTPS_PROXY="https://secure-proxy.local:8443",
        NO_PROXY="LOCALHOST, *.Internal, 10.*",
    )

    assert network.should_bypass_proxy("https://localhost:8080") is True
    assert network.should_bypass_proxy("https://API.Internal/path") is True
    assert network.should_bypass_proxy("https://10.1.2.3/file") is True
    assert network.should_bypass_proxy("https://example.com") is False

    assert network.get_proxies("https://localhost:8080") == {}
    assert network.get_proxies("https://example.com") == {
        "http": "http://proxy.local:8080",
        "https": "https://secure-proxy.local:8443",
    }


def test_get_proxies_falls_back_to_http_proxy_for_https(monkeypatch):
    network = _set_proxy_config(
        monkeypatch,
        PROXY_MODE="http",
        HTTP_PROXY="http://proxy.local:8080",
        HTTPS_PROXY="",
        NO_PROXY="",
    )

    assert network.get_proxies("https://example.com") == {
        "http": "http://proxy.local:8080",
        "https": "http://proxy.local:8080",
    }


def test_get_proxies_returns_socks_proxy_for_both_schemes(monkeypatch):
    network = _set_proxy_config(
        monkeypatch,
        PROXY_MODE="socks5",
        SOCKS5_PROXY="socks5://proxy.local:1080",
        NO_PROXY="",
    )

    assert network.get_proxies("https://example.com") == {
        "http": "socks5://proxy.local:1080",
        "https": "socks5://proxy.local:1080",
    }


def test_a_url_that_fails_to_parse_is_not_written_to_the_log(monkeypatch):
    """The proxy check logs why a URL did not parse, never the URL itself."""
    from unittest.mock import patch

    network = _set_proxy_config(monkeypatch, NO_PROXY="localhost")
    # An unterminated IPv6 literal makes urlparse raise ValueError.
    url = "http://user:s3cret@[::1/path?apikey=s3cret"

    with patch.object(network.logger, "debug") as debug:
        assert network.should_bypass_proxy(url) is False

    logged = " ".join(str(arg) for call in debug.call_args_list for arg in call.args)
    assert "Failed to parse URL" in logged
    assert "s3cret" not in logged
