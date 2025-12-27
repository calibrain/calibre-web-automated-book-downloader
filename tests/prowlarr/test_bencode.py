"""
Tests for bencode encoding/decoding in the torrent utilities.
"""

import pytest

from cwa_book_downloader.release_sources.prowlarr.clients.torrent_utils import (
    bencode_encode as _bencode_encode,
    bencode_decode as _bencode_decode,
    extract_info_hash_from_torrent as _extract_info_hash_from_torrent,
)


class TestBencodeDecode:
    """Tests for bencode decoding."""

    def test_decode_integer(self):
        """Test decoding integers."""
        result, remaining = _bencode_decode(b"i42e")
        assert result == 42
        assert remaining == b""

    def test_decode_negative_integer(self):
        """Test decoding negative integers."""
        result, remaining = _bencode_decode(b"i-42e")
        assert result == -42

    def test_decode_zero(self):
        """Test decoding zero."""
        result, remaining = _bencode_decode(b"i0e")
        assert result == 0

    def test_decode_string(self):
        """Test decoding byte strings."""
        result, remaining = _bencode_decode(b"5:hello")
        assert result == b"hello"
        assert remaining == b""

    def test_decode_empty_string(self):
        """Test decoding empty string."""
        result, remaining = _bencode_decode(b"0:")
        assert result == b""

    def test_decode_list(self):
        """Test decoding lists."""
        result, remaining = _bencode_decode(b"li1ei2ei3ee")
        assert result == [1, 2, 3]
        assert remaining == b""

    def test_decode_empty_list(self):
        """Test decoding empty list."""
        result, remaining = _bencode_decode(b"le")
        assert result == []

    def test_decode_nested_list(self):
        """Test decoding nested lists."""
        result, remaining = _bencode_decode(b"lli1eeli2eee")
        assert result == [[1], [2]]

    def test_decode_dict(self):
        """Test decoding dictionaries."""
        result, remaining = _bencode_decode(b"d3:key5:valuee")
        assert result == {b"key": b"value"}
        assert remaining == b""

    def test_decode_empty_dict(self):
        """Test decoding empty dictionary."""
        result, remaining = _bencode_decode(b"de")
        assert result == {}

    def test_decode_complex_structure(self):
        """Test decoding complex nested structures."""
        # Dict with string, int, and list values
        data = b"d3:agei25e4:name4:John5:itemsli1ei2ei3eee"
        result, remaining = _bencode_decode(data)
        assert result == {
            b"age": 25,
            b"name": b"John",
            b"items": [1, 2, 3],
        }


class TestBencodeEncode:
    """Tests for bencode encoding."""

    def test_encode_integer(self):
        """Test encoding integers."""
        assert _bencode_encode(42) == b"i42e"
        assert _bencode_encode(-42) == b"i-42e"
        assert _bencode_encode(0) == b"i0e"

    def test_encode_bytes(self):
        """Test encoding byte strings."""
        assert _bencode_encode(b"hello") == b"5:hello"
        assert _bencode_encode(b"") == b"0:"

    def test_encode_string(self):
        """Test encoding regular strings (UTF-8 encoded)."""
        assert _bencode_encode("hello") == b"5:hello"
        assert _bencode_encode("") == b"0:"

    def test_encode_list(self):
        """Test encoding lists."""
        assert _bencode_encode([1, 2, 3]) == b"li1ei2ei3ee"
        assert _bencode_encode([]) == b"le"

    def test_encode_dict(self):
        """Test encoding dictionaries."""
        result = _bencode_encode({b"key": b"value"})
        assert result == b"d3:key5:valuee"

    def test_encode_dict_keys_sorted(self):
        """Test that dictionary keys are sorted."""
        # Keys should be sorted: b < z
        result = _bencode_encode({b"z": 1, b"a": 2, b"m": 3})
        # a=2, m=3, z=1
        assert result == b"d1:ai2e1:mi3e1:zi1ee"

    def test_encode_nested_structure(self):
        """Test encoding nested structures."""
        data = {b"list": [1, 2, 3], b"num": 42}
        result = _bencode_encode(data)
        # Keys sorted: "list" < "num"
        assert result == b"d4:listli1ei2ei3ee3:numi42ee"


class TestBencodeRoundTrip:
    """Tests for encoding then decoding (roundtrip)."""

    def test_roundtrip_integer(self):
        """Test roundtrip for integers."""
        original = 12345
        encoded = _bencode_encode(original)
        decoded, _ = _bencode_decode(encoded)
        assert decoded == original

    def test_roundtrip_bytes(self):
        """Test roundtrip for byte strings."""
        original = b"hello world"
        encoded = _bencode_encode(original)
        decoded, _ = _bencode_decode(encoded)
        assert decoded == original

    def test_roundtrip_list(self):
        """Test roundtrip for lists."""
        original = [1, 2, b"three", [4, 5]]
        encoded = _bencode_encode(original)
        decoded, _ = _bencode_decode(encoded)
        assert decoded == original

    def test_roundtrip_dict(self):
        """Test roundtrip for dictionaries."""
        original = {b"name": b"test", b"value": 123}
        encoded = _bencode_encode(original)
        decoded, _ = _bencode_decode(encoded)
        assert decoded == original


class TestExtractInfoHash:
    """Tests for extracting info hash from torrent files."""

    def test_extract_hash_from_simple_torrent(self):
        """Test extracting hash from a simple torrent structure."""
        # Create a minimal valid torrent structure
        info_dict = {b"name": b"test.txt", b"length": 100}
        torrent = {b"info": info_dict}
        torrent_bytes = _bencode_encode(torrent)

        result = _extract_info_hash_from_torrent(torrent_bytes)

        # Should return a 40-character hex string
        assert result is not None
        assert len(result) == 40
        assert all(c in "0123456789abcdef" for c in result)

    def test_extract_hash_returns_none_for_invalid(self):
        """Test that invalid data returns None."""
        assert _extract_info_hash_from_torrent(b"not a torrent") is None
        assert _extract_info_hash_from_torrent(b"") is None

    def test_extract_hash_returns_none_without_info(self):
        """Test that torrent without info dict returns None."""
        torrent = {b"announce": b"http://tracker.example.com"}
        torrent_bytes = _bencode_encode(torrent)

        result = _extract_info_hash_from_torrent(torrent_bytes)
        assert result is None

    def test_extract_hash_is_consistent(self):
        """Test that same torrent always produces same hash."""
        info_dict = {b"name": b"consistent.txt", b"length": 500}
        torrent = {b"info": info_dict}
        torrent_bytes = _bencode_encode(torrent)

        hash1 = _extract_info_hash_from_torrent(torrent_bytes)
        hash2 = _extract_info_hash_from_torrent(torrent_bytes)

        assert hash1 == hash2
