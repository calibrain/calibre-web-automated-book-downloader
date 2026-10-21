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
                    "docs": [
                        {
                            "key": "/books/OL32771812M",
                            "title": "Der Weg der Könige",
                            "language": ["ger"],
                        }
                    ]
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

    def test_keeps_work_for_an_english_edition_with_another_title(self):
        """Open Library shows an English edition whenever one matches the query.

        Searching "Mistborn" picks the 2023 "Mistborn" reissue of "The Final
        Empire", which is the same book under a different English title, so the
        result keeps the work's title, ISBNs and first publication year.
        """
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL5738148W",
                "title": "The Final Empire",
                "author_name": ["Brandon Sanderson"],
                "isbn": ["9780765311788"],
                "cover_i": 14658160,
                "language": ["fre", "ger", "eng"],
                "first_publish_year": 2001,
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL62594701M",
                            "title": "Mistborn",
                            "language": ["eng"],
                            "isbn": ["9781250318541"],
                            "cover_i": 1,
                            "publish_year": [2023],
                        }
                    ]
                },
            },
            "Mistborn",
        )

        assert result is not None
        assert result.title == "The Final Empire"
        assert result.search_title is None
        assert result.publish_year == 2001
        assert result.isbn_13 == "9780765311788"
        assert result.cover_url == "https://covers.openlibrary.org/b/id/14658160-L.jpg"

    def test_keeps_work_for_a_subtitled_reprint(self):
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL893516W",
                "title": "Children of Dune",
                "author_name": ["Frank Herbert"],
                "first_publish_year": 1976,
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL1M",
                            "title": "Children of Dune (Dune Chronicles, Book 3)",
                            "language": ["eng"],
                            "publish_year": [1987],
                        }
                    ]
                },
            },
            "Children of Dune",
        )

        assert result is not None
        assert result.title == "Children of Dune"
        assert result.search_title is None
        assert result.publish_year == 1976

    def test_keeps_work_when_the_query_names_the_work(self):
        """A `lang` preference shows a translation even for the work's own title."""
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL893516W",
                "title": "Children of Dune",
                "author_name": ["Frank Herbert"],
                "first_publish_year": 1976,
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL2M",
                            "title": "Dune-Zyklus",
                            "language": ["ger"],
                            "publish_year": [1985],
                        }
                    ]
                },
            },
            "children of dune",
        )

        assert result is not None
        assert result.title == "Children of Dune"
        assert result.search_title is None
        assert result.publish_year == 1976

    def test_keeps_work_for_an_edition_of_unknown_language(self):
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL5738148W",
                "title": "The Final Empire",
                "author_name": ["Brandon Sanderson"],
                "first_publish_year": 2001,
                "editions": {
                    "docs": [{"key": "/books/OL3M", "title": "Mistborn", "publish_year": [2023]}]
                },
            },
            "Mistborn",
        )

        assert result is not None
        assert result.title == "The Final Empire"
        assert result.publish_year == 2001

    def test_ignores_edition_differing_only_in_case_and_accents(self):
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL1W",
                "title": "L’étranger",
                "author_name": ["Albert Camus"],
                "first_publish_year": 1942,
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL4M",
                            "title": "L'Etranger",
                            "language": ["fre"],
                            "publish_year": [1995],
                        }
                    ]
                },
            }
        )

        assert result is not None
        assert result.title == "L’étranger"
        assert result.publish_year == 1942

    def test_prefers_translation_matched_by_query(self):
        provider = OpenLibraryProvider()

        result = provider._parse_search_doc(
            {
                "key": "/works/OL5738148W",
                "title": "The Final Empire",
                "author_name": ["Brandon Sanderson"],
                "first_publish_year": 2001,
                "editions": {
                    "docs": [
                        {
                            "key": "/books/OL5M",
                            "title": "Kinder des Nebels",
                            "language": ["ger"],
                            "publish_year": [2018],
                        }
                    ]
                },
            },
            "Kinder des Nebels",
        )

        assert result is not None
        assert result.title == "Kinder des Nebels"
        assert result.search_title == "Kinder des Nebels"
        assert result.publish_year == 2018
