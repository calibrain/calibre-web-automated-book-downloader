"""
Tests for torrent utility functions.

Tests:
- parse_transmission_url
- bencode_encode/decode
- extract_info_hash_from_torrent
- extract_hash_from_magnet
"""

import base64
import hashlib
from unittest.mock import MagicMock

import pytest

from shelfmark.download.clients.torrent_utils import (
    bencode_decode,
    bencode_encode,
    extract_hash_from_magnet,
    extract_info_hash_from_torrent,
    extract_torrent_info,
    parse_transmission_url,
)


class TestParseTransmissionUrl:
    """Tests for parse_transmission_url function."""

    def test_parse_simple_url(self):
        """Test parsing a simple URL with host and port."""
        protocol, host, port, path = parse_transmission_url("http://localhost:9091")
        assert protocol == "http"
        assert host == "localhost"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_url_with_custom_port(self):
        """Test parsing URL with custom port."""
        protocol, host, port, path = parse_transmission_url("http://myserver:8080")
        assert protocol == "http"
        assert host == "myserver"
        assert port == 8080
        assert path == "/transmission/rpc"

    def test_parse_url_with_path(self):
        """Test parsing URL with existing path."""
        protocol, host, port, path = parse_transmission_url(
            "http://localhost:9091/transmission/rpc"
        )
        assert protocol == "http"
        assert host == "localhost"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_url_with_partial_path(self):
        """Test parsing URL with partial path appends /rpc."""
        protocol, host, port, path = parse_transmission_url("http://localhost:9091/custom")
        assert protocol == "http"
        assert host == "localhost"
        assert port == 9091
        assert path == "/custom/transmission/rpc"

    def test_parse_url_with_trailing_slash(self):
        """Test parsing URL with trailing slash."""
        protocol, host, port, path = parse_transmission_url("http://localhost:9091/")
        assert protocol == "http"
        assert host == "localhost"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_url_without_port(self):
        """Test parsing URL without port uses default 9091."""
        protocol, host, port, path = parse_transmission_url("http://transmission")
        assert protocol == "http"
        assert host == "transmission"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_https_url(self):
        """Test parsing HTTPS URL."""
        protocol, host, port, path = parse_transmission_url(
            "https://secure.transmission.local:9091"
        )
        assert protocol == "https"
        assert host == "secure.transmission.local"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_url_with_ip_address(self):
        """Test parsing URL with IP address."""
        protocol, host, port, path = parse_transmission_url("http://192.168.1.100:9091")
        assert protocol == "http"
        assert host == "192.168.1.100"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_empty_url_uses_defaults(self):
        """Test parsing empty URL uses localhost defaults."""
        protocol, host, port, path = parse_transmission_url("")
        assert protocol == "http"
        assert host == "localhost"
        assert port == 9091
        assert path == "/transmission/rpc"


class TestBencodeDecode:
    """Tests for bencode decoding."""

    def test_decode_integer(self):
        """Test decoding integers."""
        result, remaining = bencode_decode(b"i42e")
        assert result == 42
        assert remaining == b""

    def test_decode_negative_integer(self):
        """Test decoding negative integers."""
        result, _remaining = bencode_decode(b"i-42e")
        assert result == -42

    def test_decode_zero(self):
        """Test decoding zero."""
        result, _remaining = bencode_decode(b"i0e")
        assert result == 0

    def test_decode_large_integer(self):
        """Test decoding large integers."""
        result, _remaining = bencode_decode(b"i999999999999e")
        assert result == 999999999999

    def test_decode_string(self):
        """Test decoding byte strings."""
        result, remaining = bencode_decode(b"5:hello")
        assert result == b"hello"
        assert remaining == b""

    def test_decode_empty_string(self):
        """Test decoding empty string."""
        result, _remaining = bencode_decode(b"0:")
        assert result == b""

    def test_decode_unicode_string(self):
        """Test decoding unicode bytes."""
        data = "tëst".encode()
        encoded = f"{len(data)}:".encode() + data
        result, _remaining = bencode_decode(encoded)
        assert result == data

    def test_decode_list(self):
        """Test decoding lists."""
        result, remaining = bencode_decode(b"li1ei2ei3ee")
        assert result == [1, 2, 3]
        assert remaining == b""

    def test_decode_empty_list(self):
        """Test decoding empty list."""
        result, _remaining = bencode_decode(b"le")
        assert result == []

    def test_decode_nested_list(self):
        """Test decoding nested lists."""
        result, _remaining = bencode_decode(b"lli1eeli2eee")
        assert result == [[1], [2]]

    def test_decode_mixed_list(self):
        """Test decoding list with mixed types."""
        result, _remaining = bencode_decode(b"l5:helloi42ee")
        assert result == [b"hello", 42]

    def test_decode_dict(self):
        """Test decoding dictionaries."""
        result, remaining = bencode_decode(b"d3:key5:valuee")
        assert result == {b"key": b"value"}
        assert remaining == b""

    def test_decode_empty_dict(self):
        """Test decoding empty dictionary."""
        result, _remaining = bencode_decode(b"de")
        assert result == {}

    def test_decode_complex_structure(self):
        """Test decoding complex nested structures."""
        # Dict with string, int, and list values
        data = b"d3:agei25e4:name4:John5:itemsli1ei2ei3eee"
        result, _remaining = bencode_decode(data)
        assert result == {
            b"age": 25,
            b"name": b"John",
            b"items": [1, 2, 3],
        }

    def test_decode_invalid_data_raises(self):
        """Test that invalid data raises ValueError."""
        with pytest.raises(ValueError):
            bencode_decode(b"x")


class TestBencodeEncode:
    """Tests for bencode encoding."""

    def test_encode_integer(self):
        """Test encoding integers."""
        assert bencode_encode(42) == b"i42e"
        assert bencode_encode(-42) == b"i-42e"
        assert bencode_encode(0) == b"i0e"

    def test_encode_bytes(self):
        """Test encoding byte strings."""
        assert bencode_encode(b"hello") == b"5:hello"
        assert bencode_encode(b"") == b"0:"

    def test_encode_string(self):
        """Test encoding regular strings (UTF-8 encoded)."""
        assert bencode_encode("hello") == b"5:hello"
        assert bencode_encode("") == b"0:"

    def test_encode_list(self):
        """Test encoding lists."""
        assert bencode_encode([1, 2, 3]) == b"li1ei2ei3ee"
        assert bencode_encode([]) == b"le"

    def test_encode_dict(self):
        """Test encoding dictionaries."""
        result = bencode_encode({b"key": b"value"})
        assert result == b"d3:key5:valuee"

    def test_encode_dict_keys_sorted(self):
        """Test that dictionary keys are sorted."""
        # Keys should be sorted: a < m < z
        result = bencode_encode({b"z": 1, b"a": 2, b"m": 3})
        assert result == b"d1:ai2e1:mi3e1:zi1ee"

    def test_encode_nested_structure(self):
        """Test encoding nested structures."""
        data = {b"list": [1, 2, 3], b"num": 42}
        result = bencode_encode(data)
        assert result == b"d4:listli1ei2ei3ee3:numi42ee"

    def test_encode_invalid_type_raises(self):
        """Test that invalid types raise ValueError."""
        with pytest.raises(ValueError):
            bencode_encode(3.14)  # floats not supported


class TestBencodeRoundTrip:
    """Tests for encoding then decoding (roundtrip)."""

    def test_roundtrip_integer(self):
        """Test roundtrip for integers."""
        original = 12345
        encoded = bencode_encode(original)
        decoded, _ = bencode_decode(encoded)
        assert decoded == original

    def test_roundtrip_bytes(self):
        """Test roundtrip for byte strings."""
        original = b"hello world"
        encoded = bencode_encode(original)
        decoded, _ = bencode_decode(encoded)
        assert decoded == original

    def test_roundtrip_list(self):
        """Test roundtrip for lists."""
        original = [1, 2, b"three", [4, 5]]
        encoded = bencode_encode(original)
        decoded, _ = bencode_decode(encoded)
        assert decoded == original

    def test_roundtrip_dict(self):
        """Test roundtrip for dictionaries."""
        original = {b"name": b"test", b"value": 123}
        encoded = bencode_encode(original)
        decoded, _ = bencode_decode(encoded)
        assert decoded == original

    def test_roundtrip_complex_torrent_like_structure(self):
        """Test roundtrip for a structure similar to a torrent file."""
        original = {
            b"announce": b"http://tracker.example.com/announce",
            b"info": {
                b"name": b"TestFile.txt",
                b"length": 1024,
                b"piece length": 16384,
                b"pieces": b"\x00" * 20,  # SHA1 hashes
            },
        }
        encoded = bencode_encode(original)
        decoded, _ = bencode_decode(encoded)
        assert decoded == original


class TestExtractInfoHash:
    """Tests for extracting info hash from torrent files."""

    def test_extract_hash_from_simple_torrent(self):
        """Test extracting hash from a simple torrent structure."""
        info_dict = {
            b"name": b"test.txt",
            b"length": 100,
            b"piece length": 16384,
            b"pieces": b"\x00" * 20,
        }
        torrent = {b"info": info_dict}
        torrent_bytes = bencode_encode(torrent)

        result = extract_info_hash_from_torrent(torrent_bytes)

        # Should return a 40-character hex string
        assert result is not None
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_extract_hash_returns_none_for_invalid(self):
        """Test that invalid data returns None."""
        assert extract_info_hash_from_torrent(b"not a torrent") is None
        assert extract_info_hash_from_torrent(b"") is None

    def test_extract_hash_returns_none_without_info(self):
        """Test that torrent without info dict returns None."""
        torrent = {b"announce": b"http://tracker.example.com"}
        torrent_bytes = bencode_encode(torrent)

        result = extract_info_hash_from_torrent(torrent_bytes)
        assert result is None

    def test_extract_hash_is_consistent(self):
        """Test that same torrent always produces same hash."""
        info_dict = {b"name": b"consistent.txt", b"length": 500}
        torrent = {b"info": info_dict}
        torrent_bytes = bencode_encode(torrent)

        hash1 = extract_info_hash_from_torrent(torrent_bytes)
        hash2 = extract_info_hash_from_torrent(torrent_bytes)

        assert hash1 == hash2

    def test_extract_hash_different_for_different_torrents(self):
        """Test that different torrents produce different hashes."""
        torrent1 = {b"info": {b"name": b"file1.txt", b"length": 100}}
        torrent2 = {b"info": {b"name": b"file2.txt", b"length": 100}}

        hash1 = extract_info_hash_from_torrent(bencode_encode(torrent1))
        hash2 = extract_info_hash_from_torrent(bencode_encode(torrent2))

        assert hash1 != hash2

    def test_extract_hash_v2_without_pieces(self):
        """Use SHA-256 when torrent lacks v1 pieces."""
        info_dict = {
            b"meta version": 2,
            b"file tree": {b"test.txt": {b"": {b"length": 123}}},
            b"piece length": 16384,
        }
        torrent = {b"info": info_dict}
        torrent_bytes = bencode_encode(torrent)

        expected = hashlib.sha256(bencode_encode(info_dict)).hexdigest().lower()
        assert extract_info_hash_from_torrent(torrent_bytes) == expected


class TestExtractTorrentInfo:
    """Tests for extracting torrent info from user-supplied URLs."""

    def test_fetches_untrusted_torrent_url_to_recover_missing_hash(self, monkeypatch):
        """Regression for #1012.

        A download URL on a non-Prowlarr origin (e.g. a direct tracker link, or
        Prowlarr reached through a separate proxy) must still be prefetched so
        the info_hash can be recovered when the feed did not provide one.
        Otherwise qBittorrent fails with "Could not determine torrent hash from
        URL".
        """
        info_dict = {
            b"name": b"book.txt",
            b"length": 100,
            b"piece length": 16384,
            b"pieces": b"\x00" * 20,
        }
        torrent_data = bencode_encode({b"info": info_dict})
        expected_hash = hashlib.sha1(bencode_encode(info_dict)).hexdigest().lower()

        config_values = {"PROWLARR_URL": "https://prowlarr.example"}
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        response = MagicMock(status_code=200, content=torrent_data)
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        # No expected_hash supplied: the hash can only come from the prefetch.
        result = extract_torrent_info(
            "https://tracker.example/download/book.torrent",
            fetch_torrent=True,
        )

        assert result.info_hash == expected_hash
        assert result.torrent_data == torrent_data
        assert result.is_magnet is False
        mock_get.assert_called_once()

    def test_does_not_send_api_key_to_untrusted_torrent_url(self, monkeypatch):
        """The Prowlarr API key is never sent to a download URL on an untrusted origin."""
        info_dict = {
            b"name": b"book.txt",
            b"length": 100,
            b"piece length": 16384,
            b"pieces": b"\x00" * 20,
        }
        torrent_data = bencode_encode({b"info": info_dict})

        config_values = {
            "PROWLARR_URL": "https://prowlarr.example",
            "PROWLARR_API_KEY": "secret",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        response = MagicMock(status_code=200, content=torrent_data)
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        extract_torrent_info(
            "https://attacker.example/book.torrent",
            fetch_torrent=True,
        )

        mock_get.assert_called_once()
        sent_headers = mock_get.call_args.kwargs.get("headers", {})
        assert "X-Api-Key" not in sent_headers

    def test_fetches_configured_prowlarr_torrent_url(self, monkeypatch):
        """Configured Prowlarr download URLs can still be prefetched and parsed."""
        info_dict = {
            b"name": b"trusted.txt",
            b"length": 100,
            b"piece length": 16384,
            b"pieces": b"\x00" * 20,
        }
        torrent_data = bencode_encode({b"info": info_dict})
        expected_hash = hashlib.sha1(bencode_encode(info_dict)).hexdigest().lower()

        config_values = {
            "PROWLARR_URL": "https://prowlarr.example",
            "PROWLARR_API_KEY": "secret",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        response = MagicMock(status_code=200, content=torrent_data)
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        result = extract_torrent_info(
            "https://prowlarr.example/1/download?apikey=secret&indexer=7",
            fetch_torrent=True,
        )

        assert result.info_hash == expected_hash
        assert result.torrent_data == torrent_data
        assert result.is_magnet is False
        mock_get.assert_called_once()

    def test_normalizes_configured_origin_before_trusting_torrent_url(self, monkeypatch):
        """Configured Prowlarr URLs match the same normalization used by the source."""
        info_dict = {
            b"name": b"trusted.txt",
            b"length": 100,
            b"piece length": 16384,
            b"pieces": b"\x00" * 20,
        }
        torrent_data = bencode_encode({b"info": info_dict})
        expected_hash = hashlib.sha1(bencode_encode(info_dict)).hexdigest().lower()

        config_values = {
            "PROWLARR_URL": "prowlarr.example:9696/",
            "PROWLARR_API_KEY": "secret",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        response = MagicMock(status_code=200, content=torrent_data)
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        result = extract_torrent_info(
            "http://prowlarr.example:9696/1/download?apikey=secret&indexer=7",
            fetch_torrent=True,
        )

        assert result.info_hash == expected_hash
        assert result.torrent_data == torrent_data
        mock_get.assert_called_once()

    def test_follows_trusted_redirect_to_untrusted_host_without_api_key(self, monkeypatch):
        """Regression for #1012 on v1.3.2.

        Prowlarr's download endpoint commonly redirects to the indexer's own
        download link on another origin. The prefetch must follow that redirect
        (it is often the only way to learn the info_hash) but must not forward
        the Prowlarr API key to the untrusted origin.
        """
        info_dict = {
            b"name": b"book.txt",
            b"length": 100,
            b"piece length": 16384,
            b"pieces": b"\x00" * 20,
        }
        torrent_data = bencode_encode({b"info": info_dict})
        expected_hash = hashlib.sha1(bencode_encode(info_dict)).hexdigest().lower()

        config_values = {
            "PROWLARR_URL": "https://prowlarr.example",
            "PROWLARR_API_KEY": "secret",
        }
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.config.get",
            lambda key, default="": config_values.get(key, default),
        )
        redirect = MagicMock(status_code=302)
        redirect.headers = {"Location": "https://tracker.example/download/book.torrent"}
        final = MagicMock(status_code=200, content=torrent_data)
        final.raise_for_status = MagicMock()
        mock_get = MagicMock(side_effect=[redirect, final])
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        # No expected_hash supplied: the hash can only come from the prefetch.
        result = extract_torrent_info(
            "https://prowlarr.example/1/download?apikey=secret&indexer=7",
            fetch_torrent=True,
        )

        assert result.info_hash == expected_hash
        assert result.torrent_data == torrent_data
        assert result.is_magnet is False
        assert mock_get.call_count == 2
        trusted_headers = mock_get.call_args_list[0].kwargs["headers"]
        untrusted_headers = mock_get.call_args_list[1].kwargs["headers"]
        assert trusted_headers.get("X-Api-Key") == "secret"
        assert "X-Api-Key" not in untrusted_headers

    def test_redirect_to_magnet_link_returns_magnet_info(self, monkeypatch):
        """A download URL redirecting to a magnet link yields the magnet's hash."""
        magnet = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=test"
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.config.get",
            lambda key, default="": "https://prowlarr.example" if key == "PROWLARR_URL" else "",
        )
        response = MagicMock(status_code=302)
        response.headers = {"Location": magnet}
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        result = extract_torrent_info(
            "https://prowlarr.example/1/download?apikey=secret&indexer=7",
            fetch_torrent=True,
        )

        assert result.is_magnet is True
        assert result.magnet_url == magnet
        assert result.info_hash == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
        mock_get.assert_called_once()

    def test_gives_up_after_too_many_redirects(self, monkeypatch):
        """A redirect loop falls back to the expected hash instead of spinning."""
        expected_hash = "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.config.get",
            lambda key, default="": "https://prowlarr.example" if key == "PROWLARR_URL" else "",
        )
        response = MagicMock(status_code=302)
        response.headers = {"Location": "https://tracker.example/loop"}
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        result = extract_torrent_info(
            "https://prowlarr.example/1/download?apikey=secret&indexer=7",
            fetch_torrent=True,
            expected_hash=expected_hash,
        )

        assert result.info_hash == expected_hash
        assert result.torrent_data is None
        assert result.is_magnet is False
        # Initial request plus the maximum of five followed redirects.
        assert mock_get.call_count == 6


class TestTorrentFetchHopCheck:
    """Each hop of the torrent fetch must point at a public address."""

    @staticmethod
    def _setup(monkeypatch, resolutions, responses, config_values=None):
        values = config_values or {"PROWLARR_URL": "https://prowlarr.example"}
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.config.get",
            lambda key, default="": values.get(key, default),
        )
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils._resolve_host_addresses",
            lambda host: resolutions[host],
        )
        mock_get = MagicMock(side_effect=responses)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)
        return mock_get

    @staticmethod
    def _redirect(location):
        response = MagicMock(status_code=302)
        response.headers = {"Location": location}
        return response

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "169.254.169.254",
            "10.0.0.5",
            "192.168.1.10",
            "::1",
            "fe80::1",
            "::ffff:127.0.0.1",
        ],
    )
    def test_redirect_to_non_public_address_is_refused(self, monkeypatch, address):
        mock_get = self._setup(
            monkeypatch,
            {"prowlarr.example": ["10.0.0.2"], "evil.example": [address]},
            [self._redirect("http://evil.example/x")],
        )

        result = extract_torrent_info("https://prowlarr.example/1/download", fetch_torrent=True)

        assert result.torrent_data is None
        assert result.fetch_error
        mock_get.assert_called_once()

    def test_any_non_public_answer_refuses_the_hop(self, monkeypatch):
        mock_get = self._setup(
            monkeypatch,
            {"mixed.example": ["93.184.216.34", "127.0.0.1"]},
            [MagicMock(status_code=200, content=b"")],
        )

        result = extract_torrent_info("https://mixed.example/a.torrent", fetch_torrent=True)

        assert result.fetch_error
        mock_get.assert_not_called()

    def test_initial_url_to_loopback_is_refused(self, monkeypatch):
        mock_get = self._setup(monkeypatch, {"127.0.0.1": ["127.0.0.1"]}, [])

        result = extract_torrent_info("http://127.0.0.1:8080/a.torrent", fetch_torrent=True)

        assert result.fetch_error
        mock_get.assert_not_called()

    def test_non_http_redirect_scheme_is_refused(self, monkeypatch):
        mock_get = self._setup(
            monkeypatch,
            {"prowlarr.example": ["10.0.0.2"]},
            [self._redirect("ftp://prowlarr.example/x")],
        )

        result = extract_torrent_info("https://prowlarr.example/1/download", fetch_torrent=True)

        assert result.fetch_error
        mock_get.assert_called_once()

    def test_configured_prowlarr_host_may_be_on_the_lan(self, monkeypatch):
        info_dict = {b"name": b"b", b"length": 1, b"piece length": 16384, b"pieces": b"\x00" * 20}
        torrent_data = bencode_encode({b"info": info_dict})
        final = MagicMock(status_code=200, content=torrent_data)
        final.raise_for_status = MagicMock()
        mock_get = self._setup(
            monkeypatch,
            {"prowlarr.lan": ["192.168.1.20"]},
            [final],
            {"PROWLARR_URL": "http://prowlarr.lan:9696", "PROWLARR_API_KEY": "secret"},
        )

        result = extract_torrent_info(
            "http://prowlarr.lan:9696/1/download?indexer=7", fetch_torrent=True
        )

        assert result.torrent_data == torrent_data
        assert mock_get.call_args.kwargs["headers"].get("X-Api-Key") == "secret"

    def test_unresolvable_name_reports_a_fetch_error(self, monkeypatch):
        def fail(host):
            raise OSError("Name or service not known")

        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.config.get",
            lambda key, default="": default,
        )
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils._resolve_host_addresses", fail
        )
        mock_get = MagicMock()
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        result = extract_torrent_info("https://nowhere.invalid/a.torrent", fetch_torrent=True)

        assert result.fetch_error
        mock_get.assert_not_called()


class TestExtractHashFromMagnet:
    """Tests for extracting hash from magnet links."""

    def test_extract_hash_from_hex_magnet(self):
        """Test extracting 40-char hex hash from magnet link."""
        magnet = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=test"
        result = extract_hash_from_magnet(magnet)
        assert result == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"

    def test_extract_hash_from_base32_magnet(self):
        """Test extracting 32-char base32 hash from magnet link."""
        # Base32 encoded hash: "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
        magnet = "magnet:?xt=urn:btih:ABCDEFGHIJKLMNOPQRSTUVWXYZ234567&dn=test"
        result = extract_hash_from_magnet(magnet)
        # Should be converted to hex (lowercase)
        assert result is not None
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_extract_hash_uppercase_hex(self):
        """Test that uppercase hex is converted to lowercase."""
        magnet = "magnet:?xt=urn:btih:3B245504CF5F11BBDBE1201CEA6A6BF45AEE1BC0&dn=test"
        result = extract_hash_from_magnet(magnet)
        assert result == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"

    def test_extract_hash_no_btih(self):
        """Test that magnets without btih return None."""
        magnet = "magnet:?dn=test"
        result = extract_hash_from_magnet(magnet)
        assert result is None

    def test_extract_hash_invalid_format(self):
        """Test that invalid hash format returns None."""
        magnet = "magnet:?xt=urn:btih:invalid&dn=test"
        result = extract_hash_from_magnet(magnet)
        assert result is None

    def test_extract_hash_not_magnet(self):
        """Test that non-magnet URLs return None."""
        result = extract_hash_from_magnet("https://example.com/file.torrent")
        assert result is None

    def test_extract_hash_empty_string(self):
        """Test that empty string returns None."""
        result = extract_hash_from_magnet("")
        assert result is None

    def test_extract_hash_complex_magnet(self):
        """Test extracting from complex magnet with many parameters."""
        magnet = (
            "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
            "&dn=Ubuntu+22.04"
            "&tr=udp://tracker.example.com:80"
            "&tr=udp://tracker2.example.com:6969"
            "&xl=12345"
        )
        result = extract_hash_from_magnet(magnet)
        assert result == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"

    def test_extract_hash_from_btmh_hex(self):
        """Test extracting v2 hash from btmh (hex multihash)."""
        digest = bytes(range(1, 33))
        multihash = b"\x12\x20" + digest
        magnet = f"magnet:?xt=urn:btmh:{multihash.hex()}&dn=test"
        result = extract_hash_from_magnet(magnet)
        assert result == digest.hex()

    def test_extract_hash_from_btmh_base32(self):
        """Test extracting v2 hash from btmh (base32 multihash)."""
        digest = bytes(range(1, 33))
        multihash = b"\x12\x20" + digest
        b32 = base64.b32encode(multihash).decode("ascii").rstrip("=")
        magnet = f"magnet:?xt=urn:btmh:{b32}&dn=test"
        result = extract_hash_from_magnet(magnet)
        assert result == digest.hex()


class TestTorrentFetchCache:
    """Tests for reusing a fetched .torrent across find_existing/add_download (#1111)."""

    @staticmethod
    def _valid_torrent():
        info_dict = {
            b"name": b"book.txt",
            b"length": 100,
            b"piece length": 16384,
            b"pieces": b"\x00" * 20,
        }
        torrent_data = bencode_encode({b"info": info_dict})
        info_hash = hashlib.sha1(bencode_encode(info_dict)).hexdigest().lower()
        return torrent_data, info_hash

    def test_repeated_url_reuses_fetched_torrent_data(self, monkeypatch):
        """The same download URL is fetched once per add attempt, not once per caller.

        find_existing() and add_download() both resolve the request URL; private
        tracker links behind Prowlarr's proxy can be rate-limited or single-use,
        so the second fetch must be served from the cache.
        """
        torrent_data, info_hash = self._valid_torrent()
        response = MagicMock(status_code=200, content=torrent_data)
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        url = "https://prowlarr.example/26/download?apikey=secret&link=token"
        first = extract_torrent_info(url, fetch_torrent=True)
        second = extract_torrent_info(url, fetch_torrent=True)

        mock_get.assert_called_once()
        assert first.info_hash == info_hash
        assert second.info_hash == info_hash
        assert second.torrent_data == torrent_data

    def test_failed_fetch_is_not_cached_and_records_reason(self, monkeypatch):
        """Fetch failures are retried on the next call and expose the reason."""
        import requests as requests_module

        mock_get = MagicMock(
            side_effect=requests_module.exceptions.HTTPError(
                "500 Server Error: Internal Server Error for url: https://prowlarr.example/26/download"
            )
        )
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        url = "https://prowlarr.example/26/download?apikey=secret&link=token"
        first = extract_torrent_info(url, fetch_torrent=True)
        second = extract_torrent_info(url, fetch_torrent=True)

        assert mock_get.call_count == 2
        assert first.fetch_error is not None
        assert "500 Server Error" in first.fetch_error
        assert first.info_hash is None
        assert second.fetch_error is not None

    def test_fetch_failure_still_falls_back_to_expected_hash(self, monkeypatch):
        """A known infohash keeps working when the prefetch fails."""
        import requests as requests_module

        mock_get = MagicMock(side_effect=requests_module.exceptions.ConnectionError("boom"))
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        known_hash = "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
        result = extract_torrent_info(
            "https://tracker.example/download/book.torrent",
            fetch_torrent=True,
            expected_hash=known_hash,
        )

        assert result.info_hash == known_hash
        assert result.fetch_error is not None
        assert "boom" in result.fetch_error

    def test_expected_hash_applies_to_cached_hashless_result(self, monkeypatch):
        """A cached fetch without a hash still honors a caller's expected_hash."""
        response = MagicMock(status_code=200, content=b"<html>not a torrent</html>")
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        url = "https://tracker.example/download/book.torrent"
        known_hash = "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
        first = extract_torrent_info(url, fetch_torrent=True)
        second = extract_torrent_info(url, fetch_torrent=True, expected_hash=known_hash)

        mock_get.assert_called_once()
        assert first.info_hash is None
        assert second.info_hash == known_hash

    def test_cache_expires_after_ttl(self, monkeypatch):
        """Stale cache entries are refetched instead of reused."""
        torrent_data, _ = self._valid_torrent()
        response = MagicMock(status_code=200, content=torrent_data)
        response.raise_for_status = MagicMock()
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        clock = {"now": 1000.0}
        monkeypatch.setattr(
            "shelfmark.download.clients.torrent_utils.time.monotonic",
            lambda: clock["now"],
        )

        url = "https://tracker.example/download/book.torrent"
        extract_torrent_info(url, fetch_torrent=True)
        clock["now"] += 121.0
        extract_torrent_info(url, fetch_torrent=True)

        assert mock_get.call_count == 2

    def test_magnet_redirect_result_is_reused(self, monkeypatch):
        """A URL that redirects to a magnet link is also only resolved once."""
        magnet = "magnet:?xt=urn:btih:3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0&dn=test"
        response = MagicMock(status_code=302, headers={"Location": magnet})
        mock_get = MagicMock(return_value=response)
        monkeypatch.setattr("shelfmark.download.clients.torrent_utils.requests.get", mock_get)

        url = "https://tracker.example/download/book.torrent"
        first = extract_torrent_info(url, fetch_torrent=True)
        second = extract_torrent_info(url, fetch_torrent=True)

        mock_get.assert_called_once()
        assert first.is_magnet is True
        assert second.magnet_url == magnet
        assert second.info_hash == "3b245504cf5f11bbdbe1201cea6a6bf45aee1bc0"
