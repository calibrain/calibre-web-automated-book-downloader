"""Tests for natural sort and sequential part number assignment."""

import pytest
from pathlib import Path
from cwa_book_downloader.core.naming import natural_sort_key, assign_part_numbers


class TestNaturalSortKey:

    def test_simple_numbers(self):
        files = ["Part 2.mp3", "Part 10.mp3", "Part 1.mp3"]
        assert sorted(files, key=natural_sort_key) == ["Part 1.mp3", "Part 2.mp3", "Part 10.mp3"]

    def test_leading_zeros(self):
        files = ["Track 01.mp3", "Track 10.mp3", "Track 02.mp3"]
        assert sorted(files, key=natural_sort_key) == ["Track 01.mp3", "Track 02.mp3", "Track 10.mp3"]

    def test_case_insensitive(self):
        files = ["PART 2.mp3", "part 1.mp3", "Part 3.mp3"]
        assert sorted(files, key=natural_sort_key) == ["part 1.mp3", "PART 2.mp3", "Part 3.mp3"]

    def test_multiple_numbers_in_filename(self):
        files = ["CD2_Track10.mp3", "CD1_Track2.mp3", "CD1_Track10.mp3", "CD2_Track1.mp3"]
        assert sorted(files, key=natural_sort_key) == [
            "CD1_Track2.mp3", "CD1_Track10.mp3", "CD2_Track1.mp3", "CD2_Track10.mp3"
        ]

    def test_no_numbers(self):
        files = ["charlie.mp3", "alpha.mp3", "bravo.mp3"]
        assert sorted(files, key=natural_sort_key) == ["alpha.mp3", "bravo.mp3", "charlie.mp3"]

    def test_path_objects(self):
        files = [Path("file10.mp3"), Path("file2.mp3"), Path("file1.mp3")]
        assert [f.name for f in sorted(files, key=natural_sort_key)] == ["file1.mp3", "file2.mp3", "file10.mp3"]

    def test_uses_filename_only(self):
        files = [Path("/z/dir/file1.mp3"), Path("/a/dir/file2.mp3")]
        sorted_files = sorted(files, key=natural_sort_key)
        assert sorted_files[0].name == "file1.mp3"


class TestAssignPartNumbers:

    def test_empty_list(self):
        assert assign_part_numbers([]) == []

    def test_single_file(self):
        assert assign_part_numbers([Path("book.mp3")]) == [(Path("book.mp3"), "01")]

    def test_multiple_files_sorted(self):
        files = [Path("Part 3.mp3"), Path("Part 1.mp3"), Path("Part 2.mp3")]
        assert assign_part_numbers(files) == [
            (Path("Part 1.mp3"), "01"),
            (Path("Part 2.mp3"), "02"),
            (Path("Part 3.mp3"), "03"),
        ]

    def test_natural_sort_applied(self):
        files = [Path("Chapter 10.mp3"), Path("Chapter 2.mp3"), Path("Chapter 1.mp3")]
        assert assign_part_numbers(files) == [
            (Path("Chapter 1.mp3"), "01"),
            (Path("Chapter 2.mp3"), "02"),
            (Path("Chapter 10.mp3"), "03"),
        ]

    def test_custom_zero_padding(self):
        files = [Path("a.mp3"), Path("b.mp3")]
        assert assign_part_numbers(files, zero_pad_width=3) == [(Path("a.mp3"), "001"), (Path("b.mp3"), "002")]

    def test_many_files_padding(self):
        files = [Path(f"track_{i}.mp3") for i in range(100, 0, -1)]
        result = assign_part_numbers(files, zero_pad_width=3)
        assert result[0] == (Path("track_1.mp3"), "001")
        assert result[-1] == (Path("track_100.mp3"), "100")


class TestRealWorldScenarios:

    def test_standard_part_naming(self):
        files = [
            Path("The Way of Kings - Part 02.mp3"),
            Path("The Way of Kings - Part 01.mp3"),
            Path("The Way of Kings - Part 10.mp3"),
            Path("The Way of Kings - Part 03.mp3"),
        ]
        result = assign_part_numbers(files)
        assert [r[0].name for r in result] == [
            "The Way of Kings - Part 01.mp3",
            "The Way of Kings - Part 02.mp3",
            "The Way of Kings - Part 03.mp3",
            "The Way of Kings - Part 10.mp3",
        ]

    def test_cd_track_naming(self):
        files = [Path("CD02_Track01.mp3"), Path("CD01_Track02.mp3"), Path("CD01_Track01.mp3"), Path("CD02_Track02.mp3")]
        result = assign_part_numbers(files)
        assert [r[0].name for r in result] == [
            "CD01_Track01.mp3", "CD01_Track02.mp3", "CD02_Track01.mp3", "CD02_Track02.mp3"
        ]

    def test_disc_track_naming(self):
        files = [Path("Disc 1 - Track 10.mp3"), Path("Disc 1 - Track 2.mp3"), Path("Disc 2 - Track 1.mp3")]
        result = assign_part_numbers(files)
        assert [r[0].name for r in result] == [
            "Disc 1 - Track 2.mp3", "Disc 1 - Track 10.mp3", "Disc 2 - Track 1.mp3"
        ]

    def test_simple_numbered_files(self):
        files = [Path("02 Chapter Two.mp3"), Path("01 Chapter One.mp3"), Path("10 Chapter Ten.mp3")]
        result = assign_part_numbers(files)
        assert [r[0].name for r in result] == ["01 Chapter One.mp3", "02 Chapter Two.mp3", "10 Chapter Ten.mp3"]

    def test_bracketed_numbers(self):
        files = [Path("Book Title [03].mp3"), Path("Book Title [01].mp3"), Path("Book Title [02].mp3")]
        result = assign_part_numbers(files)
        assert [r[0].name for r in result] == ["Book Title [01].mp3", "Book Title [02].mp3", "Book Title [03].mp3"]


class TestNoFalsePositives:
    """Titles with numbers (451, 1984, etc.) don't cause issues with sequential assignment."""

    def test_fahrenheit_451(self):
        files = [Path("Fahrenheit 451 - Part 2.mp3"), Path("Fahrenheit 451 - Part 1.mp3")]
        result = assign_part_numbers(files)
        assert result[0] == (Path("Fahrenheit 451 - Part 1.mp3"), "01")
        assert result[1] == (Path("Fahrenheit 451 - Part 2.mp3"), "02")

    def test_1984(self):
        files = [Path("1984 - Chapter 03.mp3"), Path("1984 - Chapter 01.mp3"), Path("1984 - Chapter 02.mp3")]
        result = assign_part_numbers(files)
        assert [r[0].name for r in result] == ["1984 - Chapter 01.mp3", "1984 - Chapter 02.mp3", "1984 - Chapter 03.mp3"]

    def test_catch_22(self):
        files = [Path("Catch-22 Part 2.mp3"), Path("Catch-22 Part 1.mp3")]
        result = assign_part_numbers(files)
        assert result[0] == (Path("Catch-22 Part 1.mp3"), "01")

    def test_2001_a_space_odyssey(self):
        files = [Path("2001 A Space Odyssey - 02.mp3"), Path("2001 A Space Odyssey - 01.mp3")]
        result = assign_part_numbers(files)
        assert result[0][0].name == "2001 A Space Odyssey - 01.mp3"


class TestEdgeCases:

    def test_identical_filenames_different_dirs(self):
        files = [Path("/dir2/track.mp3"), Path("/dir1/track.mp3")]
        result = assign_part_numbers(files)
        assert len(result) == 2

    def test_unicode_filenames(self):
        files = [Path("日本語タイトル 02.mp3"), Path("日本語タイトル 01.mp3")]
        result = assign_part_numbers(files)
        assert result[0][0].name == "日本語タイトル 01.mp3"

    def test_very_large_numbers(self):
        files = [Path("track_1000.mp3"), Path("track_100.mp3"), Path("track_10.mp3")]
        result = assign_part_numbers(files)
        assert [r[0].name for r in result] == ["track_10.mp3", "track_100.mp3", "track_1000.mp3"]

    def test_mixed_extensions(self):
        files = [Path("track_2.m4b"), Path("track_1.mp3"), Path("track_3.flac")]
        result = assign_part_numbers(files)
        assert [r[0].name for r in result] == ["track_1.mp3", "track_2.m4b", "track_3.flac"]

    def test_no_numbers_alphabetical(self):
        files = [Path("zebra.mp3"), Path("apple.mp3"), Path("mango.mp3")]
        result = assign_part_numbers(files)
        assert [r[0].name for r in result] == ["apple.mp3", "mango.mp3", "zebra.mp3"]
