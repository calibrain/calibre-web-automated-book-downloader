"""Multi-book packs are filed one book at a time through the normal pipeline."""

import os
from pathlib import Path
from threading import Event
from unittest.mock import patch

from shelfmark.core.models import DownloadTask, SearchMode
from tests.core.test_processing_integration import _build_config, _sync_config


def _run(temp_path: Path, task: DownloadTask, ingest: Path, staging: Path, **config_kwargs):
    from shelfmark.download.postprocess.router import post_process_download

    statuses: list[tuple[str, str | None]] = []
    with (
        patch("shelfmark.core.config.config") as mock_config,
        patch("shelfmark.config.env.TMP_DIR", staging),
    ):
        mock_config.get = _build_config(
            ingest,
            organization=config_kwargs.pop("organization", "organize"),
            supported_audiobook_formats=["m4b", "mp3"],
            audiobook_organize_template=config_kwargs.pop(
                "audiobook_organize_template", "{Author}/{Title}/{Title}{ - PartNumber}"
            ),
            **config_kwargs,
        )
        mock_config.CUSTOM_SCRIPT = None
        _sync_config(mock_config, mock_config)
        result = post_process_download(
            temp_path, task, Event(), lambda s, m=None: statuses.append((s, m))
        )
    return result, statuses


def _nested_pack(root: Path) -> Path:
    pack = root / "Sun Eater"
    for folder, name in (
        ("Book 1 - Empire of Silence", "empire.m4b"),
        ("Book 2 - Howling Dark", "howling.m4b"),
    ):
        (pack / folder).mkdir(parents=True)
        (pack / folder / name).write_text(name)
    (pack / "cover.jpg").write_text("img")
    return pack


def _audiobook_task(**overrides) -> DownloadTask:
    fields = {
        "task_id": "pack-1",
        "source": "direct_download",
        "title": "Drive",
        "author": "James S. A. Corey",
        "content_type": "audiobook",
        "series_name": "The Expanse",
        "series_position": 2.6,
        "search_mode": SearchMode.UNIVERSAL,
    }
    fields.update(overrides)
    return DownloadTask(**fields)


def test_approved_plan_files_each_book_with_its_own_title(tmp_path):
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    pack = staging / "Expanse"
    pack.mkdir()
    for name in (
        "The Expanse 1.0 - Leviathan Wakes (2011).m4b",
        "The Expanse 2.0 - Caliban's War (2012).m4b",
    ):
        (pack / name).write_text(name)
    (pack / "The Expanse 1.0 - Leviathan Wakes (2011).txt").write_text("notes")

    task = _audiobook_task(
        title="Sun Eater",  # the searched book; must not name the pack's books
        book_plan=[
            {
                "title": "Leviathan Wakes (edited)",
                "series_position": 1.0,
                "year": 2011,
                "files": ["The Expanse 1.0 - Leviathan Wakes (2011).m4b"],
            },
            {
                "title": "Caliban's War",
                "series_position": 2.0,
                "year": 2012,
                "files": ["The Expanse 2.0 - Caliban's War (2012).m4b"],
            },
        ],
    )

    result, statuses = _run(pack, task, ingest, staging)

    assert result is not None
    author_dir = ingest / "James S. A. Corey"
    assert sorted(p.name for p in author_dir.iterdir()) == [
        "Caliban's War",
        "Leviathan Wakes (edited)",
    ]
    assert (author_dir / "Leviathan Wakes (edited)" / "Leviathan Wakes (edited).m4b").exists()
    assert (author_dir / "Caliban's War" / "Caliban's War.m4b").exists()
    assert statuses[-1] == ("complete", "Complete (2 books, 2 files)")


def test_multi_book_flag_splits_nested_pack_heuristically(tmp_path):
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    pack = _nested_pack(staging)

    result, _ = _run(pack, _audiobook_task(multi_book=True), ingest, staging)

    assert result is not None
    author_dir = ingest / "James S. A. Corey"
    assert (author_dir / "Empire of Silence" / "Empire of Silence.m4b").exists()
    assert (author_dir / "Howling Dark" / "Howling Dark.m4b").exists()


def test_pack_book_series_position_does_not_leak_from_searched_book(tmp_path):
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    pack = staging / "Two"
    for folder in ("Alpha", "Beta"):
        (pack / folder).mkdir(parents=True)
        (pack / folder / f"{folder.lower()}.m4b").write_text(folder)

    result, _ = _run(
        pack,
        _audiobook_task(multi_book=True),
        ingest,
        staging,
        audiobook_organize_template="{Author}/{SeriesPosition - }{Title}/{Title}",
    )

    assert result is not None
    assert sorted(p.name for p in (ingest / "James S. A. Corey").iterdir()) == ["Alpha", "Beta"]


def test_multifile_book_inside_pack_keeps_part_numbers_per_book(tmp_path):
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    pack = staging / "Pack"
    (pack / "Book 1 - One").mkdir(parents=True)
    (pack / "Book 2 - Two").mkdir(parents=True)
    for i in (1, 2, 3):
        (pack / "Book 1 - One" / f"part{i}.mp3").write_text(str(i))
    (pack / "Book 2 - Two" / "two.mp3").write_text("t")

    result, statuses = _run(pack, _audiobook_task(multi_book=True), ingest, staging)

    assert result is not None
    one = ingest / "James S. A. Corey" / "One"
    assert sorted(p.name for p in one.iterdir()) == ["One - 01.mp3", "One - 02.mp3", "One - 03.mp3"]
    assert (ingest / "James S. A. Corey" / "Two" / "Two.mp3").exists()
    assert statuses[-1] == ("complete", "Complete (2 books, 4 files)")


def test_hardlinked_torrent_pack_leaves_source_tree_intact(tmp_path):
    downloads = tmp_path / "downloads"
    ingest = tmp_path / "ingest"
    downloads.mkdir()
    ingest.mkdir()
    pack = _nested_pack(downloads)
    task = _audiobook_task(source="prowlarr", multi_book=True, original_download_path=str(pack))

    result, _ = _run(pack, task, ingest, tmp_path / "staging", hardlink=True)

    assert result is not None
    empire_src = pack / "Book 1 - Empire of Silence" / "empire.m4b"
    empire_dst = ingest / "James S. A. Corey" / "Empire of Silence" / "Empire of Silence.m4b"
    assert empire_src.exists()
    assert empire_dst.exists()
    assert os.stat(empire_src).st_ino == os.stat(empire_dst).st_ino


def test_without_pack_fields_nested_pack_is_still_one_book(tmp_path):
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    pack = _nested_pack(staging)

    result, _ = _run(pack, _audiobook_task(), ingest, staging)

    assert result is not None
    drive = ingest / "James S. A. Corey" / "Drive"
    assert sorted(p.name for p in drive.iterdir()) == ["Drive - 01.m4b", "Drive - 02.m4b"]


def test_single_group_with_multi_book_flag_uses_searched_title(tmp_path):
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    pack = staging / "Series" / "Book 1 - Solo"
    pack.mkdir(parents=True)
    (pack / "solo.m4b").write_text("s")

    result, statuses = _run(pack.parent, _audiobook_task(multi_book=True), ingest, staging)

    assert result is not None
    assert (ingest / "James S. A. Corey" / "Drive" / "Drive.m4b").exists()
    assert statuses[-1] == ("complete", "Complete")


def test_ignored_book_is_not_filed_even_though_the_plan_omits_it(tmp_path):
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    pack = _nested_pack(tmp_path)

    task = _audiobook_task(
        book_plan=[
            {
                "title": "Empire of Silence",
                "series_position": 1.0,
                "year": None,
                "files": ["Book 1 - Empire of Silence/empire.m4b"],
            }
        ],
        ignored_files=["Book 2 - Howling Dark/howling.m4b"],
    )

    result, _statuses = _run(pack, task, ingest, staging)

    assert result is not None
    filed = sorted(p.name for p in ingest.rglob("*.m4b"))
    assert filed == ["Empire of Silence.m4b"]


def test_ignored_file_is_skipped_from_a_single_remaining_book(tmp_path):
    staging = tmp_path / "staging"
    ingest = tmp_path / "ingest"
    staging.mkdir()
    ingest.mkdir()
    pack = _nested_pack(tmp_path)

    task = _audiobook_task(
        multi_book=True,
        ignored_files=["Book 2 - Howling Dark/howling.m4b"],
    )

    result, _statuses = _run(pack, task, ingest, staging)

    assert result is not None
    assert [p.name for p in ingest.rglob("*.m4b")] != []
    assert not any("howling" in p.name.lower() for p in ingest.rglob("*"))
