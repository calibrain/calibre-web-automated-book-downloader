"""Stream several downloaded files to a client as one ZIP, without staging the archive."""

import os
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

_ZIP_STREAM_CHUNK_SIZE = 1024 * 1024


class _ChunkSink:
    """Write-only ZipFile target that hands the written bytes back to the caller.

    Having no tell() or seek() makes ZipFile treat it as an unseekable stream: each
    entry's CRC and sizes go in a trailing data descriptor instead of being patched
    back into the local header, so nothing has to be held until the entry ends.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def write(self, data: bytes) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        """Nothing is buffered beyond what drain() hands out."""

    def close(self) -> None:
        """Nothing to release; the caller drains what is left after ZipFile closes."""

    def drain(self) -> Iterator[bytes]:
        chunks, self._chunks = self._chunks, []
        yield from chunks


def zip_entry_names(paths: list[str]) -> list[tuple[Path, str]]:
    """Pair each file with its name inside the archive.

    Names are relative to the files' common parent, so a book's own folders
    (``Title/Part 01.m4b``) survive while the library path above them does not.
    """
    files = [Path(path) for path in paths]
    root = Path(os.path.commonpath([str(file.parent) for file in files]))
    return [(file, file.relative_to(root).as_posix()) for file in files]


def stream_zip(paths: list[str]) -> Iterator[bytes]:
    """Yield an uncompressed ZIP of ``paths``, reading each file once in chunks.

    Stored, not deflated: audiobooks and ebooks are already compressed, so deflating
    them would cost CPU for next to nothing. ZIP64 kicks in per entry from the file
    size, which a multi-gigabyte audiobook needs.
    """
    sink = _ChunkSink()
    with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        for path, arcname in zip_entry_names(paths):
            info = zipfile.ZipInfo.from_file(path, arcname, strict_timestamps=False)
            info.compress_type = zipfile.ZIP_STORED
            with path.open("rb") as source, archive.open(info, "w") as target:
                while chunk := source.read(_ZIP_STREAM_CHUNK_SIZE):
                    target.write(chunk)
                    yield from sink.drain()
            yield from sink.drain()
    yield from sink.drain()
