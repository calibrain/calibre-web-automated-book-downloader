from __future__ import annotations

from typing import Any

import pytest

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import wikidata

LITERARY_WORK = "Q7725634"
FILM = "Q11424"


def _claim(value: str) -> dict[str, Any]:
    datavalue = {"id": value} if value.startswith("Q") else value
    return {"mainsnak": {"datavalue": {"value": datavalue}}}


def _entity(
    qid: str,
    labels: dict[str, str],
    *,
    instance_of: tuple[str, ...] = (LITERARY_WORK,),
    authors: tuple[str, ...] = ("Q1",),
    ol_ids: tuple[str, ...] = (),
    edition_of: tuple[str, ...] = (),
    sitelinks: int = 0,
) -> dict[str, Any]:
    claims = {
        "P31": [_claim(x) for x in instance_of],
        "P50": [_claim(x) for x in authors],
        "P648": [_claim(x) for x in ol_ids],
    }
    if edition_of:
        claims["P629"] = [_claim(x) for x in edition_of]
    return {
        "id": qid,
        "labels": {lang: {"language": lang, "value": v} for lang, v in labels.items()},
        "aliases": {},
        "claims": claims,
        "sitelinks": {f"site{i}": {} for i in range(sitelinks)},
    }


def _person(qid: str, labels: dict[str, str]) -> dict[str, Any]:
    return {"id": qid, "labels": {k: {"value": v} for k, v in labels.items()}, "aliases": {}}


class FakeApi:
    """Answers the MediaWiki calls the module makes; records what was asked."""

    def __init__(
        self,
        *,
        searches: dict[str, list[str]] | None = None,
        entities: dict[str, dict[str, Any]] | None = None,
        people: dict[str, list[str]] | None = None,
    ) -> None:
        self.searches = searches or {}
        self.entities = entities or {}
        self.people = people or {}
        self.calls: list[str] = []

    def __call__(self, params: dict[str, Any], deadline: wikidata.Deadline) -> dict[str, Any]:
        action = params.get("action")
        if action == "query":
            self.calls.append(params["srsearch"])
            hits = self.searches.get(params["srsearch"], [])
            return {"query": {"search": [{"title": q} for q in hits]}}
        if action == "wbgetentities":
            ids = params["ids"].split("|")
            self.calls.append(f"entities:{params['ids']}")
            return {"entities": {q: self.entities[q] for q in ids if q in self.entities}}
        if action == "wbsearchentities":
            self.calls.append(f"people:{params['search']}")
            return {"search": [{"id": q} for q in self.people.get(params["search"], [])]}
        raise AssertionError(f"unexpected call {params}")


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch: pytest.MonkeyPatch) -> None:
    get_metadata_cache().clear()
    monkeypatch.setattr(wikidata, "_breaker_until", 0.0)


SECRET_HISTORY = _entity(
    "Q21",
    {"en": "The Secret History", "uk": "Таємна історія", "pl": "Tajemna historia"},
    ol_ids=("OL1W",),
    sitelinks=40,
)


class TestLocalizedTitles:
    def test_work_linked_by_its_open_library_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        api = FakeApi(
            searches={"haswbstatement:P648=OL1W": ["Q21"]}, entities={"Q21": SECRET_HISTORY}
        )
        monkeypatch.setattr(wikidata, "_api", api)

        titles = wikidata.localized_titles(("OL1W",), "The Secret History", "Donna Tartt")

        assert titles == {
            "en": "The Secret History",
            "uk": "Таємна історія",
            "pl": "Tajemna historia",
        }
        assert api.calls == ["haswbstatement:P648=OL1W", "entities:Q21"]

    def test_wikidata_may_name_the_merged_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        api = FakeApi(
            searches={"haswbstatement:P648=OL9W": ["Q21"]}, entities={"Q21": SECRET_HISTORY}
        )
        monkeypatch.setattr(wikidata, "_api", api)

        titles = wikidata.localized_titles(("OL2W", "OL9W"), "The Secret History")

        assert titles["uk"] == "Таємна історія"

    def test_author_fallback_needs_the_same_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        little_friend = _entity("Q20", {"en": "The Little Friend", "uk": "Маленький друг"})
        api = FakeApi(
            searches={
                "haswbstatement:P648=OL5A": ["Q1"],
                "haswbstatement:P50=Q1 the secret history": ["Q20", "Q21"],
            },
            entities={"Q20": little_friend, "Q21": SECRET_HISTORY},
        )
        monkeypatch.setattr(wikidata, "_api", api)

        titles = wikidata.localized_titles(("OL3W",), "The Secret History", "", ("OL5A",))

        assert titles["uk"] == "Таємна історія"

    def test_author_found_by_name_when_open_library_has_no_link(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        api = FakeApi(
            searches={"haswbstatement:P50=Q1 the secret history": ["Q21"]},
            entities={"Q21": SECRET_HISTORY},
            people={"Donna Tartt": ["Q1"]},
        )
        monkeypatch.setattr(wikidata, "_api", api)

        titles = wikidata.localized_titles(("OL3W",), "The Secret History", "Donna Tartt")

        assert titles["pl"] == "Tajemna historia"

    def test_disambiguation_is_not_part_of_the_title(self, monkeypatch: pytest.MonkeyPatch) -> None:
        tango = _entity("Q30", {"uk": "Танґо смерті (роман)", "en": "Tango of Death"})
        api = FakeApi(searches={"haswbstatement:P648=OL4W": ["Q30"]}, entities={"Q30": tango})
        monkeypatch.setattr(wikidata, "_api", api)

        assert wikidata.localized_titles(("OL4W",))["uk"] == "Танґо смерті"

    @pytest.mark.parametrize(
        "entity",
        [
            _entity(
                "Q30",
                {"en": "The Talented Mr. Ripley", "uk": "Талановитий містер Ріплі"},
                instance_of=(FILM,),
            ),
            _entity("Q31", {"en": "The Talented Mr. Ripley"}, edition_of=("Q32",)),
            _entity("Q33", {"en": "The Talented Mr. Ripley"}, authors=()),
        ],
        ids=["film-adaptation", "edition", "no-author"],
    )
    def test_only_the_written_work_counts(
        self, monkeypatch: pytest.MonkeyPatch, entity: dict[str, Any]
    ) -> None:
        api = FakeApi(
            searches={"haswbstatement:P648=OL7W": [entity["id"]]},
            entities={entity["id"]: entity},
        )
        monkeypatch.setattr(wikidata, "_api", api)

        assert wikidata.localized_titles(("OL7W",), "The Talented Mr. Ripley") == {}

    def test_failures_are_not_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        api = FakeApi(
            searches={"haswbstatement:P648=OL1W": ["Q21"]}, entities={"Q21": SECRET_HISTORY}
        )

        def down(params: dict[str, Any], deadline: wikidata.Deadline) -> dict[str, Any]:
            raise wikidata.WikidataUnavailableError("timeout")

        monkeypatch.setattr(wikidata, "_api", down)
        with pytest.raises(wikidata.WikidataUnavailableError):
            wikidata.localized_titles(("OL1W",), "The Secret History")

        monkeypatch.setattr(wikidata, "_api", api)
        titles = wikidata.localized_titles(("OL1W",), "The Secret History")
        assert titles["uk"] == "Таємна історія"


class TestFindWorks:
    UKRAINE_HISTORY = _entity(
        "Q40", {"uk": "Таємна історія України"}, authors=("Q2",), ol_ids=("OL40W",)
    )
    ALCHEMIST = _entity("Q50", {"uk": "Алхімік (роман)", "en": "The Alchemist"}, authors=("Q3",))
    PEOPLE = {
        "Q1": _person("Q1", {"en": "Donna Tartt", "uk": "Донна Тартт"}),
        "Q2": _person("Q2", {"uk": "Іван Історик"}),
        "Q3": _person("Q3", {"en": "Paulo Coelho", "uk": "Пауло Коельйо"}),
    }

    def _api(self, searches: dict[str, list[str]]) -> FakeApi:
        return FakeApi(
            searches={f"{q} haswbstatement:P50": hits for q, hits in searches.items()},
            entities={
                "Q21": SECRET_HISTORY,
                "Q40": self.UKRAINE_HISTORY,
                "Q50": self.ALCHEMIST,
                **self.PEOPLE,
            },
        )

    def test_title_and_author_typed_together(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(wikidata, "_api", self._api({"таємна історія": ["Q40", "Q21"]}))

        works = wikidata.find_works("Тартт Таємна історія")

        assert [w.qid for w in works] == ["Q21"]
        assert works[0].ol_work_ids == ("OL1W",)
        assert works[0].author == "Donna Tartt"
        assert works[0].titles["uk"] == "Таємна історія"

    def test_extra_words_must_be_the_author(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(wikidata, "_api", self._api({"таємна історія україни": ["Q21", "Q40"]}))

        assert [w.qid for w in wikidata.find_works("Таємна історія України")] == ["Q40"]

    def test_label_part_and_surname_spelling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # «Алхімік (роман)» is typed as «Алхімік»; «Коельо» is how many spell «Коельйо».
        monkeypatch.setattr(wikidata, "_api", self._api({"алхімік": ["Q50"]}))

        works = wikidata.find_works("Коельо Алхімік")

        assert [w.qid for w in works] == ["Q50"]
        assert works[0].ol_work_ids == ()
        assert works[0].titles["uk"] == "Алхімік"

    def test_nothing_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(wikidata, "_api", FakeApi())

        assert wikidata.find_works("Неіснуюча книжка") == []


class TestWikidataWork:
    WORK = wikidata.WikidataWork(
        qid="Q12106092",
        ol_work_ids=(),
        titles={"uk": "Захар Беркут", "en": "Zakhar Berkut"},
        author="Ivan Franko",
        author_names=("Ivan Franko", "Іван Франко", "Franko, Ivan", "I. Franko"),
    )

    @pytest.mark.parametrize(
        ("names", "expected"),
        [
            (["Іван Франко"], True),
            (["Franko, Ivan"], True),
            (["I. Franko"], True),
            (["Frank Herbert"], False),
            (["Ivan Bunin"], False),
            (["Petro Kolomiets"], False),
            (["Martin Luther King Jr."], False),
        ],
    )
    def test_is_by_compares_surnames(self, names: list[str], expected: bool) -> None:
        assert self.WORK.is_by(names) is expected


class FakeResponse:
    def __init__(self, status: int, data: Any = None, retry_after: str | None = None) -> None:
        self.status_code = status
        self.data = data if data is not None else {}
        self.headers = {"Retry-After": retry_after} if retry_after else {}

    def json(self) -> Any:
        return self.data


class TestTransport:
    def test_retry_after_beyond_the_deadline_gives_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slept: list[float] = []
        monkeypatch.setattr(wikidata.time, "sleep", slept.append)
        monkeypatch.setattr(wikidata, "_send", lambda *_: FakeResponse(429, retry_after="60"))

        with pytest.raises(wikidata.WikidataUnavailableError):
            wikidata._api({"action": "query"}, wikidata.Deadline(10))
        assert slept == []

    def test_one_retry_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        answers = [FakeResponse(503, retry_after="1"), FakeResponse(200, {"ok": 1})]
        monkeypatch.setattr(wikidata.time, "sleep", lambda _s: None)
        monkeypatch.setattr(wikidata, "_send", lambda *_: answers.pop(0))

        assert wikidata._api({"action": "query"}, wikidata.Deadline(10)) == {"ok": 1}

    def test_deadline_reached(self) -> None:
        with pytest.raises(wikidata.WikidataUnavailableError):
            wikidata.Deadline(0).remaining()

    def test_paused_after_a_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(wikidata, "_send", lambda *_: FakeResponse(500))
        with pytest.raises(wikidata.WikidataUnavailableError):
            wikidata._api({"action": "query"}, wikidata.Deadline(10))

        def must_not_send(*_: Any) -> FakeResponse:
            raise AssertionError("must not send while paused")

        monkeypatch.setattr(wikidata, "_send", must_not_send)
        with pytest.raises(wikidata.WikidataUnavailableError, match="paused"):
            wikidata._api({"action": "query"}, wikidata.Deadline(10))


class TestTextHelpers:
    def test_normalize(self) -> None:
        assert wikidata.normalize("  Таємна   ІСТОРІЯ! ") == "таємна історія"
        assert wikidata.normalize("Café — Crème") == "cafe creme"

    def test_search_words_keep_diacritics_and_whole_words(self) -> None:
        # Wikidata search treats «України» and «Украiни» as different words.
        assert wikidata.search_words("Таємна історія України!") == "таємна історія україни"
        assert wikidata.search_words("alpha beta gamma", max_length=12) == "alpha beta"
        assert wikidata.search_words("a" * 130) == "a" * 120

    def test_ge_and_he_are_one_letter(self) -> None:
        assert wikidata.normalize("Танґо смерті") == wikidata.normalize("Танго смерті")

    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ("коельо", "коельио", True),
            ("tolkien", "tolkin", True),
            ("франко", "франко", True),
            ("кінг", "кіно", False),
            ("andrukhovych", "andrukhovich", True),
            ("orwell", "orwellian", False),
        ],
    )
    def test_close_spelling(self, a: str, b: str, expected: bool) -> None:
        assert wikidata.close_spelling(a, b) is expected

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Ivan Franko", "franko"),
            ("Franko, Ivan", "franko"),
            ("J. R. R. Tolkien", "tolkien"),
            ("Q.", ""),
            ("O. S. Zabuz͡hko", "zabuzhko"),
            ("Франко І. Я.", "франко"),
            ("Tolkien J.R.R.", "tolkien"),
            ("Kurt Vonnegut Jr.", "vonnegut"),
        ],
    )
    def test_surname(self, name: str, expected: str) -> None:
        assert wikidata.surname(name) == expected

    def test_label_forms(self) -> None:
        assert "Гобіт" in wikidata.label_forms("Гобіт, або Туди і звідти")
        assert "Алхімік" in wikidata.label_forms("Алхімік (роман)")
        assert "Людина розумна" in wikidata.label_forms("Sapiens: Людина розумна")
        # A part too short to be a title on its own is dropped; a whole label never is.
        assert wikidata.label_forms("Я, робот") == ["Я, робот", "робот"]
        assert wikidata.label_forms("活着") == ["活着"]
        # The disambiguator is not a part of the title either.
        assert wikidata.label_forms("Танґо смерті (роман)") == [
            "Танґо смерті (роман)",
            "Танґо смерті",
        ]

    def test_contains_phrase_is_whole_word(self) -> None:
        assert wikidata.contains_phrase("Ріплі", "Талановитий містер Ріплі")
        assert not wikidata.contains_phrase("Ріп", "Талановитий містер Ріплі")

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("Таємна історія", True),
            ("Η Μυστική Ιστορία", True),
            ("Tajemná historie", False),
            ("1984", False),
            ("Nº 1 Ladies' Detective Agency", False),
        ],
    )
    def test_non_latin_detection(self, text: str, expected: bool) -> None:
        assert wikidata.has_non_latin_letters(text) is expected
