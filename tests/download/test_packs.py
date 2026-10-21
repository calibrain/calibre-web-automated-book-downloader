"""Tests for multi-book pack planning (shelfmark.download.postprocess.packs)."""

from pathlib import Path

import pytest

from shelfmark.download.postprocess.packs import (
    PackBook,
    PackFile,
    group_files_into_books,
    match_plan_to_files,
    parse_pack_book_name,
    plan_pack,
)

AUDIO = {"m4b", "mp3"}


class TestParsePackBookName:
    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Book 3 - Howling Dark", ("Howling Dark", 3.0, None)),
            ("Book 03: Howling Dark", ("Howling Dark", 3.0, None)),
            ("03 - Empire of Silence", ("Empire of Silence", 3.0, None)),
            ("2.5 - Interlude", ("Interlude", 2.5, None)),
            ("[03] Empire of Silence", ("Empire of Silence", 3.0, None)),
            ("#3 Empire of Silence", ("Empire of Silence", 3.0, None)),
            ("3. Empire of Silence", ("Empire of Silence", 3.0, None)),
            ("Empire of Silence", ("Empire of Silence", None, None)),
            ("Empire of Silence (2018)", ("Empire of Silence", None, 2018)),
        ],
    )
    def test_strips_series_markers(self, name, expected):
        assert parse_pack_book_name(name, series_name=None) == expected

    def test_strips_leading_series_name_and_trailing_year(self):
        assert parse_pack_book_name(
            "The Expanse 1.0 - Leviathan Wakes (2011)", series_name="The Expanse"
        ) == ("Leviathan Wakes", 1.0, 2011)

    def test_series_name_match_is_case_insensitive(self):
        assert parse_pack_book_name(
            "the expanse 2.5 - Gods of Risk", series_name="The Expanse"
        ) == (
            "Gods of Risk",
            2.5,
            None,
        )

    @pytest.mark.parametrize(
        "name",
        [
            "The Expanse 0.2 - An Expanse Novella - The Churn (2014)",
            "The Expanse 0.2 - The Expanse Novella - The Churn (2014)",
            "The Expanse 0.2 - An Expanse Short Story - The Churn (2014)",
        ],
    )
    def test_strips_series_novella_label(self, name):
        assert parse_pack_book_name(name, series_name="The Expanse") == ("The Churn", 0.2, 2014)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Uncrowned Cradle, Book 7", ("Uncrowned", 7.0, None)),
            ("Reaper Cradle, Volume 10", ("Reaper", 10.0, None)),
            ("Soulsmith  Cradle, Book 2", ("Soulsmith", 2.0, None)),
            ("Wintersteel - Cradle Book 8", ("Wintersteel", 8.0, None)),
            ("Wintersteel (Cradle, Book 8)", ("Wintersteel", 8.0, None)),
            # A trailing bare number is a chapter/part, never a series position.
            ("Unsouled - 02", ("Unsouled - 02", None, None)),
        ],
    )
    def test_trailing_series_marker(self, name, expected):
        assert parse_pack_book_name(name, series_name="Cradle") == expected

    def test_trailing_series_name_only_stripped_with_a_marker(self):
        # "Stories from Cradle" is the title; nothing marks a position, so keep it.
        assert parse_pack_book_name("Threshold: Stories from Cradle", series_name="Cradle") == (
            "Threshold: Stories from Cradle",
            None,
            None,
        )

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            # AudiobookBay renders "<folder> <file>" as one flat string.
            (
                "Will Wight - Unsouled Cradle, Book 1 Will Wight - Unsouled Cradle, Book 1",
                ("Unsouled", 1.0, None),
            ),
            (
                "Will Wight - Skysworn Cradle, Book 4 Skysworn Cradle, Book 4",
                ("Skysworn", 4.0, None),
            ),
            ("Will Wight - Bloodline Cradle, Book 9", ("Bloodline", 9.0, None)),
        ],
    )
    def test_strips_author_prefix_and_glued_folder_name(self, name, expected):
        assert (
            parse_pack_book_name(name, series_name="Cradle", author_name="Will Wight") == expected
        )

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Gods of Risk 2.5 - Gods of Risk", ("Gods of Risk", 2.5, None)),
            ("Cibola Burn 4 - Cibola Burn (2014)", ("Cibola Burn", 4.0, 2014)),
            ("cibola burn 4 - Cibola Burn", ("Cibola Burn", 4.0, None)),
            # Different text on each side is a real "Series N - Title" name, not a repeat.
            ("Sun Eater 2 - Howling Dark", ("Sun Eater 2 - Howling Dark", None, None)),
        ],
    )
    def test_collapses_title_repeated_around_the_position(self, name, expected):
        assert parse_pack_book_name(name, series_name=None) == expected

    def test_bare_numeric_title_is_left_alone(self):
        assert parse_pack_book_name("1984", series_name=None) == ("1984", None, None)

    def test_marker_that_would_leave_nothing_is_left_alone(self):
        assert parse_pack_book_name("Book 3", series_name=None) == ("Book 3", None, None)


class TestPlanPack:
    def test_nested_subfolders_become_separate_books(self):
        files = [
            PackFile("Sun Eater/Book 1 - Empire of Silence/Empire of Silence.m4b", 10),
            PackFile("Sun Eater/Book 2 - Howling Dark/Howling Dark.m4b", 20),
            PackFile("Sun Eater/cover.jpg", 1),
        ]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert plan.is_pack
        assert [b.title for b in plan.books] == ["Empire of Silence", "Howling Dark"]
        assert [b.series_position for b in plan.books] == [1.0, 2.0]
        assert plan.books[0].files == ["Sun Eater/Book 1 - Empire of Silence/Empire of Silence.m4b"]
        assert plan.ignored == ["Sun Eater/cover.jpg"]

    def test_flat_pack_becomes_one_book_per_file_and_ignores_sidecars(self):
        files = [
            PackFile("The Expanse 1.0 - Leviathan Wakes (2011).m4b", 100),
            PackFile("The Expanse 1.0 - Leviathan Wakes (2011).txt", 1),
            PackFile("The Expanse 2.0 - Caliban's War (2012).m4b", 100),
        ]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name="The Expanse")
        assert plan.is_pack
        assert [(b.title, b.series_position, b.year) for b in plan.books] == [
            ("Leviathan Wakes", 1.0, 2011),
            ("Caliban's War", 2.0, 2012),
        ]
        assert plan.ignored == ["The Expanse 1.0 - Leviathan Wakes (2011).txt"]

    def test_flat_chaptered_mp3_tracks_are_one_book_not_a_pack(self):
        # A single audiobook whose chapters are named "01 - <chapter>.mp3" must not be
        # split into one "book" per track just because each name carries a number.
        files = [
            PackFile("The Hobbit/01 - An Unexpected Party.mp3", 10),
            PackFile("The Hobbit/02 - Roast Mutton.mp3", 10),
            PackFile("The Hobbit/03 - A Short Rest.mp3", 10),
        ]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert not plan.is_pack
        assert len(plan.books) == 1
        assert plan.books[0].files == [
            "The Hobbit/01 - An Unexpected Party.mp3",
            "The Hobbit/02 - Roast Mutton.mp3",
            "The Hobbit/03 - A Short Rest.mp3",
        ]

    def test_flat_repeated_title_mp3_tracks_are_one_book(self):
        # Same title on every track ("01 - The Hobbit.mp3") is a chaptered book too.
        files = [
            PackFile("01 - The Hobbit.mp3", 10),
            PackFile("02 - The Hobbit.mp3", 10),
        ]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert not plan.is_pack
        assert len(plan.books) == 1

    def test_audiobookbay_flat_list_with_trailing_markers_is_a_pack(self):
        # ABB's file table has no folder separators: "<folder> <file> <size>".
        files = [
            PackFile(
                "Will Wight - Unsouled Cradle, Book 1 Will Wight - Unsouled Cradle, Book 1.sfv", 1
            ),
            PackFile(
                "Will Wight - Unsouled Cradle, Book 1 Will Wight - Unsouled Cradle, Book 1.m4a", 9
            ),
            PackFile("Will Wight - Skysworn Cradle, Book 4 Skysworn Cradle, Book 4.m4b", 9),
            PackFile("Uncrowned Cradle, Book 7.m4b", 9),
            PackFile(
                "Will Wight - Reaper Cradle, Volume 10 Will Wight - Reaper Cradle, Volume 10.m4b", 9
            ),
        ]
        plan = plan_pack(
            files,
            supported_extensions={"m4a", "m4b"},
            series_name="Cradle",
            author_name="Will Wight",
        )
        assert plan.is_pack
        assert [(b.title, b.series_position) for b in plan.books] == [
            ("Unsouled", 1.0),
            ("Skysworn", 4.0),
            ("Uncrowned", 7.0),
            ("Reaper", 10.0),
        ]

    def test_deeper_nesting_collapses_onto_book_folder(self):
        files = [
            PackFile("Book 1/CD1/01.mp3"),
            PackFile("Book 1/CD2/01.mp3"),
            PackFile("Book 2/01.mp3"),
        ]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert [b.files for b in plan.books] == [
            ["Book 1/CD1/01.mp3", "Book 1/CD2/01.mp3"],
            ["Book 2/01.mp3"],
        ]

    def test_single_wrapping_folder_is_not_a_book_boundary(self):
        # A torrent named "Series" containing one multi-part book is a single book.
        files = [PackFile("Series/Book 1/01.mp3"), PackFile("Series/Book 1/02.mp3")]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert not plan.is_pack
        assert len(plan.books) == 1

    def test_root_files_and_subfolders_coexist(self):
        files = [PackFile("Novella.m4b"), PackFile("Book 1/a.m4b"), PackFile("Book 1/b.m4b")]
        plan = plan_pack(files, supported_extensions=AUDIO, series_name=None)
        assert [b.files for b in plan.books] == [["Novella.m4b"], ["Book 1/a.m4b", "Book 1/b.m4b"]]

    def test_single_file_is_not_a_pack(self):
        plan = plan_pack([PackFile("Book.m4b")], supported_extensions=AUDIO, series_name=None)
        assert not plan.is_pack
        assert plan.books[0].title == "Book"

    def test_empty_input(self):
        plan = plan_pack([], supported_extensions=AUDIO, series_name=None)
        assert plan.books == []
        assert not plan.is_pack


class TestGroupFilesIntoBooks:
    def test_groups_on_disk_files_by_top_level_folder(self, tmp_path: Path):
        a = tmp_path / "Book 1 - A" / "a.m4b"
        b = tmp_path / "Book 2 - B" / "b.m4b"
        for f in (a, b):
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")
        groups = group_files_into_books([a, b], series_name=None)
        assert [(g.title, g.series_position, g.files) for g in groups] == [
            ("A", 1.0, [a]),
            ("B", 2.0, [b]),
        ]


class TestMatchPlanToFiles:
    def test_matches_by_relative_path_then_basename(self, tmp_path: Path):
        root = tmp_path / "staging" / "Sun Eater"
        a = root / "Book 1 - A" / "a.m4b"
        b = root / "Book 2 - B" / "b.m4b"
        for f in (a, b):
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")
        plan = [
            PackBook(title="Alpha", series_position=1.0, year=2001, files=["Book 1 - A/a.m4b"]),
            PackBook(title="Beta", series_position=2.0, year=None, files=["b.m4b"]),
        ]
        groups = match_plan_to_files(plan, [a, b])
        assert [(g.title, g.series_position, g.year, g.files) for g in groups] == [
            ("Alpha", 1.0, 2001, [a]),
            ("Beta", 2.0, None, [b]),
        ]

    def test_matches_glued_plan_path_by_basename_suffix(self, tmp_path: Path):
        # The plan came from ABB's "<folder> <file>" strings; on disk the file sits in a folder.
        root = tmp_path / "Cradle - Will Wight Books 1-10"
        a = root / "Will Wight - Skysworn Cradle, Book 4" / "Skysworn Cradle, Book 4.m4b"
        b = root / "Uncrowned Cradle, Book 7.m4b"
        for f in (a, b):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")
        plan = [
            PackBook(
                title="Skysworn",
                series_position=4.0,
                year=None,
                files=["Will Wight - Skysworn Cradle, Book 4 Skysworn Cradle, Book 4.m4b"],
            ),
            PackBook(
                title="Uncrowned",
                series_position=7.0,
                year=None,
                files=["Uncrowned Cradle, Book 7.m4b"],
            ),
        ]
        groups = match_plan_to_files(plan, [a, b])
        assert [(g.title, g.files) for g in groups] == [("Skysworn", [a]), ("Uncrowned", [b])]

    def test_unmatched_files_fall_back_to_heuristic_groups(self, tmp_path: Path):
        a = tmp_path / "Book 1 - A" / "a.m4b"
        c = tmp_path / "Book 3 - C" / "c.m4b"
        for f in (a, c):
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")
        plan = [PackBook(title="Alpha", series_position=1.0, year=None, files=["Book 1 - A/a.m4b"])]
        groups = match_plan_to_files(plan, [a, c])
        assert [(g.title, g.files) for g in groups] == [("Alpha", [a]), ("C", [c])]

    def test_unmatched_chaptered_audio_files_share_group_in_same_folder(self, tmp_path: Path):
        # Issue #1176: Indexer/metadata lists only a subset of tracks, remaining tracks in
        # the same folder must append to the existing book group rather than splitting.
        root = tmp_path / "Westwell - Hot & Cold (2023)"
        root.mkdir(parents=True)
        files = [root / f"track_{i:03d}.flac" for i in range(1, 6)]
        for f in files:
            f.write_bytes(b"x")

        plan = [
            PackBook(
                title="Westwell - Hot & Cold (2023)",
                series_position=None,
                year=2023,
                files=[
                    f"Westwell - Hot & Cold (2023)/{files[0].name}",
                    f"Westwell - Hot & Cold (2023)/{files[1].name}",
                ],
            )
        ]
        groups = match_plan_to_files(plan, files)
        assert len(groups) == 1
        assert groups[0].title == "Westwell - Hot & Cold (2023)"
        assert groups[0].files == files

    def test_single_book_plan_chaptered_audio_multi_disc_grouped_together(self, tmp_path: Path):
        # Single-book plan with multi-disc audio files: unmatched CD2 tracks belong to the book
        root = tmp_path / "Audiobook"
        cd1_file = root / "CD1" / "01.mp3"
        cd2_file = root / "CD2" / "01.mp3"
        for f in (cd1_file, cd2_file):
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")

        plan = [
            PackBook(
                title="Audiobook",
                series_position=1.0,
                year=None,
                files=["Audiobook/CD1/01.mp3"],
            )
        ]
        groups = match_plan_to_files(plan, [cd1_file, cd2_file])
        assert len(groups) == 1
        assert groups[0].title == "Audiobook"
        assert cd1_file in groups[0].files
        assert cd2_file in groups[0].files

    def test_multi_book_pack_unmatched_chaptered_audio_attaches_to_corresponding_book(
        self, tmp_path: Path
    ):
        # In a multi-book pack, unmatched tracks in Book 1's folder stay in Book 1
        root = tmp_path / "Series Pack"
        b1_t1 = root / "Book 1" / "01.mp3"
        b1_t2 = root / "Book 1" / "02.mp3"
        b2_t1 = root / "Book 2" / "01.mp3"
        b2_t2 = root / "Book 2" / "02.mp3"
        for f in (b1_t1, b1_t2, b2_t1, b2_t2):
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"x")

        plan = [
            PackBook(
                title="Book 1", series_position=1.0, year=None, files=["Series Pack/Book 1/01.mp3"]
            ),
            PackBook(
                title="Book 2", series_position=2.0, year=None, files=["Series Pack/Book 2/01.mp3"]
            ),
        ]
        groups = match_plan_to_files(plan, [b1_t1, b1_t2, b2_t1, b2_t2])
        assert len(groups) == 2
        assert groups[0].title == "Book 1"
        assert set(groups[0].files) == {b1_t1, b1_t2}
        assert groups[1].title == "Book 2"
        assert set(groups[1].files) == {b2_t1, b2_t2}


class TestDropIgnoredFiles:
    def test_drops_by_relative_path_and_keeps_others(self, tmp_path: Path):
        from shelfmark.download.postprocess.packs import drop_ignored_files

        files = [tmp_path / "A" / "a.m4b", tmp_path / "A" / "a.epub", tmp_path / "B" / "b.m4b"]
        assert drop_ignored_files(files, ["A/a.epub"]) == [files[0], files[2]]

    def test_basename_fallback_only_when_unique(self, tmp_path: Path):
        from shelfmark.download.postprocess.packs import drop_ignored_files

        files = [tmp_path / "A" / "01.mp3", tmp_path / "B" / "01.mp3", tmp_path / "B" / "x.mp3"]
        assert drop_ignored_files(files, ["x.mp3"]) == files[:2]
        assert drop_ignored_files(files, ["Z/01.mp3"]) == files

    def test_no_ignored_is_a_noop(self, tmp_path: Path):
        from shelfmark.download.postprocess.packs import drop_ignored_files

        files = [tmp_path / "a.m4b"]
        assert drop_ignored_files(files, None) is files

    @pytest.mark.parametrize("skipped", ["Extras/Book.epub", "Pack/Extras/Book.epub"])
    def test_a_skipped_subfolder_file_keeps_the_root_file_of_the_same_name(
        self, tmp_path: Path, skipped: str
    ):
        from shelfmark.download.postprocess.packs import drop_ignored_files

        kept = tmp_path / "Pack" / "Book.epub"
        files = [kept, tmp_path / "Pack" / "Extras" / "Book.epub"]
        assert drop_ignored_files(files, [skipped]) == [kept]

    def test_an_ambiguous_skip_drops_nothing(self, tmp_path: Path):
        from shelfmark.download.postprocess.packs import drop_ignored_files

        files = [tmp_path / "X" / "Book.epub", tmp_path / "Y" / "Book.epub"]
        assert drop_ignored_files(files, ["Book.epub"]) == files

    def test_drops_audiobookbay_glued_folder_and_file_names(self, tmp_path: Path):
        from shelfmark.download.postprocess.packs import drop_ignored_files

        root = tmp_path / "Cradle - Will Wight Books 1-10"
        skysworn = root / "Will Wight - Skysworn Cradle, Book 4" / "Skysworn Cradle, Book 4.m4b"
        uncrowned = root / "Uncrowned Cradle, Book 7.m4b"
        files = [skysworn, uncrowned]
        glued = "Will Wight - Skysworn Cradle, Book 4 Skysworn Cradle, Book 4.m4b"
        assert drop_ignored_files(files, [glued]) == [uncrowned]

    def test_glued_names_pick_the_right_folder_for_chaptered_tracks(self, tmp_path: Path):
        from shelfmark.download.postprocess.packs import drop_ignored_files

        files = [
            tmp_path / "Pack" / "Book 1" / "01.mp3",
            tmp_path / "Pack" / "Book 1" / "02.mp3",
            tmp_path / "Pack" / "Book 2" / "01.mp3",
            tmp_path / "Pack" / "Book 2" / "02.mp3",
        ]
        assert drop_ignored_files(files, ["Book 2 01.mp3", "Book 2 02.mp3"]) == files[:2]
