"""Tests for the shared language resolution used by every release source."""

import json
from pathlib import Path

import pytest

from shelfmark.core.languages import (
    LANGUAGE_DATA_PATH,
    known_language_codes,
    language_alias_map,
    language_name,
    normalize_language,
    supported_book_languages,
)

BASELINE = json.loads(
    (Path(__file__).parent / "fixtures" / "language_alias_baseline.json").read_text(
        encoding="utf-8"
    )
)


class TestBaselineEquivalence:
    """Every alias the per-source maps used to handle must still resolve the same.

    These maps lived in prowlarr/source.py and audiobookbay/source.py before they
    were consolidated here. The fixture is a frozen snapshot taken before the
    move, so a regression shows up as a concrete alias rather than a vague
    behaviour change.
    """

    @pytest.mark.parametrize(
        ("alias", "expected"), sorted(BASELINE["prowlarr_three_letter"].items())
    )
    def test_prowlarr_three_letter_aliases_unchanged(self, alias, expected):
        assert normalize_language(alias) == expected

    @pytest.mark.parametrize(("alias", "expected"), sorted(BASELINE["audiobookbay_names"].items()))
    def test_audiobookbay_names_unchanged(self, alias, expected):
        assert normalize_language(alias) == expected

    @pytest.mark.parametrize(
        ("alias", "expected"), sorted(BASELINE["direct_download_derived"].items())
    )
    def test_direct_download_derived_aliases_unchanged(self, alias, expected):
        # Direct Download built its aliases from the data file rather than a
        # literal map, so consolidating silently dropped the spellings it
        # derived -- notably the underscore form of a hyphenated code.
        assert normalize_language(alias) == expected


class TestNormalizeLanguage:
    def test_accepts_two_letter_codes(self):
        assert normalize_language("en") == "en"
        assert normalize_language("sv") == "sv"

    def test_accepts_three_letter_codes_in_both_iso_639_2_forms(self):
        # Bibliographic and terminological forms differ for these.
        assert normalize_language("ger") == normalize_language("deu") == "de"
        assert normalize_language("fre") == normalize_language("fra") == "fr"
        assert normalize_language("per") == normalize_language("fas") == "fa"
        assert normalize_language("ice") == normalize_language("isl") == "is"
        assert normalize_language("may") == normalize_language("msa") == "ms"

    def test_accepts_english_names(self):
        assert normalize_language("Swedish") == "sv"
        assert normalize_language("Scottish Gaelic") == "gd"

    def test_is_case_and_whitespace_insensitive(self):
        assert normalize_language("  ENG  ") == "en"
        assert normalize_language("sWeDiSh") == "sv"

    def test_returns_none_for_placeholders(self):
        for placeholder in ("unknown", "unk", "n/a", "na", "-", "--", "none", "null", "", "   "):
            assert normalize_language(placeholder) is None, placeholder

    def test_returns_none_for_unknown_values(self):
        assert normalize_language("xyz") is None
        assert normalize_language("Klingon") is None

    def test_returns_none_for_none(self):
        assert normalize_language(None) is None


class TestLanguageData:
    def test_every_alias_resolves_to_a_known_code(self):
        codes = known_language_codes()
        assert set(language_alias_map().values()) <= codes

    def test_codes_are_ascii(self):
        # "zh-Hant" once used a U+2011 non-breaking hyphen, which silently
        # defeats any comparison against the normal spelling.
        entries = json.loads(LANGUAGE_DATA_PATH.read_text(encoding="utf-8"))
        assert [e["code"] for e in entries if not e["code"].isascii()] == []

    def test_codes_are_unique(self):
        entries = json.loads(LANGUAGE_DATA_PATH.read_text(encoding="utf-8"))
        codes = [e["code"] for e in entries]
        assert len(codes) == len(set(codes))

    def test_language_name_round_trips(self):
        assert language_name("sv") == "Swedish"
        assert language_name("ml") == "Malayalam"
        assert language_name("zzz") is None
        assert language_name(None) is None


class TestMyAnonamouseCoverage:
    """MyAnonamouse offers 62 languages and Prowlarr passes its code through
    untransformed, so every one has to resolve here or the language is lost."""

    # Observed in live MyAnonamouse data via Prowlarr.
    OBSERVED = {"ENG": "en", "SWE": "sv", "MAL": "ml"}

    @pytest.mark.parametrize(("tag", "expected"), sorted(OBSERVED.items()))
    def test_observed_tags_resolve(self, tag, expected):
        assert normalize_language(tag) == expected

    def test_every_offered_language_resolves(self):
        # Names as MyAnonamouse's own searchLanguages selector lists them.
        offered = [
            "English",
            "Afrikaans",
            "Arabic",
            "Bengali",
            "Bosnian",
            "Bulgarian",
            "Burmese",
            "Catalan",
            "Chinese",
            "Croatian",
            "Czech",
            "Danish",
            "Dutch",
            "Estonian",
            "Farsi",
            "Finnish",
            "French",
            "German",
            "Greek",
            "Gujarati",
            "Hebrew",
            "Hindi",
            "Hungarian",
            "Icelandic",
            "Indonesian",
            "Irish",
            "Italian",
            "Japanese",
            "Javanese",
            "Kannada",
            "Korean",
            "Lithuanian",
            "Latin",
            "Latvian",
            "Malay",
            "Malayalam",
            "Manx",
            "Marathi",
            "Norwegian",
            "Polish",
            "Portuguese",
            "Punjabi",
            "Romanian",
            "Russian",
            "Scottish Gaelic",
            "Sanskrit",
            "Serbian",
            "Slovenian",
            "Spanish",
            "Swedish",
            "Tagalog",
            "Tamil",
            "Telugu",
            "Thai",
            "Turkish",
            "Ukrainian",
            "Urdu",
            "Vietnamese",
        ]
        unresolved = [name for name in offered if normalize_language(name) is None]
        assert unresolved == []


class TestSupportedBookLanguages:
    """What the settings dropdown and /api/config expose to clients."""

    def test_exposes_only_the_fields_clients_declare(self):
        # The frontend Language type is {code, language}. Aliases are an
        # implementation detail and would bloat every /api/config response.
        entries = supported_book_languages()
        assert entries
        assert all(set(e) == {"code", "language"} for e in entries)

    def test_covers_every_known_code(self):
        assert {e["code"] for e in supported_book_languages()} == set(known_language_codes())


class TestLegacyTraditionalChineseCode:
    """Traditional Chinese was stored with a U+2011 non-breaking hyphen.

    The canonical code is now the ASCII spelling, but anything persisted
    earlier still carries U+2011, so both have to resolve to the same language
    or those users lose their selection (reported on PR #1142).
    """

    LEGACY = "zh\u2011Hant"
    CANONICAL = "zh-Hant"

    def test_the_legacy_spelling_still_resolves(self):
        assert normalize_language(self.LEGACY) == self.CANONICAL

    def test_both_spellings_are_the_same_language(self):
        assert normalize_language(self.LEGACY) == normalize_language(self.CANONICAL)

    def test_the_legacy_spelling_really_does_use_a_different_character(self):
        # Guards the test itself: if this ever became a plain hyphen the two
        # cases above would pass for the wrong reason.
        assert self.LEGACY != self.CANONICAL
        assert not self.LEGACY.isascii()

    @pytest.mark.parametrize("dash", ["-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014"])
    def test_any_dash_variant_resolves(self, dash):
        assert normalize_language(f"zh{dash}Hant") == self.CANONICAL


class TestCodesDoNotShadowEachOther:
    """A code must never resolve to a different language than itself.

    'zh' and 'zh-Hant' are distinct entries; registering the base of a
    hyphenated code as an alias made 'zh' resolve correctly only because
    Chinese happens to appear first in the data file.
    """

    def test_chinese_does_not_resolve_to_traditional_chinese(self):
        assert normalize_language("zh") == "zh"
        assert normalize_language("zh-Hant") == "zh-Hant"

    def test_every_code_resolves_to_itself(self):
        for code in known_language_codes():
            assert normalize_language(code) == code, f"{code} resolved elsewhere"


class TestPortugueseVariants:
    """Brazilian and European Portuguese are distinct entries alongside the
    unqualified 'pt', mirroring the 'zh' / 'zh-Hant' split (issue #1320)."""

    def test_unqualified_portuguese_is_unchanged(self):
        assert normalize_language("pt") == "pt"
        assert normalize_language("por") == "pt"
        assert normalize_language("Portuguese") == "pt"

    @pytest.mark.parametrize(
        "spelling",
        ["pt-BR", "pt-br", "pt_BR", "pt‑BR", "Brazilian Portuguese", "portugues brasileiro"],
    )
    def test_brazilian_portuguese_resolves(self, spelling):
        assert normalize_language(spelling) == "pt-BR"

    @pytest.mark.parametrize(
        "spelling", ["pt-PT", "pt-pt", "pt_PT", "European Portuguese", "portugues europeu"]
    )
    def test_european_portuguese_resolves(self, spelling):
        assert normalize_language(spelling) == "pt-PT"

    def test_accented_portuguese_names_resolve(self):
        assert normalize_language("Português (Brasil)") == "pt-BR"
        assert normalize_language("Português (Portugal)") == "pt-PT"

    def test_variants_do_not_shadow_the_base_code(self):
        # 'pt' still resolves to itself even though 'pt-BR' / 'pt-PT' share its
        # prefix and appear later in the data file.
        assert normalize_language("pt") == "pt"
        assert normalize_language("pt-BR") == "pt-BR"
        assert normalize_language("pt-PT") == "pt-PT"


class TestSubtagSeparators:
    """The U+2011 in the old Traditional Chinese code renders close enough to
    both a hyphen and an underscore that either is a plausible thing to type."""

    @pytest.mark.parametrize("separator", ["-", "_", "‐", "‑", "–", "—"])
    def test_any_separator_spelling_resolves(self, separator):
        assert normalize_language(f"zh{separator}Hant") == "zh-Hant"

    def test_separators_do_not_merge_unrelated_codes(self):
        # Folding a separator must not make one language answer to another.
        assert normalize_language("zh") == "zh"
        assert normalize_language("en_GB") is None
