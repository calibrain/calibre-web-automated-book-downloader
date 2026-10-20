"""Unit tests for the slskd release source."""

from unittest.mock import MagicMock

import pytest

from shelfmark.core.search_plan import ReleaseSearchPlan, ReleaseSearchVariant
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import ReleaseProtocol, get_source
from shelfmark.release_sources.slskd.source import (
    SlskdSource,
    _plan_queries,
    build_releases,
    file_extension,
    get_supported_formats,
    iter_peer_files,
    make_source_id,
    rank_key,
    remote_directory_name,
    split_remote_path,
)

# ── fixtures / helpers ─────────────────────────────────────────────────────────


def _make_book(**kwargs) -> BookMetadata:
    defaults = {
        "provider": "hardcover",
        "provider_id": "1",
        "title": "Dune",
        "authors": ["Frank Herbert"],
    }
    defaults.update(kwargs)
    return BookMetadata(**defaults)


def _make_plan(
    *,
    variants: list[tuple[str, str]] | None = None,
    manual_query: str | None = None,
) -> ReleaseSearchPlan:
    variants = variants if variants is not None else [("Dune", "Frank Herbert")]
    title_variants = [ReleaseSearchVariant(title=t, author=a) for t, a in variants]
    return ReleaseSearchPlan(
        languages=["en"],
        isbn_candidates=[],
        author="Frank Herbert",
        title_variants=title_variants,
        grouped_title_variants=title_variants,
        manual_query=manual_query,
    )


def _file(filename: str, size: int = 1000, **extra) -> dict:
    return {"filename": filename, "size": size, "isLocked": False, **extra}


def _response(username: str = "peer", files: list[dict] | None = None, **extra) -> dict:
    base = {
        "username": username,
        "hasFreeUploadSlot": True,
        "queueLength": 0,
        "uploadSpeed": 500000,
        "fileCount": len(files or []),
        "files": files or [],
    }
    base.update(extra)
    return base


EBOOK_FORMATS = {"epub", "mobi", "azw3", "pdf"}
AUDIO_FORMATS = {"mp3", "m4b", "flac", "zip"}


# ── path helpers ───────────────────────────────────────────────────────────────


class TestPathHelpers:
    def test_split_backslash_path(self):
        assert split_remote_path("@@a\\Books\\Author\\Dune.epub") == (
            "@@a\\Books\\Author",
            "Dune.epub",
        )

    def test_split_forward_slash_path(self):
        assert split_remote_path("share/Books/Dune.epub") == ("share/Books", "Dune.epub")

    def test_split_bare_filename(self):
        assert split_remote_path("Dune.epub") == ("", "Dune.epub")

    def test_split_mixed_separators_uses_last(self):
        assert split_remote_path("a\\b/c\\d.epub") == ("a\\b/c", "d.epub")

    def test_remote_directory_name(self):
        assert remote_directory_name("@@a\\Books\\Author") == "Author"
        assert remote_directory_name("Author") == "Author"
        assert remote_directory_name("") == ""

    @pytest.mark.parametrize(
        ("name", "ext"),
        [
            ("Dune.EPUB", "epub"),
            ("Dune.part1.mp3", "mp3"),
            ("no-extension", ""),
            ("trailing.", ""),
        ],
    )
    def test_file_extension(self, name, ext):
        assert file_extension(name) == ext

    def test_source_id_is_stable_and_namespaced(self):
        a = make_source_id("peer", "x\\y.epub")
        assert a == make_source_id("peer", "x\\y.epub")
        assert a.startswith("slskd:")
        assert a != make_source_id("other", "x\\y.epub")


# ── iter_peer_files ────────────────────────────────────────────────────────────


class TestIterPeerFiles:
    def test_extracts_files(self):
        response = _response(files=[_file("@@a\\Books\\Dune.epub", 2048)])
        (pf,) = iter_peer_files(response)
        assert pf.username == "peer"
        assert pf.filename == "@@a\\Books\\Dune.epub"
        assert pf.directory == "@@a\\Books"
        assert pf.basename == "Dune.epub"
        assert pf.extension == "epub"
        assert pf.size == 2048

    def test_skips_locked_files(self):
        response = _response(files=[_file("a\\locked.epub", isLocked=True), _file("a\\ok.epub")])
        assert [pf.basename for pf in iter_peer_files(response)] == ["ok.epub"]

    def test_skips_garbage(self):
        response = _response(files=[None, {"filename": ""}, {"filename": "dir\\"}, "junk"])
        assert iter_peer_files(response) == []

    def test_missing_username_yields_nothing(self):
        assert iter_peer_files({"files": [_file("a.epub")]}) == []

    def test_size_coercion(self):
        response = _response(files=[_file("a.epub", size="12"), _file("b.epub", size=None)])
        assert [pf.size for pf in iter_peer_files(response)] == [12, 0]


# ── build_releases ─────────────────────────────────────────────────────────────


class TestBuildReleases:
    def test_ebook_one_release_per_file(self):
        response = _response(
            files=[
                _file("@@a\\Books\\Dune.epub", 1000),
                _file("@@a\\Books\\Dune.mobi", 2000),
                _file("@@a\\Books\\cover.jpg", 50),
            ]
        )
        releases = build_releases([response], content_type="ebook", formats=EBOOK_FORMATS)
        assert [r.title for r in releases] == ["Dune", "Dune"]
        assert [r.format for r in releases] == ["epub", "mobi"]
        assert releases[0].source == "slskd"
        assert releases[0].protocol == ReleaseProtocol.SOULSEEK
        assert releases[0].indexer == "peer"
        assert releases[0].content_type == "ebook"
        assert releases[0].size_bytes == 1000
        assert releases[0].size == "1000 B"
        assert releases[0].extra["username"] == "peer"
        assert releases[0].extra["files"] == [{"filename": "@@a\\Books\\Dune.epub", "size": 1000}]
        assert releases[0].extra["directory"] == "@@a\\Books"
        assert releases[0].extra["directory_name"] == "Books"
        assert releases[0].extra["availability"] == "Free slot"
        assert releases[0].extra["formats"] == ["epub"]

    def test_format_filter_respected(self):
        response = _response(files=[_file("a\\x.pdf"), _file("a\\x.epub")])
        releases = build_releases([response], content_type="ebook", formats={"epub"})
        assert [r.format for r in releases] == ["epub"]

    def test_bare_files_with_no_folder_are_not_grouped_into_one_audiobook(self):
        response = _response(
            files=[_file("01 Intro.mp3", 100), _file("Other Book.mp3", 200), _file("c.mp3", 300)]
        )

        releases = build_releases([response], content_type="audiobook", formats=AUDIO_FORMATS)

        assert sorted(r.extra["file_count"] for r in releases) == [1, 1, 1]

    def test_audiobook_groups_directory(self):
        response = _response(
            files=[
                _file("@@a\\Audio\\Dune\\01.mp3", 100),
                _file("@@a\\Audio\\Dune\\02.mp3", 200),
                _file("@@a\\Audio\\Dune\\03.mp3", 300),
                _file("@@a\\Audio\\Dune\\cover.jpg", 5),
                _file("@@a\\Audio\\Other\\single.m4b", 999),
            ]
        )
        releases = build_releases([response], content_type="audiobook", formats=AUDIO_FORMATS)
        assert len(releases) == 2
        grouped = next(r for r in releases if r.extra["file_count"] == 3)
        single = next(r for r in releases if r.extra["file_count"] == 1)
        assert grouped.title == "Dune (3 files)"
        assert grouped.format == "mp3"
        assert grouped.size_bytes == 600
        assert grouped.content_type == "audiobook"
        assert [f["filename"] for f in grouped.extra["files"]] == [
            "@@a\\Audio\\Dune\\01.mp3",
            "@@a\\Audio\\Dune\\02.mp3",
            "@@a\\Audio\\Dune\\03.mp3",
        ]
        assert single.title == "single"
        assert single.format == "m4b"

    def test_audiobook_archives_stay_separate(self):
        response = _response(
            files=[
                _file("@@a\\Dune\\Dune.zip", 5000),
                _file("@@a\\Dune\\01.mp3", 100),
                _file("@@a\\Dune\\02.mp3", 100),
            ]
        )
        releases = build_releases([response], content_type="audiobook", formats=AUDIO_FORMATS)
        formats = sorted((r.format, r.extra["file_count"]) for r in releases)
        assert formats == [("mp3", 2), ("zip", 1)]

    def test_dominant_format_wins_for_group(self):
        response = _response(
            files=[
                _file("@@a\\Dune\\01.flac"),
                _file("@@a\\Dune\\02.mp3"),
                _file("@@a\\Dune\\03.mp3"),
            ]
        )
        (release,) = build_releases([response], content_type="audiobook", formats=AUDIO_FORMATS)
        assert release.format == "mp3"
        assert release.extra["formats"] == ["mp3", "flac"]

    def test_dedupes_identical_files_across_responses(self):
        response = _response(files=[_file("a\\x.epub")])
        releases = build_releases([response, response], content_type="ebook", formats=EBOOK_FORMATS)
        assert len(releases) == 1

    def test_same_file_from_two_peers_is_two_releases(self):
        a = _response(username="alice", files=[_file("a\\x.epub")])
        b = _response(username="bob", files=[_file("a\\x.epub")])
        releases = build_releases([a, b], content_type="ebook", formats=EBOOK_FORMATS)
        assert {r.indexer for r in releases} == {"alice", "bob"}
        assert len({r.source_id for r in releases}) == 2

    def test_availability_labels(self):
        busy = _response(hasFreeUploadSlot=False, queueLength=0, files=[_file("a\\x.epub")])
        queued = _response(hasFreeUploadSlot=False, queueLength=4, files=[_file("a\\x.epub")])
        (busy_release,) = build_releases([busy], content_type="ebook", formats=EBOOK_FORMATS)
        (queued_release,) = build_releases([queued], content_type="ebook", formats=EBOOK_FORMATS)
        assert busy_release.extra["availability"] == "Busy"
        assert queued_release.extra["availability"] == "Queue 4"


# ── ranking ────────────────────────────────────────────────────────────────────


class TestRanking:
    def test_free_slot_then_queue_then_speed(self):
        responses = [
            _response(username="slow-free", uploadSpeed=10, files=[_file("a\\x.epub")]),
            _response(
                username="fast-queued",
                hasFreeUploadSlot=False,
                queueLength=2,
                uploadSpeed=10**7,
                files=[_file("a\\x.epub")],
            ),
            _response(username="fast-free", uploadSpeed=10**6, files=[_file("a\\x.epub")]),
            _response(
                username="short-queue",
                hasFreeUploadSlot=False,
                queueLength=1,
                uploadSpeed=1,
                files=[_file("a\\x.epub")],
            ),
        ]
        releases = build_releases(responses, content_type="ebook", formats=EBOOK_FORMATS)
        releases.sort(key=rank_key)
        assert [r.indexer for r in releases] == [
            "fast-free",
            "slow-free",
            "short-queue",
            "fast-queued",
        ]


# ── supported formats ──────────────────────────────────────────────────────────


class TestSupportedFormats:
    def test_reads_ebook_formats(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(
            mod.config,
            "get",
            lambda k, d=None: ["EPUB", ".mobi"] if k == "SUPPORTED_FORMATS" else d,
        )
        assert get_supported_formats("ebook") == {"epub", "mobi"}

    def test_reads_audiobook_formats(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(
            mod.config,
            "get",
            lambda k, d=None: ["m4b"] if k == "SUPPORTED_AUDIOBOOK_FORMATS" else d,
        )
        assert get_supported_formats("audiobook") == {"m4b"}

    def test_falls_back_to_defaults(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", lambda k, d=None: d)
        assert "epub" in get_supported_formats("ebook")
        assert "mp3" in get_supported_formats("audiobook")
        assert "zip" in get_supported_formats("audiobook")

    def test_accepts_comma_string(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", lambda k, d=None: "epub, pdf")
        assert get_supported_formats("ebook") == {"epub", "pdf"}


# ── query planning ─────────────────────────────────────────────────────────────


class TestPlanQueries:
    def test_title_author_then_bare_title(self):
        assert _plan_queries(_make_plan()) == ["Dune Frank Herbert", "Dune"]

    def test_manual_query_first(self):
        plan = _make_plan(manual_query="dune messiah herbert")
        assert _plan_queries(plan)[0] == "dune messiah herbert"

    def test_dedupes_and_caps(self):
        plan = _make_plan(
            variants=[("Dune", "Frank Herbert"), ("dune", "frank herbert"), ("Dune Messiah", "X")]
        )
        queries = _plan_queries(plan)
        assert queries == ["Dune Frank Herbert", "Dune", "Dune Messiah X"]

    def test_empty_plan(self):
        assert _plan_queries(_make_plan(variants=[])) == []


# ── SlskdSource ────────────────────────────────────────────────────────────────


class TestSlskdSource:
    def _config(self, **overrides):
        values = {"SLSKD_ENABLED": True, "SLSKD_URL": "http://slskd:5030", "SLSKD_API_KEY": "k"}
        values.update(overrides)
        return lambda k, default=None: values.get(k, default)

    def test_registered(self):
        assert isinstance(get_source("slskd"), SlskdSource)

    def test_available_when_enabled_and_url_set(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", self._config())
        assert SlskdSource().is_available() is True

    def test_unavailable_when_disabled(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", self._config(SLSKD_ENABLED=False))
        assert SlskdSource().is_available() is False

    def test_unavailable_without_url(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", self._config(SLSKD_URL=""))
        assert SlskdSource().is_available() is False

    def test_column_config_serializes(self):
        from shelfmark.release_sources import serialize_column_config

        data = serialize_column_config(SlskdSource().get_column_config())
        assert [c["key"] for c in data["columns"]] == [
            "extra.username",
            "extra.availability",
            "format",
            "size",
        ]
        assert data["supported_filters"] == ["format"]
        assert data["leading_cell"]["type"] == "none"
        assert {o["sort_key"] for o in data["extra_sort_options"]} == {
            "extra.upload_speed",
            "extra.queue_length",
        }

    def test_search_without_client_returns_empty(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", self._config(SLSKD_URL=""))
        assert SlskdSource().search(_make_book(), _make_plan()) == []

    def test_search_stops_after_first_query_with_results(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", self._config())
        client = MagicMock()
        client.search.return_value = [_response(files=[_file("a\\Dune.epub")])]
        monkeypatch.setattr(SlskdSource, "_get_client", lambda self: client)

        releases = SlskdSource().search(_make_book(), _make_plan())

        assert len(releases) == 1
        client.search.assert_called_once()
        assert client.search.call_args.args == ("Dune Frank Herbert",)
        kwargs = client.search.call_args.kwargs
        assert kwargs["wait_seconds"] == 15
        assert kwargs["response_limit"] == 100

    def test_search_falls_back_to_bare_title(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", self._config())
        client = MagicMock()
        client.search.side_effect = [[], [_response(files=[_file("a\\Dune.epub")])]]
        monkeypatch.setattr(SlskdSource, "_get_client", lambda self: client)

        releases = SlskdSource().search(_make_book(), _make_plan())

        assert len(releases) == 1
        assert [c.args[0] for c in client.search.call_args_list] == ["Dune Frank Herbert", "Dune"]

    def test_expand_search_runs_every_query(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", self._config())
        client = MagicMock()
        client.search.side_effect = [
            [_response(files=[_file("a\\Dune.epub")])],
            [_response(username="other", files=[_file("b\\Dune.epub")])],
        ]
        monkeypatch.setattr(SlskdSource, "_get_client", lambda self: client)

        releases = SlskdSource().search(_make_book(), _make_plan(), expand_search=True)

        assert len(releases) == 2
        assert client.search.call_count == 2

    def test_search_survives_client_errors(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", self._config())
        client = MagicMock()
        client.search.side_effect = [RuntimeError("boom"), [_response(files=[_file("a\\x.epub")])]]
        monkeypatch.setattr(SlskdSource, "_get_client", lambda self: client)

        assert len(SlskdSource().search(_make_book(), _make_plan())) == 1

    def test_search_uses_configured_limits(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(
            mod.config, "get", self._config(SLSKD_SEARCH_TIMEOUT="30", SLSKD_RESPONSE_LIMIT=5000)
        )
        client = MagicMock()
        client.search.return_value = []
        monkeypatch.setattr(SlskdSource, "_get_client", lambda self: client)

        SlskdSource().search(_make_book(), _make_plan())

        kwargs = client.search.call_args_list[0].kwargs
        assert kwargs["wait_seconds"] == 30
        assert kwargs["response_limit"] == 1000  # clamped

    def test_search_audiobook_uses_audio_formats(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", self._config())
        client = MagicMock()
        client.search.return_value = [
            _response(
                files=[_file("a\\Dune\\01.mp3"), _file("a\\Dune\\02.mp3"), _file("a\\Dune.epub")]
            )
        ]
        monkeypatch.setattr(SlskdSource, "_get_client", lambda self: client)

        releases = SlskdSource().search(_make_book(), _make_plan(), content_type="audiobook")

        assert len(releases) == 1
        assert releases[0].extra["file_count"] == 2


# ── fork additions: containers, merged variants, strict-match signals ───────────


class TestWholeBookContainers:
    def test_an_m4b_next_to_loose_chapters_is_its_own_release(self):
        response = _response(
            files=[
                _file("@@a\\Dune\\Dune.m4b", 900),
                _file("@@a\\Dune\\01.mp3", 100),
                _file("@@a\\Dune\\02.mp3", 100),
                _file("@@a\\Dune\\03.mp3", 100),
            ]
        )

        releases = build_releases([response], content_type="audiobook", formats=AUDIO_FORMATS)

        assert sorted((r.format, r.extra["file_count"]) for r in releases) == [
            ("m4b", 1),
            ("mp3", 3),
        ]

    def test_two_m4b_files_in_one_folder_are_two_releases(self):
        response = _response(files=[_file("@@a\\Dune\\A.m4b", 5), _file("@@a\\Dune\\B.m4b", 6)])

        releases = build_releases([response], content_type="audiobook", formats=AUDIO_FORMATS)

        assert sorted(r.extra["file_count"] for r in releases) == [1, 1]

    def test_mixed_chapter_formats_stay_one_release(self):
        response = _response(files=[_file("@@a\\Dune\\01.flac"), _file("@@a\\Dune\\02.mp3")])

        releases = build_releases([response], content_type="audiobook", formats=AUDIO_FORMATS)

        assert len(releases) == 1


class TestMergedQueryVariants:
    def test_chapters_found_by_different_queries_are_one_release(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", TestSlskdSource()._config())
        client = MagicMock()
        client.search.side_effect = [
            [_response(files=[_file("@@a\\Dune\\01.mp3"), _file("@@a\\Dune\\02.mp3")])],
            [_response(files=[_file("@@a\\Dune\\02.mp3"), _file("@@a\\Dune\\03.mp3")])],
        ]
        monkeypatch.setattr(SlskdSource, "_get_client", lambda self: client)

        releases = SlskdSource().search(
            _make_book(), _make_plan(), expand_search=True, content_type="audiobook"
        )

        assert len(releases) == 1
        assert [f["filename"] for f in releases[0].extra["files"]] == [
            "@@a\\Dune\\01.mp3",
            "@@a\\Dune\\02.mp3",
            "@@a\\Dune\\03.mp3",
        ]

    def test_merging_keeps_each_peers_own_slot_details(self):
        from shelfmark.release_sources.slskd.source import merge_responses

        first = _response(username="a", files=[_file("x\\1.mp3")], hasFreeUploadSlot=True)
        second = _response(
            username="a", files=[_file("x\\2.mp3")], hasFreeUploadSlot=False, queueLength=9
        )
        other = _response(username="b", files=[_file("y\\1.mp3")])

        merged = merge_responses([[first], [second, other]])

        assert sorted(r["username"] for r in merged) == ["a", "b"]
        a = next(r for r in merged if r["username"] == "a")
        assert sorted(f["filename"] for f in a["files"]) == ["x\\1.mp3", "x\\2.mp3"]
        assert a["hasFreeUploadSlot"] is True  # the first answer's details win


class TestStrictMatchSignals:
    """Auto-download looks for the author in the title, the peer name and extra['author'], and
    for bundle names in extra['title_raw']. A Soulseek result has the author only in its folder."""

    def test_the_folder_path_is_offered_as_the_author_text(self):
        response = _response(files=[_file("@@a\\Books\\Frank Herbert\\Dune\\Dune.epub")])

        (release,) = build_releases([response], content_type="ebook", formats=EBOOK_FORMATS)

        assert "Frank Herbert" in release.extra["author"]
        assert "Dune" in release.extra["author"]

    def test_a_folder_release_also_offers_its_path_as_the_author_text(self):
        response = _response(
            files=[
                _file("@@a\\Audio\\Frank Herbert\\Dune\\01.mp3"),
                _file("@@a\\Audio\\Frank Herbert\\Dune\\02.mp3"),
            ]
        )

        (release,) = build_releases([response], content_type="audiobook", formats=AUDIO_FORMATS)

        assert "Frank Herbert" in release.extra["author"]

    def test_the_folder_name_is_kept_for_bundle_detection(self):
        response = _response(
            files=[
                _file("@@a\\Herbert\\Dune Complete Series\\01.mp3"),
                _file("@@a\\Herbert\\Dune Complete Series\\02.mp3"),
            ]
        )

        (release,) = build_releases([response], content_type="audiobook", formats=AUDIO_FORMATS)

        assert "Complete Series" in release.extra["title_raw"]


class TestAvailability:
    def test_unavailable_without_an_api_key(self, monkeypatch):
        import shelfmark.release_sources.slskd.source as mod

        monkeypatch.setattr(mod.config, "get", TestSlskdSource()._config(SLSKD_API_KEY=""))

        assert SlskdSource().is_available() is False
