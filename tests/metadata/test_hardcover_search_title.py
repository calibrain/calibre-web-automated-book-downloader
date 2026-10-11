import pytest

from shelfmark.metadata_providers.hardcover import _compute_search_title


class TestHardcoverComputeSearchTitle:
    def test_without_a_series_name_the_prefix_is_the_title(self):
        # "Mistborn: The Final Empire" from a search hit, which carries no series name:
        # the subtitle cannot be told from a description, so the prefix is kept.
        assert _compute_search_title("Mistborn: The Final Empire", "The Final Empire") == "Mistborn"

    def test_prefers_main_title_when_subtitle_is_descriptive(self):
        assert (
            _compute_search_title(
                "The Cuckoo's Egg: Tracking a Spy Through the Maze of Computer Espionage",
                "Tracking a Spy Through the Maze of Computer Espionage",
            )
            == "The Cuckoo's Egg"
        )

    @pytest.mark.parametrize(
        ("title", "subtitle", "series", "expected"),
        [
            # The subtitle is as long as the prefix, which the old length rule read as
            # "Series: Book Title" and searched as "The Making of a Scientist".
            (
                "An Appetite for Wonder: The Making of a Scientist",
                "The Making of a Scientist",
                "Richard Dawkins' Memoirs",
                "An Appetite for Wonder",
            ),
            (
                "Good and Valuable Consideration: Jack Reacher vs. Nick Heller",
                "Jack Reacher vs. Nick Heller",
                "Jack Reacher",
                "Good and Valuable Consideration",
            ),
            (
                "The Fall of Númenor: and Other Tales from the Second Age of Middle-earth",
                "and Other Tales from the Second Age of Middle-earth",
                "Tales of Middle Earth",
                "The Fall of Númenor",
            ),
            (
                "The Return of the Shadow: The History of The Lord of the Rings, Part One",
                "The History of The Lord of the Rings, Part One",
                "The History of Middle-Earth",
                "The Return of the Shadow",
            ),
        ],
    )
    def test_a_subtitle_that_is_not_the_series_title_leaves_the_prefix(
        self, title, subtitle, series, expected
    ):
        assert _compute_search_title(title, subtitle, series_name=series) == expected

    @pytest.mark.parametrize(
        ("title", "subtitle", "series"),
        [
            # Searched as "The Vampire" before, which found every vampire novel but this.
            ("Vittorio, the Vampire", "The Vampire", "New Tales of the Vampires"),
            # Searched as "Index" before.
            ("The History of Middle Earth Index", "Index", "The History of Middle-Earth"),
            (
                "Sauron Defeated",
                "The End Of The Third Age: The History of the Lord of the Rings, part four",
                "The History of Middle-Earth",
            ),
            ("Alex Cross's Trial", "a novel", "Alex Cross"),
            (
                "The Children of Hurin",
                "The Tale of the Children of Hrin. by J.R.R. Tolkien",
                "Tales of Middle Earth",
            ),
        ],
    )
    def test_a_subtitle_the_title_does_not_contain_is_never_searched_on_its_own(
        self, title, subtitle, series
    ):
        assert _compute_search_title(title, subtitle, series_name=series) is None

    def test_does_not_use_subtitle_when_it_looks_like_series_position(self):
        assert _compute_search_title("The Stormlight Archive: Book 1", "Book 1") is None
        assert _compute_search_title("Some Series: Volume II", "Volume II") is None
        assert (
            _compute_search_title(
                "The Stormlight Archive: Book 1", "Book 1", series_name="The Stormlight Archive"
            )
            is None
        )

    def test_strips_series_prefix_when_series_name_available(self):
        assert (
            _compute_search_title("Mistborn: The Final Empire", None, series_name="Mistborn")
            == "The Final Empire"
        )
        assert (
            _compute_search_title(
                "Mistborn: The Final Empire", "The Final Empire", series_name="Mistborn"
            )
            == "The Final Empire"
        )

    def test_a_title_that_is_not_series_first_keeps_its_prefix_even_in_a_series(self):
        # Hardcover names the series, but the title is not "Series: Title".
        assert (
            _compute_search_title("Star Wars: Thrawn", None, series_name="Star Wars: Thrawn")
            is None
        )

    def test_strips_parenthetical_suffix(self):
        assert _compute_search_title("The Martian (Unabridged)", None) == "The Martian"
        assert _compute_search_title("The Martian", None) is None

    @pytest.mark.parametrize(
        ("title", "subtitle", "series", "expected"),
        [
            # Hardcover's subtitle here is unrelated marketing text; the alternate title
            # is what kept AudiobookBay from returning the book at all.
            (
                "The Hobbit, or There and Back Again",
                "Heartache, Heritage, and Happiness",
                "Middle Earth",
                "The Hobbit",
            ),
            # The subtitle is the sequel's title; it must not become the search title.
            (
                "The Mummy, or Ramses the Damned",
                "The Passion of Cleopatra",
                "Ramses the Damned",
                "The Mummy",
            ),
            ("Moby-Dick; or, The Whale", None, None, "Moby-Dick"),
            ("Dead or Alive", None, None, None),
        ],
    )
    def test_strips_an_alternate_title(self, title, subtitle, series, expected):
        assert _compute_search_title(title, subtitle, series_name=series) == expected

    def test_returns_none_when_no_useful_simplification(self):
        assert _compute_search_title("Dune", None) is None
