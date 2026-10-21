import io
import zipfile

from shelfmark.download.zip_stream import stream_zip, zip_entry_names


def test_stream_zip_holds_every_file_uncompressed(tmp_path):
    book = tmp_path / "Author" / "Title"
    book.mkdir(parents=True)
    (book / "Part 01.m4b").write_bytes(b"chapter one")
    (book / "Part 02.m4b").write_bytes(b"chapter two" * 1000)

    data = b"".join(stream_zip([str(book / "Part 01.m4b"), str(book / "Part 02.m4b")]))

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == ["Part 01.m4b", "Part 02.m4b"]
        assert archive.read("Part 01.m4b") == b"chapter one"
        assert archive.read("Part 02.m4b") == b"chapter two" * 1000
        assert {info.compress_type for info in archive.infolist()} == {zipfile.ZIP_STORED}


def test_stream_zip_yields_while_reading_rather_than_at_the_end(tmp_path, monkeypatch):
    import shelfmark.download.zip_stream as zip_stream

    monkeypatch.setattr(zip_stream, "_ZIP_STREAM_CHUNK_SIZE", 16)
    big = tmp_path / "big.mp3"
    big.write_bytes(b"x" * 160)

    chunks = list(stream_zip([str(big)]))

    assert len(chunks) > 10


def test_zip_entry_names_keep_folders_below_the_common_parent(tmp_path):
    first = tmp_path / "library" / "Book One" / "a.mp3"
    second = tmp_path / "library" / "Book Two" / "b.mp3"

    names = [name for _, name in zip_entry_names([str(first), str(second)])]

    assert names == ["Book One/a.mp3", "Book Two/b.mp3"]
