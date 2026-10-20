from shelfmark.metadata_providers.openlibrary import OpenLibraryProvider


class TestOpenLibraryParseSearchDoc:
    def test_prefers_matched_edition_over_work_title(self):
        """Searching a translated title shouldn't surface the work's original title.

        Open Library groups every edition (including translations) under one work
        record, and reports the work's own `title` - usually the original-language
        edition - regardless of which translation's text actually matched the query.
        Querying "Der Weg der Könige" matches the German edition of Brandon
        Sanderson's "The Way of Kings" by title, but the work-level title still says
        "The Way of Kings". The nested `editions` block carries the edition whose own
        fields satisfied the query, so that edition's title/isbn/cover/language
        should win - otherwise both the displayed result and the downstream release
        search (which uses `search_title`, see search_plan._pick_search_title) stay
        in English even though the user searched for the German edition by name.
        """
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL15358691W",
                "title": "The Way of Kings",
                "author_name": ["Brandon Sanderson"],
                "isbn": ["9780765326355", "0765326353"],
                "cover_i": 14658316,
                "language": ["eng"],
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL32771812M",
                            "title": "Der Weg der Könige",
                            "cover_i": 14658425,
                            "language": ["ger"],
                            "isbn": ["9783453317109", "3453317106"],
                            "publish_year": [2015],
                        }
                    ]
                },
                "first_publish_year": 2010,
            }
        )

        assert result is not None
        assert result.title == "Der Weg der Könige"
        assert result.publish_year == 2015
        assert result.search_title == "Der Weg der Könige"
        assert result.language == "ger"
        assert result.isbn_13 == "9783453317109"
        assert result.isbn_10 == "3453317106"
        assert result.cover_url == "https://covers.openlibrary.org/b/id/14658425-L.jpg"

    def test_keeps_work_year_when_matched_edition_has_none(self):
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL15358691W",
                "title": "The Way of Kings",
                "author_name": ["Brandon Sanderson"],
                "first_publish_year": 2010,
                "editions": {
                    "docs": [{"key": "/books/OL32771812M", "title": "Der Weg der Könige"}]
                },
            }
        )

        assert result is not None
        assert result.title == "Der Weg der Könige"
        assert result.publish_year == 2010

    def test_falls_back_to_work_title_without_a_matched_edition(self):
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL15358691W",
                "title": "The Way of Kings",
                "author_name": ["Brandon Sanderson"],
                "isbn": ["9780765326355"],
                "cover_i": 14658316,
                "language": ["eng"],
            }
        )

        assert result is not None
        assert result.title == "The Way of Kings"
        assert result.search_title is None
        assert result.language == "eng"

    def test_ignores_matched_edition_identical_to_work_title(self):
        """An edition that happens to share the work's title adds nothing new."""
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL15358691W",
                "title": "The Way of Kings",
                "author_name": ["Brandon Sanderson"],
                "isbn": ["9780765326355"],
                "cover_i": 14658316,
                "language": ["eng"],
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL1M",
                            "title": "The Way of Kings",
                            "language": ["eng"],
                        }
                    ]
                },
            }
        )

        assert result is not None
        assert result.search_title is None
        assert result.isbn_13 == "9780765326355"
