"""
Tests for the naming module - template parsing and library path building.
"""

import pytest
from pathlib import Path
import tempfile
import os
import shutil

from cwa_book_downloader.core.naming import (
    natural_sort_key,
    assign_part_numbers,
    parse_naming_template,
    build_library_path,
    sanitize_filename,
    sanitize_path_component,
    format_series_position,
)


class TestNaturalSortAndAssignment:

    def test_natural_sort_simple_numbers(self):
        files = ["Part 2.mp3", "Part 10.mp3", "Part 1.mp3"]
        assert sorted(files, key=natural_sort_key) == ["Part 1.mp3", "Part 2.mp3", "Part 10.mp3"]

    def test_natural_sort_cd_track_pattern(self):
        files = ["CD2_Track10.mp3", "CD1_Track2.mp3", "CD1_Track10.mp3", "CD2_Track1.mp3"]
        assert sorted(files, key=natural_sort_key) == [
            "CD1_Track2.mp3", "CD1_Track10.mp3", "CD2_Track1.mp3", "CD2_Track10.mp3"
        ]

    def test_assign_part_numbers_empty(self):
        assert assign_part_numbers([]) == []

    def test_assign_part_numbers_sorted(self):
        files = [Path("Part 3.mp3"), Path("Part 1.mp3"), Path("Part 2.mp3")]
        assert assign_part_numbers(files) == [
            (Path("Part 1.mp3"), "01"), (Path("Part 2.mp3"), "02"), (Path("Part 3.mp3"), "03")
        ]

    def test_assign_part_numbers_custom_padding(self):
        files = [Path("a.mp3"), Path("b.mp3")]
        assert assign_part_numbers(files, zero_pad_width=3) == [(Path("a.mp3"), "001"), (Path("b.mp3"), "002")]

    def test_no_false_positives_fahrenheit_451(self):
        files = [Path("Fahrenheit 451 - Part 2.mp3"), Path("Fahrenheit 451 - Part 1.mp3")]
        result = assign_part_numbers(files)
        assert result[0] == (Path("Fahrenheit 451 - Part 1.mp3"), "01")


class TestParseNamingTemplate:
    """Tests for template parsing with variable substitution."""

    def test_simple_substitution(self):
        """Test basic token replacement."""
        result = parse_naming_template(
            "{Author}/{Title}",
            {"Author": "Brandon Sanderson", "Title": "The Way of Kings"}
        )
        assert result == "Brandon Sanderson/The Way of Kings"

    def test_conditional_suffix(self):
        """Test conditional suffix inclusion."""
        template = "{Author}/{Series/}{Title}"

        # With series
        result = parse_naming_template(template, {
            "Author": "Brandon Sanderson",
            "Series": "Stormlight Archive",
            "Title": "The Way of Kings"
        })
        assert result == "Brandon Sanderson/Stormlight Archive/The Way of Kings"

        # Without series
        result = parse_naming_template(template, {
            "Author": "Brandon Sanderson",
            "Series": None,
            "Title": "The Way of Kings"
        })
        assert result == "Brandon Sanderson/The Way of Kings"

    def test_conditional_prefix(self):
        """Test conditional prefix inclusion."""
        template = "{Title}{ - Subtitle}"

        # With subtitle
        result = parse_naming_template(template, {
            "Title": "The Way of Kings",
            "Subtitle": "Journey Before Destination"
        })
        assert result == "The Way of Kings - Journey Before Destination"

        # Without subtitle
        result = parse_naming_template(template, {
            "Title": "The Way of Kings",
            "Subtitle": None
        })
        assert result == "The Way of Kings"

    def test_subtitle_token(self):
        """Test subtitle in various template positions."""
        metadata = {
            "Author": "Brandon Sanderson",
            "Title": "The Way of Kings",
            "Subtitle": "Book One of the Stormlight Archive"
        }

        # Subtitle after title
        result = parse_naming_template("{Author}/{Title} - {Subtitle}", metadata)
        assert result == "Brandon Sanderson/The Way of Kings - Book One of the Stormlight Archive"

        # Conditional subtitle
        result = parse_naming_template("{Author}/{Title}{ - Subtitle}", metadata)
        assert result == "Brandon Sanderson/The Way of Kings - Book One of the Stormlight Archive"

    def test_part_number_token(self):
        """Test PartNumber in templates."""
        metadata = {
            "Author": "Brandon Sanderson",
            "Title": "The Way of Kings",
            "PartNumber": "01"
        }

        # Literal " - Part " in template
        result = parse_naming_template("{Author}/{Title} - Part {PartNumber}", metadata)
        assert result == "Brandon Sanderson/The Way of Kings - Part 01"

        # Conditional prefix on PartNumber itself
        result = parse_naming_template("{Author}/{Title}{ - PartNumber}", metadata)
        assert result == "Brandon Sanderson/The Way of Kings - 01"

    def test_part_number_without_value(self):
        """Test PartNumber when not provided."""
        metadata = {
            "Author": "Brandon Sanderson",
            "Title": "The Way of Kings",
            "PartNumber": None
        }

        # Conditional prefix: " - " only appears if PartNumber has value
        result = parse_naming_template("{Author}/{Title}{ - PartNumber}", metadata)
        assert result == "Brandon Sanderson/The Way of Kings"

    def test_series_position(self):
        """Test series position formatting."""
        template = "{SeriesPosition - }{Title}"

        # Integer position
        result = parse_naming_template(template, {"SeriesPosition": 1, "Title": "Book"})
        assert result == "1 - Book"

        # Float position (novella)
        result = parse_naming_template(template, {"SeriesPosition": 1.5, "Title": "Book"})
        assert result == "1.5 - Book"

        # No position
        result = parse_naming_template(template, {"SeriesPosition": None, "Title": "Book"})
        assert result == "Book"

    def test_year_token(self):
        """Test year in templates."""
        result = parse_naming_template(
            "{Author}/{Title} ({Year})",
            {"Author": "Sanderson", "Title": "Book", "Year": 2010}
        )
        assert result == "Sanderson/Book (2010)"

    def test_case_insensitive_tokens(self):
        """Test that token matching is case-insensitive."""
        result = parse_naming_template(
            "{author}/{TITLE}",
            {"Author": "Sanderson", "Title": "Book"}
        )
        assert result == "Sanderson/Book"

    def test_special_characters_sanitized(self):
        """Test that special characters are sanitized."""
        result = parse_naming_template(
            "{Author}/{Title}",
            {"Author": "Author: Name", "Title": "Book: Subtitle?"}
        )
        assert ":" not in result
        assert "?" not in result

    def test_empty_template(self):
        """Test empty template."""
        assert parse_naming_template("", {"Title": "Book"}) == ""

    def test_empty_metadata(self):
        """Test with no metadata values."""
        result = parse_naming_template("{Author}/{Title}", {})
        assert result == ""

    def test_complex_template(self):
        """Test complex template with multiple conditional tokens."""
        template = "{Author}/{Series/}{SeriesPosition - }{Title}{ - Subtitle} ({Year})"

        # All fields present
        result = parse_naming_template(template, {
            "Author": "Brandon Sanderson",
            "Series": "Stormlight",
            "SeriesPosition": 1,
            "Title": "The Way of Kings",
            "Subtitle": "Epic Fantasy",
            "Year": 2010
        })
        assert result == "Brandon Sanderson/Stormlight/1 - The Way of Kings - Epic Fantasy (2010)"

        # Minimal fields
        result = parse_naming_template(template, {
            "Author": "Brandon Sanderson",
            "Title": "The Way of Kings"
        })
        assert result == "Brandon Sanderson/The Way of Kings"


class TestBuildLibraryPath:
    """Tests for complete library path building."""

    def test_basic_path(self):
        """Test basic path building."""
        path = build_library_path(
            "/books",
            "{Author}/{Title}",
            {"Author": "Sanderson", "Title": "Book"},
            extension="epub"
        )
        assert path == Path("/books/Sanderson/Book.epub")

    def test_path_with_subtitle(self):
        """Test path with subtitle."""
        path = build_library_path(
            "/books",
            "{Author}/{Title}{ - Subtitle}",
            {"Author": "Sanderson", "Title": "Book", "Subtitle": "A Novel"},
            extension="epub"
        )
        assert path == Path("/books/Sanderson/Book - A Novel.epub")

    def test_path_with_part_number(self):
        """Test path with part number for audiobooks."""
        path = build_library_path(
            "/audiobooks",
            "{Author}/{Title} - Part {PartNumber}",
            {"Author": "Sanderson", "Title": "Book", "PartNumber": "01"},
            extension="mp3"
        )
        assert path == Path("/audiobooks/Sanderson/Book - Part 01.mp3")

    def test_path_traversal_prevented(self):
        """Test that path traversal is prevented."""
        with pytest.raises(ValueError, match="traversal"):
            build_library_path(
                "/books",
                "{Author}/{Title}",
                {"Author": "../../../etc", "Title": "passwd"},
                extension="txt"
            )

    def test_fallback_to_title(self):
        """Test fallback when template produces empty result."""
        path = build_library_path(
            "/books",
            "{Series/}{Title}",
            {"Title": "Book"},
            extension="epub"
        )
        assert "Book" in str(path)

    def test_no_extension(self):
        """Test path without extension."""
        path = build_library_path(
            "/books",
            "{Author}/{Title}",
            {"Author": "Sanderson", "Title": "Book"},
            extension=None
        )
        assert path == Path("/books/Sanderson/Book")


class TestSanitizeFilename:
    """Tests for filename sanitization."""

    @pytest.mark.parametrize("input_name,expected", [
        ("normal_file", "normal_file"),
        ("file:with:colons", "file_with_colons"),
        ("file*with*stars", "file_with_stars"),
        ("file?with?questions", "file_with_questions"),
        ('file"with"quotes', "file_with_quotes"),
        ("file<with>angles", "file_with_angles"),
        ("file|with|pipes", "file_with_pipes"),
    ])
    def test_invalid_chars_replaced(self, input_name, expected):
        """Test that invalid characters are replaced."""
        assert sanitize_filename(input_name) == expected

    def test_leading_trailing_stripped(self):
        """Test that leading/trailing whitespace and dots are stripped."""
        assert sanitize_filename("  file  ") == "file"
        assert sanitize_filename("...file...") == "file"
        assert sanitize_filename(". file .") == "file"

    def test_multiple_underscores_collapsed(self):
        """Test that multiple underscores are collapsed."""
        assert sanitize_filename("file___name") == "file_name"

    def test_max_length_enforced(self):
        """Test that max length is enforced."""
        long_name = "a" * 300
        result = sanitize_filename(long_name, max_length=100)
        assert len(result) == 100

    def test_empty_string(self):
        """Test empty string handling."""
        assert sanitize_filename("") == ""
        assert sanitize_filename(None) == ""


class TestFormatSeriesPosition:
    """Tests for series position formatting."""

    def test_integer_position(self):
        """Test integer positions."""
        assert format_series_position(1) == "1"
        assert format_series_position(10) == "10"

    def test_float_integer_position(self):
        """Test float that's effectively an integer."""
        assert format_series_position(1.0) == "1"
        assert format_series_position(5.0) == "5"

    def test_float_position(self):
        """Test actual float positions (novellas)."""
        assert format_series_position(1.5) == "1.5"
        assert format_series_position(2.3) == "2.3"

    def test_none_position(self):
        """Test None handling."""
        assert format_series_position(None) == ""


class TestIntegration:
    """Integration tests for complete library path workflows."""

    def test_audiobook_multi_part_workflow(self):
        """Test complete workflow for multi-part audiobook."""
        base_metadata = {
            "Author": "Brandon Sanderson",
            "Title": "The Way of Kings",
            "Subtitle": "Stormlight Archive Book 1",
            "Year": 2010,
            "Series": "Stormlight Archive",
            "SeriesPosition": 1,
        }

        # Use literal " - Part " text for part numbers
        template = "{Author}/{Series/}{Title} - Part {PartNumber}"

        # Simulate processing multiple files (unsorted order)
        files = [
            Path("The Way of Kings - Part 03.mp3"),
            Path("The Way of Kings - Part 01.mp3"),
            Path("The Way of Kings - Part 02.mp3"),
        ]

        # Use assign_part_numbers to sort and number sequentially
        files_with_parts = assign_part_numbers(files)

        for file_path, part_num in files_with_parts:
            file_metadata = {**base_metadata, "PartNumber": part_num}
            path = build_library_path("/audiobooks", template, file_metadata, extension="mp3")

            assert "Brandon Sanderson" in str(path)
            assert "Stormlight Archive" in str(path)
            assert "The Way of Kings" in str(path)
            assert f"Part {part_num}" in str(path)

    def test_ebook_with_subtitle_workflow(self):
        """Test complete workflow for ebook with subtitle."""
        metadata = {
            "Author": "Frank Herbert",
            "Title": "Dune",
            "Subtitle": "Deluxe Edition",
            "Year": 1965,
        }

        template = "{Author}/{Title}{ - Subtitle} ({Year})"

        path = build_library_path("/books", template, metadata, extension="epub")
        assert path == Path("/books/Frank Herbert/Dune - Deluxe Edition (1965).epub")

    def test_ebook_without_subtitle_workflow(self):
        """Test workflow for ebook without subtitle."""
        metadata = {
            "Author": "Frank Herbert",
            "Title": "Dune",
            "Year": 1965,
        }

        template = "{Author}/{Title}{ - Subtitle} ({Year})"

        path = build_library_path("/books", template, metadata, extension="epub")
        assert path == Path("/books/Frank Herbert/Dune (1965).epub")


class TestFilesystemOperations:
    """Tests for actual filesystem operations - folder creation and file handling."""

    @pytest.fixture
    def temp_library(self):
        """Create a temporary library directory."""
        temp_dir = tempfile.mkdtemp(prefix="test_library_")
        yield Path(temp_dir)
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_creates_new_folder_structure(self, temp_library):
        """Test that new folder structure is created correctly."""
        metadata = {"Author": "Brandon Sanderson", "Title": "Mistborn"}
        template = "{Author}/{Title}"

        path = build_library_path(str(temp_library), template, metadata, extension="epub")

        # Create the directory structure
        path.parent.mkdir(parents=True, exist_ok=True)

        # Path is: temp_library/Brandon Sanderson/Mistborn.epub
        assert path.parent.exists()
        assert path.parent.name == "Brandon Sanderson"
        assert path.name == "Mistborn.epub"

    def test_adds_file_to_existing_folder(self, temp_library):
        """Test that files can be added to existing folders."""
        # Create existing author folder with a book
        author_dir = temp_library / "Brandon Sanderson"
        author_dir.mkdir(parents=True)
        existing_book = author_dir / "Elantris.epub"
        existing_book.write_text("existing book content")

        # Add a new book to the same author folder
        metadata = {"Author": "Brandon Sanderson", "Title": "Mistborn"}
        template = "{Author}/{Title}"

        path = build_library_path(str(temp_library), template, metadata, extension="epub")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("new book content")

        # Both books should exist
        assert existing_book.exists()
        assert path.exists()
        assert len(list(author_dir.iterdir())) == 2

    def test_adds_multiple_parts_to_same_folder(self, temp_library):
        """Test adding multiple audiobook parts to the same folder."""
        base_metadata = {
            "Author": "Brandon Sanderson",
            "Title": "The Way of Kings",
            "Series": "Stormlight Archive",
        }
        template = "{Author}/{Series}/{Title} - Part {PartNumber}"

        parts = ["01", "02", "03"]
        created_files = []

        for part_num in parts:
            file_metadata = {**base_metadata, "PartNumber": part_num}
            path = build_library_path(str(temp_library), template, file_metadata, extension="mp3")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"part {part_num} content")
            created_files.append(path)

        # All files should exist in the same folder
        for f in created_files:
            assert f.exists()

        # All files should be in the same directory
        parent_dir = created_files[0].parent
        assert all(f.parent == parent_dir for f in created_files)
        assert len(list(parent_dir.iterdir())) == 3

    def test_nested_series_folder_structure(self, temp_library):
        """Test creating deeply nested folder structures for series."""
        metadata = {
            "Author": "Brandon Sanderson",
            "Series": "Cosmere/Stormlight Archive",
            "Title": "The Way of Kings",
        }
        template = "{Author}/{Series/}{Title}"

        path = build_library_path(str(temp_library), template, metadata, extension="epub")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")

        assert path.exists()
        # Check the nested structure
        assert "Brandon Sanderson" in str(path)
        assert "Cosmere" in str(path)
        assert "Stormlight Archive" in str(path)

    def test_file_collision_detection(self, temp_library):
        """Test behavior when a file with the same name already exists."""
        metadata = {"Author": "Brandon Sanderson", "Title": "Mistborn"}
        template = "{Author}/{Title}"

        path = build_library_path(str(temp_library), template, metadata, extension="epub")
        path.parent.mkdir(parents=True, exist_ok=True)

        # Create first file
        path.write_text("original content")
        assert path.exists()

        # Build same path again - path building should still work
        path2 = build_library_path(str(temp_library), template, metadata, extension="epub")
        assert path == path2

        # Note: The actual collision handling (overwrite, rename, skip)
        # is done in the orchestrator, not in build_library_path

    def test_special_characters_in_folder_names(self, temp_library):
        """Test that special characters are sanitized in folder names."""
        metadata = {
            "Author": "Author: With Colons",
            "Title": "Book? With <Special> Characters*"
        }
        template = "{Author}/{Title}"

        path = build_library_path(str(temp_library), template, metadata, extension="epub")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")

        assert path.exists()
        # Verify no invalid characters in path
        path_str = str(path)
        for char in ':*?"<>|':
            assert char not in path_str

    def test_empty_series_skips_folder(self, temp_library):
        """Test that empty series doesn't create empty folder level."""
        metadata = {
            "Author": "Brandon Sanderson",
            "Title": "Elantris",
            "Series": None,
        }
        template = "{Author}/{Series/}{Title}"

        path = build_library_path(str(temp_library), template, metadata, extension="epub")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")

        # Should be Author/Title.epub, not Author//Title.epub or Author/None/Title.epub
        assert path.exists()
        assert path.parent.name == "Brandon Sanderson"
        assert path.name == "Elantris.epub"

    def test_unicode_in_folder_names(self, temp_library):
        """Test that unicode characters work in folder names."""
        metadata = {
            "Author": "Андрей Сапковский",  # Cyrillic
            "Title": "Ведьмак",  # Cyrillic
        }
        template = "{Author}/{Title}"

        path = build_library_path(str(temp_library), template, metadata, extension="epub")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")

        assert path.exists()
        assert "Андрей Сапковский" in str(path)

    def test_very_long_path_components(self, temp_library):
        """Test that very long names are truncated."""
        long_title = "A" * 300  # Longer than typical filesystem limits
        metadata = {
            "Author": "Author",
            "Title": long_title,
        }
        template = "{Author}/{Title}"

        path = build_library_path(str(temp_library), template, metadata, extension="epub")

        # Path should be buildable without error
        assert path is not None
        # Title component should be truncated
        assert len(path.stem) < 250
