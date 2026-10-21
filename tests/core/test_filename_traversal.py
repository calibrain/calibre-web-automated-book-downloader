import pytest

from shelfmark.core.models import build_filename, normalize_format, resolve_staged_path


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("epub", "epub"),
        ("EPUB", "epub"),
        (".mobi", "mobi"),
        (" pdf ", "pdf"),
        ("azw3", "azw3"),
        ("m4b", "m4b"),
        ("mp3", "mp3"),
        ("cbz", "cbz"),
        ("fb2", "fb2"),
        ("/../../../config/x", None),
        ("../x", None),
        ("a/b", None),
        ("a\\b", None),
        ("ep.ub", None),
        ("epub\x00", None),
        ("epub\n", "epub"),
        ("", None),
        (None, None),
        (5, None),
        ("abcdefghijk", None),
    ],
)
def test_normalize_format(raw, expected):
    assert normalize_format(raw) == expected


@pytest.mark.parametrize("fmt", ["/../../../config/x", "../x", "a/b", "a\\b", "x\x00y"])
def test_build_filename_never_makes_path_component_from_format(fmt):
    name = build_filename(".", None, None, fmt)
    assert "/" not in name
    assert "\\" not in name
    assert "\x00" not in name
    assert name == "."


def test_build_filename_keeps_legitimate_format():
    assert (
        build_filename("Dune", "Frank Herbert", "1965", "EPUB")
        == "Frank Herbert - Dune (1965).epub"
    )


def test_resolve_staged_path_accepts_plain_child(tmp_path):
    assert resolve_staged_path(tmp_path, "book.epub") == tmp_path / "book.epub"


@pytest.mark.parametrize("name", ["", ".", "..", "../x", "a/b", "/etc/passwd"])
def test_resolve_staged_path_rejects_escapes(tmp_path, name):
    with pytest.raises(ValueError, match="outside"):
        resolve_staged_path(tmp_path, name)


def test_resolve_staged_path_rejects_symlink_out(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (base / "link").symlink_to(outside / "file")
    with pytest.raises(ValueError, match="outside"):
        resolve_staged_path(base, "link")
