"""Multi-book ("pack") release planning.

A pack is one release that contains several books: a whole-series torrent with one
subfolder per book, or a flat folder of `Series 1.0 - Title.m4b` files. The same
planning rules serve pre-download inspection (the file list comes from the release
source) and post-processing (the file list comes from disk), so what the user
approved in the modal is what gets filed.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from shelfmark.core.utils import AUDIOBOOK_FORMATS

# m4b/m4a hold a whole audiobook in one file; every other audio format (mp3, flac, ...) is
# chaptered - many files make up one book. Ebook formats are always one file per book, so
# only chaptered *audio* matters here. A flat folder is split one-book-per-file only when
# none of its files are chaptered audio: a bare list of `01 - Chapter.mp3` tracks is a
# single chaptered audiobook, not a pack of books.
_SINGLE_FILE_AUDIO_CONTAINERS = frozenset({"m4b", "m4a"})
_CHAPTERED_AUDIO_EXTENSIONS = frozenset(AUDIOBOOK_FORMATS) - _SINGLE_FILE_AUDIO_CONTAINERS

_YEAR_SUFFIX_RE = re.compile(r"\s*\(\s*(?P<year>\d{4})\s*\)\s*$")
_SERIES_MARKER_RE = re.compile(
    r"""
    ^\s*
    (?:
        \[\s*\#?(?P<bracket>\d+(?:\.\d+)?)\s*\]     # [03] / [#3]
      | \#(?P<hash>\d+(?:\.\d+)?)                    # #3
      | book\.?\s*(?P<book>\d+(?:\.\d+)?)            # Book 3 / Book. 03
      | (?P<plain>\d+(?:\.\d+)?)(?=[\s\-:.])         # 03 - / 1.0 - / 3.
    )
    \s*(?:[-:.]\s*)?
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SEPARATOR_CHARS = " \t-_:."
# "Gods of Risk 2.5 - Gods of Risk": the title repeated on both sides of the position.
_REPEATED_TITLE_RE = re.compile(
    r"^(?P<left>.+?)\s+(?P<position>\d+(?:\.\d+)?)\s*[-:\u2013]\s*(?P<right>.+)$"
)
_SERIES_LABEL_WORDS = r"(?:novella|novellas|short\s+story|short|story|novel)"
# "Uncrowned Cradle, Book 7" / "Reaper Cradle, Volume 10" / "Wintersteel (Cradle, Book 8)":
# an explicit word marks the position at the END of the name. A bare trailing number
# is deliberately not matched — "Title - 02" is a chapter, not a series position.
_TRAILING_MARKER_RE = re.compile(
    r"""
    [\s,\-:\u2013(]*
    (?:book|volume|vol\.?)\s*\#?(?P<position>\d+(?:\.\d+)?)
    \s*\)?\s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)
# AudiobookBay renders a file inside a folder as "<folder> <file>" with no separator,
# so a pack row reads "Author - Title Series, Book 1 Title Series, Book 1".
_GLUED_FOLDER_RE = re.compile(
    r"^(?P<prefix>.+?\s[-\u2013]\s)?(?P<core>.+?)\s+(?P=core)$", re.IGNORECASE
)


@dataclass(frozen=True)
class PackFile:
    """One file inside a release, path relative to the release root."""

    path: str
    size: int | None = None


@dataclass(frozen=True)
class PackBook:
    """One book split out of a pack, files as release-relative paths."""

    title: str
    series_position: float | None
    year: int | None
    files: list[str]


@dataclass(frozen=True)
class PackPlan:
    books: list[PackBook]
    ignored: list[str]

    @property
    def is_pack(self) -> bool:
        return len(self.books) > 1


@dataclass(frozen=True)
class BookGroup:
    """One book's on-disk files, ready for transfer."""

    title: str
    series_position: float | None
    year: int | None
    files: list[Path]


def _strip_series_name(name: str, series_name: str | None) -> str:
    if not series_name:
        return name
    prefix = series_name.strip()
    if not prefix or not name.lower().startswith(prefix.lower()):
        return name
    remainder = name[len(prefix) :]
    if remainder and remainder[0].isalnum():
        return name
    return remainder.lstrip(_SEPARATOR_CHARS)


def _strip_series_label(work: str, series_name: str | None) -> str:
    """Drop a leading "An <Series> Novella - " style label that some packs prepend."""
    if not series_name:
        return work
    # "The Expanse" is labelled "An Expanse Novella", so match without the article.
    core = re.sub(r"^(?:the|an?)\s+", "", series_name.strip(), flags=re.IGNORECASE)
    if not core:
        return work
    pattern = re.compile(
        rf"^(?:an?\s+|the\s+)?{re.escape(core)}\s+{_SERIES_LABEL_WORDS}\s*[-:\u2013]\s*",
        re.IGNORECASE,
    )
    return pattern.sub("", work, count=1)


def _collapse_glued_folder(name: str) -> str:
    match = _GLUED_FOLDER_RE.match(name)
    if not match:
        return name
    prefix = match.group("prefix") or ""
    core = match.group("core")
    # "Author - X X" → "Author - X" (the folder carried the author, the file did not).
    return (prefix + core).strip()


def _strip_author_name(name: str, author_name: str | None) -> str:
    """Drop a leading "Author - " (packs are often filed as `Author - Title`)."""
    if not author_name:
        return name
    prefix = author_name.strip()
    if not prefix or not name.lower().startswith(prefix.lower()):
        return name
    remainder = name[len(prefix) :]
    stripped = remainder.lstrip(_SEPARATOR_CHARS + "\u2013")
    if stripped == remainder:  # no separator after the author: part of the title
        return name
    return stripped


def _strip_trailing_series_name(work: str, series_name: str | None) -> str:
    """Drop a trailing series name left behind by a trailing position marker."""
    if not series_name:
        return work
    suffix = series_name.strip()
    if not suffix or not work.lower().endswith(suffix.lower()):
        return work
    remainder = work[: -len(suffix)]
    stripped = remainder.rstrip(_SEPARATOR_CHARS + ",(\u2013")
    if not stripped or stripped == remainder:
        return work
    return stripped


def parse_pack_book_name(
    name: str, *, series_name: str | None, author_name: str | None = None
) -> tuple[str, float | None, int | None]:
    """Split a book folder/file-stem name into (title, series position, year).

    Strips a leading series name, a leading position marker (`Book 3 - `, `03 - `,
    `1.0 - `, `3. `, `[03] `, `#3 `) and a trailing `(YYYY)`. Also understands a
    trailing marker (`Title Series, Book 3`, `Title (Series, Volume 3)`), a leading
    `Author - `, and AudiobookBay's glued `<folder> <file>` names. Returns the name
    unchanged with no position/year when nothing would be left of the title.
    """
    work = _collapse_glued_folder(name.strip())
    work = _strip_author_name(work, author_name)
    work = _strip_series_name(work, series_name)

    year: int | None = None
    year_match = _YEAR_SUFFIX_RE.search(work)
    if year_match:
        year = int(year_match.group("year"))
        work = work[: year_match.start()]

    position: float | None = None
    repeated = _REPEATED_TITLE_RE.match(work.strip())
    if (
        repeated
        and repeated.group("left").strip().lower() == repeated.group("right").strip().lower()
    ):
        return repeated.group("right").strip(), float(repeated.group("position")), year

    marker = _SERIES_MARKER_RE.match(work)
    if marker:
        raw = (
            marker.group("bracket")
            or marker.group("hash")
            or marker.group("book")
            or marker.group("plain")
        )
        position = float(raw)
        work = work[marker.end() :]
    else:
        trailing = _TRAILING_MARKER_RE.search(work)
        if trailing and trailing.start() > 0:
            position = float(trailing.group("position"))
            work = _strip_trailing_series_name(work[: trailing.start()], series_name)

    work = _strip_series_label(work, series_name)
    title = work.strip().strip(_SEPARATOR_CHARS).strip()
    if not title:
        return name, None, None
    return title, position, year


def _book_from_name(
    name: str, files: list[str], series_name: str | None, author_name: str | None = None
) -> PackBook:
    title, position, year = parse_pack_book_name(
        name, series_name=series_name, author_name=author_name
    )
    return PackBook(title=title, series_position=position, year=year, files=files)


def _common_root_parts(paths: list[PurePosixPath]) -> tuple[str, ...]:
    parents = [p.parent.parts for p in paths]
    common: list[str] = []
    for parts in zip(*parents, strict=False):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    return tuple(common)


def plan_pack(
    files: list[PackFile],
    *,
    supported_extensions: set[str],
    series_name: str | None,
    author_name: str | None = None,
    root_depth: int | None = None,
) -> PackPlan:
    """Group a release's file list into books.

    Files in a subfolder (relative to the common root) group by that subfolder. Files
    directly in the root split one-book-per-file only when at least two of them carry
    a series position in their names; otherwise they are one book (a chaptered
    audiobook, e.g. `01.mp3`, `02.mp3`). `root_depth` fixes how many leading path
    components form the root instead of deriving it from the files' common parent.
    """
    supported = {ext.lower().lstrip(".") for ext in supported_extensions}
    book_files: list[PurePosixPath] = []
    ignored: list[str] = []
    for pack_file in files:
        rel = PurePosixPath(pack_file.path.replace("\\", "/").lstrip("./"))
        if rel.suffix.lower().lstrip(".") in supported:
            book_files.append(rel)
        else:
            ignored.append(pack_file.path)

    if not book_files:
        return PackPlan(books=[], ignored=ignored)

    root_parts = (
        _common_root_parts(book_files) if root_depth is None else book_files[0].parts[:root_depth]
    )
    depth = len(root_parts)

    root_files: list[PurePosixPath] = []
    folders: dict[str, list[str]] = {}
    for rel in book_files:
        remainder = rel.parts[depth:]
        if len(remainder) > 1:
            folders.setdefault(remainder[0], []).append(str(rel))
        else:
            root_files.append(rel)

    books: list[PackBook] = []
    if root_files:
        parsed = [
            parse_pack_book_name(f.stem, series_name=series_name, author_name=author_name)
            for f in root_files
        ]
        positions = {p[1] for p in parsed if p[1] is not None}
        titles = {p[0].strip().lower() for p in parsed if p[0]}
        one_book_per_file = all(
            rel.suffix.lower().lstrip(".") not in _CHAPTERED_AUDIO_EXTENSIONS for rel in root_files
        )
        # Split a flat folder into a book per file only with real evidence of distinct
        # books: two or more series positions, more than one title, and no chaptered audio
        # (a bare list of `01 - Chapter.mp3` tracks is one book, not a pack).
        if len(positions) >= 2 and len(titles) >= 2 and one_book_per_file:
            books.extend(
                PackBook(title=title, series_position=position, year=year, files=[str(f)])
                for f, (title, position, year) in zip(root_files, parsed, strict=True)
            )
        elif len(root_files) == 1:
            books.append(
                _book_from_name(root_files[0].stem, [str(root_files[0])], series_name, author_name)
            )
        else:
            group_name = root_parts[-1] if root_parts else ""
            books.append(
                _book_from_name(group_name, [str(f) for f in root_files], series_name, author_name)
            )

    books.extend(
        _book_from_name(folder, paths, series_name, author_name)
        for folder, paths in folders.items()
    )
    return PackPlan(books=books, ignored=ignored)


def _relative_paths(
    book_files: list[Path], root: Path | None = None
) -> tuple[Path, dict[Path, str]]:
    if root is None:
        root = Path(os.path.commonpath([str(f.parent) for f in book_files]))
    return root, {f: f.relative_to(root).as_posix() for f in book_files}


def group_files_into_books(
    book_files: list[Path],
    *,
    series_name: str | None,
    author_name: str | None = None,
    root: Path | None = None,
) -> list[BookGroup]:
    """Heuristically split on-disk files into books (see `plan_pack`).

    `root` pins the release root when grouping a subset of a larger file set.
    """
    if not book_files:
        return []
    _root, rel_by_path = _relative_paths(book_files, root)
    path_by_rel = {rel: path for path, rel in rel_by_path.items()}
    extensions = {f.suffix.lower().lstrip(".") for f in book_files}
    plan = plan_pack(
        [PackFile(rel) for rel in rel_by_path.values()],
        supported_extensions=extensions,
        series_name=series_name,
        author_name=author_name,
        root_depth=None if root is None else 0,
    )
    return [
        BookGroup(
            title=book.title,
            series_position=book.series_position,
            year=book.year,
            files=[path_by_rel[rel] for rel in book.files],
        )
        for book in plan.books
    ]


def _single_best(scores: dict[Path, int]) -> Path | None:
    """Return the highest-scoring path, or None when nothing scored or the best ties."""
    if not scores:
        return None
    best = max(scores.values())
    top = [path for path, score in scores.items() if score == best]
    return top[0] if len(top) == 1 else None


def _match_ignored_entry(entry: str, parts_by_path: dict[Path, tuple[str, ...]]) -> Path | None:
    """Find the one on-disk file a skipped release path refers to (see `drop_ignored_files`)."""
    wanted = PurePosixPath(entry).parts
    if not wanted:
        return None
    for path, parts in parts_by_path.items():
        if parts == wanted:
            return path

    # The root can shift after extraction, so one path may end with the other. The file
    # sharing the most trailing components wins: a skipped `Extras/Book.epub` is the
    # subfolder copy, not a kept `Book.epub` in the root.
    shared: dict[Path, int] = {}
    for path, parts in parts_by_path.items():
        depth = min(len(parts), len(wanted))
        if parts[-depth:] == wanted[-depth:]:
            shared[path] = depth
    if (match := _single_best(shared)) is not None:
        return match

    same_name = [path for path, parts in parts_by_path.items() if parts[-1] == wanted[-1]]
    if len(same_name) == 1:
        return same_name[0]

    # AudiobookBay glues "<folder> <file>" into one name; the file whose trailing
    # components, joined the same way, end that name on the most components wins.
    glued = wanted[-1].lower()
    tails: dict[Path, int] = {}
    for path, parts in parts_by_path.items():
        for depth in range(len(parts), 0, -1):
            tail = " ".join(parts[-depth:]).lower()
            if glued == tail or glued.endswith(" " + tail):
                tails[path] = depth
                break
    return _single_best(tails)


def drop_ignored_files(book_files: list[Path], ignored: list[str] | None) -> list[Path]:
    """Remove files the user chose to skip in the pack review.

    Ignored entries are release-relative paths, and each one drops at most one file:
    the file at that relative path, else the one sharing the most trailing components
    with it (the root can shift after extraction), else the only file with that
    basename, else the one its AudiobookBay-style glued "<folder> <file>" name ends
    with. A tie drops nothing, since keeping a skipped file beats losing a kept one.
    """
    if not book_files or not ignored:
        return book_files
    _root, rel_by_path = _relative_paths(book_files)
    parts_by_path = {path: PurePosixPath(rel).parts for path, rel in rel_by_path.items()}
    wanted = {i.replace("\\", "/").lstrip("./") for i in ignored if i and i.strip()}
    dropped = {
        match
        for entry in wanted
        if (match := _match_ignored_entry(entry, parts_by_path)) is not None
    }
    return [p for p in book_files if p not in dropped]


def match_plan_to_files(
    plan: list[PackBook],
    book_files: list[Path],
    *,
    series_name: str | None = None,
    author_name: str | None = None,
) -> list[BookGroup]:
    """Apply an approved plan to on-disk files.

    Files match by release-relative path first, then by basename (archive extraction
    and client save paths can shift the root), then by the on-disk basename being a
    suffix of the planned name (sources that glue folder and file names together).
    Book files the plan does not mention fall back to heuristic grouping so nothing
    is silently dropped.
    """
    if not book_files:
        return []
    root, rel_by_path = _relative_paths(book_files)
    by_rel = {rel: path for path, rel in rel_by_path.items()}
    by_name: dict[str, list[Path]] = {}
    for path in book_files:
        by_name.setdefault(path.name, []).append(path)

    claimed: set[Path] = set()
    groups: list[BookGroup] = []
    for book in plan:
        matched: list[Path] = []
        for wanted in book.files:
            wanted_rel = wanted.replace("\\", "/").lstrip("./")
            candidate = by_rel.get(wanted_rel)
            if candidate is None:
                candidates = [
                    p for p in by_name.get(PurePosixPath(wanted_rel).name, []) if p not in claimed
                ]
                candidate = candidates[0] if candidates else None
            if candidate is None:
                wanted_name = PurePosixPath(wanted_rel).name.lower()
                candidates = [
                    p
                    for p in book_files
                    if p not in claimed and wanted_name.endswith(p.name.lower())
                ]
                candidate = candidates[0] if len(candidates) == 1 else None
            if candidate is not None and candidate not in claimed:
                claimed.add(candidate)
                matched.append(candidate)
        if matched:
            groups.append(
                BookGroup(
                    title=book.title,
                    series_position=book.series_position,
                    year=book.year,
                    files=matched,
                )
            )

    unmatched = [p for p in book_files if p not in claimed]
    still_unmatched: list[Path] = []
    for p in unmatched:
        p_ext = p.suffix.lower().lstrip(".")
        is_chaptered = p_ext in _CHAPTERED_AUDIO_EXTENSIONS

        matching_groups = [g for g in groups if any(f.parent == p.parent for f in g.files)]

        target_group: BookGroup | None = None
        if len(matching_groups) == 1 and is_chaptered:
            target_group = matching_groups[0]
        elif len(plan) == 1 and len(groups) == 1 and is_chaptered:
            target_group = groups[0]

        if target_group is not None:
            target_group.files.append(p)
            claimed.add(p)
        else:
            still_unmatched.append(p)

    if still_unmatched:
        groups.extend(
            group_files_into_books(
                still_unmatched, series_name=series_name, author_name=author_name, root=root
            )
        )
    return groups
