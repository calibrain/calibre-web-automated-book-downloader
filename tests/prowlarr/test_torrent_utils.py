"""
Tests for torrent utility functions.

Tests:
- parse_transmission_url
- bencode_encode/decode
- extract_info_hash_from_torrent
- extract_hash_from_magnet
"""

import pytest

from cwa_book_downloader.release_sources.prowlarr.clients.torrent_utils import (
    parse_transmission_url,
    bencode_decode,
    bencode_encode,
    extract_info_hash_from_torrent,
    extract_hash_from_magnet,
)


class TestParseTransmissionUrl:
    """Tests for parse_transmission_url function."""

    def test_parse_simple_url(self):
        """Test parsing a simple URL with host and port."""
        host, port, path = parse_transmission_url("http://localhost:9091")
        assert host == "localhost"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_url_with_custom_port(self):
        """Test parsing URL with custom port."""
        host, port, path = parse_transmission_url("http://myserver:8080")
        assert host == "myserver"
        assert port == 8080
        assert path == "/transmission/rpc"

    def test_parse_url_with_path(self):
        """Test parsing URL with existing path."""
        host, port, path = parse_transmission_url("http://localhost:9091/transmission/rpc")
        assert host == "localhost"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_url_with_partial_path(self):
        """Test parsing URL with partial path appends /rpc."""
        host, port, path = parse_transmission_url("http://localhost:9091/custom")
        assert host == "localhost"
        assert port == 9091
        assert path == "/custom/transmission/rpc"

    def test_parse_url_with_trailing_slash(self):
        """Test parsing URL with trailing slash."""
        host, port, path = parse_transmission_url("http://localhost:9091/")
        assert host == "localhost"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_url_without_port(self):
        """Test parsing URL without port uses default 9091."""
        host, port, path = parse_transmission_url("http://transmission")
        assert host == "transmission"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_https_url(self):
        """Test parsing HTTPS URL."""
        host, port, path = parse_transmission_url("https://secure.transmission.local:9091")
        assert host == "secure.transmission.local"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_url_with_ip_address(self):
        """Test parsing URL with IP address."""
        host, port, path = parse_transmission_url("http://192.168.1.100:9091")
        assert host == "192.168.1.100"
        assert port == 9091
        assert path == "/transmission/rpc"

    def test_parse_empty_url_uses_defaults(self):
        """Test parsing empty URL uses localhost defaults."""
        host, port, path = parse_transmission_url("")
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
        result, remaining = bencode_decode(b"i-42e")
        assert result == -42

    def test_decode_zero(self):
        """Test decoding zero."""
        result, remaining = bencode_decode(b"i0e")
        assert result == 0

    def test_decode_large_integer(self):
        """Test decoding large integers."""
        result, remaining = bencode_decode(b"i999999999999e")
        assert result == 999999999999

    def test_decode_string(self):
        """Test decoding byte strings."""
        result, remaining = bencode_decode(b"5:hello")
        assert result == b"hello"
        assert remaining == b""

    def test_decode_empty_string(self):
        """Test decoding empty string."""
        result, remaining = bencode_decode(b"0:")
        assert result == b""

    def test_decode_unicode_string(self):
        """Test decoding unicode bytes."""
        data = "tëst".encode("utf-8")
        encoded = f"{len(data)}:".encode() + data
        result, remaining = bencode_decode(encoded)
        assert result == data

    def test_decode_list(self):
        """Test decoding lists."""
        result, remaining = bencode_decode(b"li1ei2ei3ee")
        assert result == [1, 2, 3]
        assert remaining == b""

    def test_decode_empty_list(self):
        """Test decoding empty list."""
        result, remaining = bencode_decode(b"le")
        assert result == []

    def test_decode_nested_list(self):
        """Test decoding nested lists."""
        result, remaining = bencode_decode(b"lli1eeli2eee")
        assert result == [[1], [2]]

    def test_decode_mixed_list(self):
        """Test decoding list with mixed types."""
        result, remaining = bencode_decode(b"l5:helloi42ee")
        assert result == [b"hello", 42]

    def test_decode_dict(self):
        """Test decoding dictionaries."""
        result, remaining = bencode_decode(b"d3:key5:valuee")
        assert result == {b"key": b"value"}
        assert remaining == b""

    def test_decode_empty_dict(self):
        """Test decoding empty dictionary."""
        result, remaining = bencode_decode(b"de")
        assert result == {}

    def test_decode_complex_structure(self):
        """Test decoding complex nested structures."""
        # Dict with string, int, and list values
        data = b"d3:agei25e4:name4:John5:itemsli1ei2ei3eee"
        result, remaining = bencode_decode(data)
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
        info_dict = {b"name": b"test.txt", b"length": 100}
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
