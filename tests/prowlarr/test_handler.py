"""
Unit tests for the Prowlarr download handler.

These tests mock the download clients to test the handler logic
without requiring running services.
"""

import tempfile
from pathlib import Path
from threading import Event
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.download.clients import (
    DownloadState,
    DownloadStatus,
)
from shelfmark.release_sources import Release, ReleaseProtocol
from shelfmark.release_sources.prowlarr.cache import cache_release, remove_release
from shelfmark.release_sources.prowlarr.handler import ProwlarrHandler
from shelfmark.release_sources.prowlarr.utils import (
    build_source_id,
    get_preferred_download_url,
    get_protocol,
)


class ProgressRecorder:
    """Records progress and status updates during download."""

    def __init__(self):
        self.progress_values: list[float] = []
        self.status_updates: list[tuple[str, str | None]] = []

    def progress_callback(self, progress: float):
        self.progress_values.append(progress)

    def status_callback(self, status: str, message: str | None):
        self.status_updates.append((status, message))

    @property
    def last_status(self) -> str | None:
        return self.status_updates[-1][0] if self.status_updates else None

    @property
    def last_message(self) -> str | None:
        return self.status_updates[-1][1] if self.status_updates else None

    @property
    def statuses(self) -> list[str]:
        return [s[0] for s in self.status_updates]


class TestGetProtocol:
    """Tests for the get_protocol function."""

    def test_get_protocol_torrent(self):
        """Test detecting torrent protocol."""
        result = {"protocol": "torrent"}
        assert get_protocol(result) == "torrent"

    def test_get_protocol_usenet(self):
        """Test detecting usenet protocol."""
        result = {"protocol": "usenet"}
        assert get_protocol(result) == "usenet"

    def test_get_protocol_unknown(self):
        """Test unknown protocol."""
        result = {"protocol": "ftp"}
        assert get_protocol(result) == "unknown"

    def test_get_protocol_empty(self):
        """Test empty protocol."""
        result = {}
        assert get_protocol(result) == "unknown"

    def test_get_protocol_case_insensitive(self):
        """Test protocol detection is case insensitive."""
        assert get_protocol({"protocol": "TORRENT"}) == "torrent"
        assert get_protocol({"protocol": "Usenet"}) == "usenet"
        assert get_protocol({"protocol": "USENET"}) == "usenet"


def test_blackhole_prefers_torrent_file_over_magnet():
    result = {
        "protocol": "torrent",
        "downloadUrl": "https://prowlarr.example/download/123",
        "magnetUrl": "magnet:?xt=urn:btih:abc123",
    }

    assert get_preferred_download_url(result, prefer_torrent_file=True) == result["downloadUrl"]


class TestProwlarrHandlerDownloadErrors:
    """Tests for error handling in ProwlarrHandler.download()."""

    def test_download_fails_without_cached_release(self):
        """Test that download fails when release is not in cache."""
        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value=None,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="non-existent-id",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert recorder.last_message is not None
            assert "could not be refreshed" in recorder.last_message

    def test_download_fails_clearly_when_cache_miss_cannot_refresh(self):
        """Prowlarr retry URLs are not durable; cache misses must refresh by identity."""
        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value=None,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.ProwlarrSource.search",
                return_value=[],
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="retry-context-release",
                source="prowlarr",
                title="Recovered Release",
                retry_download_url="magnet:?xt=urn:btih:abc123",
                retry_download_protocol="torrent",
                retry_release_name="Recovered Release",
                retry_seeding_time_limit_minutes=60,
                retry_ratio_limit=1.5,
            )
            recorder = ProgressRecorder()

            request = handler._resolve_download(task, recorder.status_callback)

            assert request is None
            assert recorder.last_status == "error"
            assert recorder.last_message is not None
            assert "could not be refreshed" in recorder.last_message

    def test_cache_miss_re_resolves_and_uses_fresh_download_url(self):
        """Cache miss should re-query Prowlarr and use a fresh exact-match URL."""
        task_id = "fresh-guid-1"
        fresh_url = "https://prowlarr.example.com/download/fresh-token"

        def mock_search(*_args, **_kwargs):
            cache_release(
                task_id,
                {
                    "guid": task_id,
                    "protocol": "torrent",
                    "downloadUrl": fresh_url,
                    "title": "Fresh Release",
                },
            )
            return [
                Release(
                    source="prowlarr",
                    source_id=task_id,
                    title="Fresh Release",
                    info_url="https://tracker.example.com/release/1",
                    protocol=ReleaseProtocol.TORRENT,
                )
            ]

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"

        remove_release(task_id)
        try:
            with (
                patch(
                    "shelfmark.release_sources.prowlarr.handler.ProwlarrSource.search",
                    side_effect=mock_search,
                ) as mock_search_method,
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_client",
                    return_value=mock_client,
                ),
                patch.object(ProwlarrHandler, "_poll_and_complete", return_value=None),
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id=task_id,
                    source="prowlarr",
                    title="Fresh Book",
                    retry_source_context={"indexer": "MyIndexer"},
                )
                recorder = ProgressRecorder()

                handler.download(
                    task=task,
                    cancel_flag=Event(),
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                mock_search_method.assert_called_once()
                assert mock_client.add_download.call_args.kwargs["url"] == fresh_url
        finally:
            remove_release(task_id)

    def test_stale_cached_url_add_failure_re_resolves_once_and_succeeds(self):
        """Expired cached proxy URL should be refreshed once after qBittorrent hash failure."""
        task_id = "stale-guid-1"
        stale_url = "https://prowlarr.example.com/download/stale-token"
        fresh_url = "https://prowlarr.example.com/download/fresh-token"

        def mock_search(*_args, **_kwargs):
            cache_release(
                task_id,
                {
                    "guid": task_id,
                    "protocol": "torrent",
                    "downloadUrl": fresh_url,
                    "title": "Fresh Release",
                },
            )
            return [
                Release(
                    source="prowlarr",
                    source_id=task_id,
                    title="Fresh Release",
                    protocol=ReleaseProtocol.TORRENT,
                )
            ]

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.side_effect = [
            RuntimeError("Could not determine torrent hash from URL"),
            "download_id",
        ]

        cache_release(
            task_id,
            {
                "guid": task_id,
                "protocol": "torrent",
                "downloadUrl": stale_url,
                "title": "Stale Release",
            },
        )
        try:
            with (
                patch(
                    "shelfmark.release_sources.prowlarr.handler.ProwlarrSource.search",
                    side_effect=mock_search,
                ) as mock_search_method,
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_client",
                    return_value=mock_client,
                ),
                patch.object(ProwlarrHandler, "_poll_and_complete", return_value=None),
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(task_id=task_id, source="prowlarr", title="Stale Book")
                recorder = ProgressRecorder()

                handler.download(
                    task=task,
                    cancel_flag=Event(),
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert mock_client.add_download.call_count == 2
                assert mock_client.add_download.call_args_list[0].kwargs["url"] == stale_url
                assert mock_client.add_download.call_args_list[1].kwargs["url"] == fresh_url
                mock_search_method.assert_called_once()
        finally:
            remove_release(task_id)

    def test_refresh_without_exact_match_fails_clearly(self):
        """Refresh must not pick a different Prowlarr result when identity differs."""
        task_id = "missing-guid-1"
        other_id = "other-guid-1"

        def mock_search(*_args, **_kwargs):
            cache_release(
                other_id,
                {
                    "guid": other_id,
                    "protocol": "torrent",
                    "downloadUrl": "https://prowlarr.example.com/download/other",
                    "title": "Other Release",
                },
            )
            return [
                Release(
                    source="prowlarr",
                    source_id=other_id,
                    title="Other Release",
                    protocol=ReleaseProtocol.TORRENT,
                )
            ]

        remove_release(task_id)
        remove_release(other_id)
        try:
            with patch(
                "shelfmark.release_sources.prowlarr.handler.ProwlarrSource.search",
                side_effect=mock_search,
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(task_id=task_id, source="prowlarr", title="Missing Book")
                recorder = ProgressRecorder()

                request = handler._resolve_download(task, recorder.status_callback)

                assert request is None
                assert recorder.last_status == "error"
                assert recorder.last_message is not None
                assert "could not be refreshed" in recorder.last_message
        finally:
            remove_release(task_id)
            remove_release(other_id)

    def test_magnet_result_does_not_trigger_prowlarr_url_refresh(self):
        """Magnet failures should not be treated as expired Prowlarr proxy URLs."""
        task_id = "magnet-guid-1"
        magnet = "magnet:?xt=urn:btih:abc123&dn=test"
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.side_effect = RuntimeError(
            "Could not determine torrent hash from URL"
        )

        cache_release(
            task_id,
            {
                "guid": task_id,
                "protocol": "torrent",
                "downloadUrl": "https://prowlarr.example.com/download/stale-token",
                "magnetUrl": magnet,
                "title": "Magnet Release",
            },
        )
        try:
            with (
                patch(
                    "shelfmark.release_sources.prowlarr.handler.ProwlarrSource.search",
                    return_value=[],
                ) as mock_search_method,
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_client",
                    return_value=mock_client,
                ),
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(task_id=task_id, source="prowlarr", title="Magnet Book")
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=Event(),
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is None
                assert mock_client.add_download.call_count == 1
                assert mock_client.add_download.call_args.kwargs["url"] == magnet
                mock_search_method.assert_not_called()
        finally:
            remove_release(task_id)

    def test_download_fails_without_download_url(self):
        """Test that download fails when release has no download URL."""
        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "title": "Test Release",
                # No downloadUrl or magnetUrl
            },
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="no-url-release",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert recorder.last_message is not None
            assert "url" in recorder.last_message.lower()

    def test_download_fails_unknown_protocol(self):
        """Test that download fails with unknown protocol."""
        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "ftp",
                "downloadUrl": "ftp://example.com/file.zip",
            },
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="unknown-protocol",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert recorder.last_message is not None
            assert "protocol" in recorder.last_message.lower()

    def test_download_fails_no_client_configured(self):
        """Test that download fails when no client is configured."""
        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "downloadUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=None,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.list_configured_clients",
                return_value=[],
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="no-client",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert recorder.last_message is not None
            assert "client" in recorder.last_message.lower()


class TestProwlarrHandlerSeedCriteria:
    """Tests for seed criteria passed through from Prowlarr."""

    def test_resolve_download_ignores_torznab_minimum_seed_criteria(self):
        with patch(
            "shelfmark.release_sources.prowlarr.handler.get_release",
            return_value={
                "protocol": "torrent",
                "title": "Test Release",
                "magnetUrl": "magnet:?xt=urn:btih:abc123",
                "minimumSeedTime": 259200,
                "minimumRatio": 1,
            },
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="seed-time-conversion",
                source="prowlarr",
                title="Test Book",
            )

            request = handler._resolve_download(task, lambda *_: None)

            assert request is not None
            assert request.seeding_time_limit is None
            assert request.ratio_limit is None

    def test_resolve_download_uses_configured_seed_time_minutes(self):
        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "title": "Test Release",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    "configuredSeedTimeMinutes": 7200,
                    "configuredRatioLimit": 2,
                    "minimumSeedTime": 259200,
                    "minimumRatio": 1,
                },
            ),
            patch("shelfmark.release_sources.prowlarr.handler.config.get", return_value=True),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="configured-seed-time",
                source="prowlarr",
                title="Test Book",
            )

            request = handler._resolve_download(task, lambda *_: None)

            assert request is not None
            assert request.seeding_time_limit == 7200
            assert request.ratio_limit == 2.0

    def test_resolve_download_ignores_configured_seed_time_when_disabled(self):
        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "title": "Test Release",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    "configuredSeedTimeMinutes": 7200,
                    "configuredRatioLimit": 2,
                },
            ),
            patch("shelfmark.release_sources.prowlarr.handler.config.get", return_value=False),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="configured-seed-time-disabled",
                source="prowlarr",
                title="Test Book",
            )

            request = handler._resolve_download(task, lambda *_: None)

            assert request is not None
            assert request.seeding_time_limit is None
            assert request.ratio_limit is None

    def test_resolve_download_falls_back_to_prowlarr_when_enrichment_missing(self):
        """Regression test for #795: when search-time enrichment is missing,
        share limits are re-resolved from Prowlarr at grab time."""
        mock_client = MagicMock()
        mock_client.get_indexer_seed_settings.return_value = {
            5: {"seeding_time_limit_minutes": 4320, "ratio_limit": 1.0}
        }

        def config_get(key, default=None):
            return True if key == "PROWLARR_USE_SEED_PREFERENCES" else default

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "title": "Test Release",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    "indexerId": 5,
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.config.get",
                side_effect=config_get,
            ),
            patch.object(
                ProwlarrHandler,
                "_build_prowlarr_client",
                return_value=mock_client,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="seed-time-fallback",
                source="prowlarr",
                title="Test Book",
            )

            request = handler._resolve_download(task, lambda *_: None)

            assert request is not None
            assert request.seeding_time_limit == 4320
            assert request.ratio_limit == 1.0
            mock_client.get_indexer_seed_settings.assert_called_once_with(restrict_to=[5])

    def test_resolve_download_fallback_failure_leaves_limits_unset(self):
        mock_client = MagicMock()
        mock_client.get_indexer_seed_settings.side_effect = RuntimeError("prowlarr down")

        def config_get(key, default=None):
            return True if key == "PROWLARR_USE_SEED_PREFERENCES" else default

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "title": "Test Release",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    "indexerId": 5,
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.config.get",
                side_effect=config_get,
            ),
            patch.object(
                ProwlarrHandler,
                "_build_prowlarr_client",
                return_value=mock_client,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="seed-time-fallback-failure",
                source="prowlarr",
                title="Test Book",
            )

            request = handler._resolve_download(task, lambda *_: None)

            assert request is not None
            assert request.seeding_time_limit is None
            assert request.ratio_limit is None

    def test_resolve_download_skips_fallback_when_enrichment_present(self):
        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "title": "Test Release",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    "indexerId": 5,
                    "configuredSeedTimeMinutes": 7200,
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.config.get",
                side_effect=lambda key, default=None: (
                    True if key == "PROWLARR_USE_SEED_PREFERENCES" else default
                ),
            ),
            patch.object(ProwlarrHandler, "_build_prowlarr_client") as mock_builder,
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="seed-time-no-fallback",
                source="prowlarr",
                title="Test Book",
            )

            request = handler._resolve_download(task, lambda *_: None)

            assert request is not None
            assert request.seeding_time_limit == 7200
            mock_builder.assert_not_called()

    def test_download_passes_seed_limits_to_client(self):
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "title": "Test Release",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    "configuredSeedTimeMinutes": 7200,
                    "configuredRatioLimit": 1.25,
                    "minimumSeedTime": 259200,
                    "minimumRatio": 1,
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ),
            patch("shelfmark.release_sources.prowlarr.handler.config.get", return_value=True),
            patch.object(
                ProwlarrHandler,
                "_poll_and_complete",
                return_value=None,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="seed-limit-pass-through",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            call_kwargs = mock_client.add_download.call_args.kwargs
            assert call_kwargs["seeding_time_limit"] == 7200
            assert call_kwargs["ratio_limit"] == 1.25


class TestProwlarrHandlerContentType:
    """Regression tests for issue #1235 — content type must reach the client."""

    def test_download_passes_content_type_to_client(self):
        """rTorrent picks its audiobook label from content_type, not category."""
        mock_client = MagicMock()
        mock_client.name = "rtorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "title": "Test Release",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ),
            patch("shelfmark.release_sources.prowlarr.handler.config.get", return_value=True),
            patch.object(
                ProwlarrHandler,
                "_poll_and_complete",
                return_value=None,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="content-type-pass-through",
                source="prowlarr",
                title="Test Audiobook",
                content_type="audiobook",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            call_kwargs = mock_client.add_download.call_args.kwargs
            assert call_kwargs["content_type"] == "audiobook"
            # rTorrent gets no category, so content_type is its only audiobook signal.
            assert call_kwargs["category"] is None


class TestProwlarrHandlerExistingDownload:
    """Tests for handling existing downloads."""

    def test_prefers_magnet_url_for_torrents(self):
        """If both downloadUrl and magnetUrl exist, torrents should use magnetUrl."""
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "downloadUrl": "https://prowlarr.example.com/api/v1/indexer/1/download/123",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123&dn=test",
                    "title": "Test Release",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ),
            patch.object(
                ProwlarrHandler,
                "_poll_and_complete",
                return_value=None,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="torrent-prefers-magnet", source="prowlarr", title="Test Book"
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert mock_client.find_existing.call_count == 1
            called_url = mock_client.find_existing.call_args.args[0]
            assert called_url == "magnet:?xt=urn:btih:abc123&dn=test"

    def test_prefers_download_url_for_usenet(self):
        """If both downloadUrl and magnetUrl exist, usenet should use downloadUrl."""
        mock_client = MagicMock()
        mock_client.name = "sabnzbd"
        mock_client.find_existing.return_value = None

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "usenet",
                    "downloadUrl": "https://prowlarr.example.com/api/v1/indexer/1/download/456",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123&dn=test",
                    "title": "Test Release",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.remove_release",
            ),
            patch.object(
                ProwlarrHandler,
                "_poll_and_complete",
                return_value=None,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="usenet-prefers-download", source="prowlarr", title="Test Book"
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert mock_client.find_existing.call_count == 1
            called_url = mock_client.find_existing.call_args.args[0]
            assert called_url == "https://prowlarr.example.com/api/v1/indexer/1/download/456"

    def test_uses_existing_complete_download(self):
        """Test that handler uses existing complete download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create a test file
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = (
                "existing_id",
                DownloadStatus(
                    progress=100,
                    state=DownloadState.COMPLETE,
                    message="Complete",
                    complete=True,
                    file_path=str(source_file),
                ),
            )
            mock_client.get_download_path.return_value = str(source_file)

            with (
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_release",
                    return_value={
                        "protocol": "torrent",
                        "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    },
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_client",
                    return_value=mock_client,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.remove_release",
                ),
                patch(
                    "shelfmark.download.staging.get_staging_dir",
                    return_value=staging_dir,
                ),
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="existing-complete",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                assert "resolving" in recorder.statuses
                # Should NOT have called add_download
                mock_client.add_download.assert_not_called()


class TestProwlarrHandlerPolling:
    """Tests for download polling behavior."""

    def test_retries_torrent_not_found_errors(self):
        """ "Torrent not found" should be treated as transient."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            poll_count = [0]

            def mock_get_status(download_id):
                poll_count[0] += 1
                if poll_count[0] <= 2:
                    return DownloadStatus(
                        progress=0,
                        state=DownloadState.ERROR,
                        message="Torrent not found in qBittorrent",
                        complete=False,
                        file_path=None,
                    )

                return DownloadStatus(
                    progress=100,
                    state=DownloadState.COMPLETE,
                    message="Complete",
                    complete=True,
                    file_path=str(source_file),
                )

            mock_client = MagicMock()
            mock_client.name = "qbittorrent"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.side_effect = mock_get_status
            mock_client.get_download_path.return_value = str(source_file)

            with (
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_release",
                    return_value={
                        "protocol": "torrent",
                        "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    },
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_client",
                    return_value=mock_client,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.remove_release",
                ),
                patch(
                    "shelfmark.download.staging.get_staging_dir",
                    return_value=staging_dir,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                    0.01,
                ),
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="poll-not-found-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                assert poll_count[0] >= 3
                assert "resolving" in recorder.statuses

    def test_fails_fast_on_auth_errors(self):
        """Auth/API errors should not be retried as "not found"."""
        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=0,
            state=DownloadState.ERROR,
            message="qBittorrent authentication failed (HTTP 403)",
            complete=False,
            file_path=None,
        )

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="poll-auth-fail-test",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            assert "authentication failed" in (recorder.last_message or "").lower()

    def test_polls_until_complete(self):
        """Test that handler polls until download is complete."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            poll_count = [0]

            def mock_get_status(download_id):
                poll_count[0] += 1
                if poll_count[0] >= 3:
                    return DownloadStatus(
                        progress=100,
                        state=DownloadState.COMPLETE,
                        message="Complete",
                        complete=True,
                        file_path=str(source_file),
                    )
                return DownloadStatus(
                    progress=poll_count[0] * 30,
                    state=DownloadState.DOWNLOADING,
                    message=None,
                    complete=False,
                    file_path=None,
                    download_speed=1024000,
                    eta=60,
                )

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.side_effect = mock_get_status
            mock_client.get_download_path.return_value = str(source_file)

            with (
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_release",
                    return_value={
                        "protocol": "torrent",
                        "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    },
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_client",
                    return_value=mock_client,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.remove_release",
                ),
                patch(
                    "shelfmark.download.staging.get_staging_dir",
                    return_value=staging_dir,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                    0.01,  # Speed up tests
                ),
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="poll-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                assert poll_count[0] >= 3
                assert len(recorder.progress_values) >= 3

    def test_handles_error_during_download(self):
        """Test that handler handles error state during download."""
        mock_client = MagicMock()
        mock_client.name = "test_client"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=50,
            state=DownloadState.ERROR,
            message="Disk full",
            complete=False,
            file_path=None,
        )

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="error-test",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert recorder.last_status == "error"
            mock_client.remove.assert_not_called()


class TestProwlarrHandlerCancellation:
    """Tests for download cancellation."""

    def test_cancellation_does_not_remove_torrent(self):
        """Test that torrent cancellation does not remove from client."""
        mock_client = MagicMock()
        mock_client.name = "test_client"
        mock_client.find_existing.return_value = None
        mock_client.add_download.return_value = "download_id"
        mock_client.get_status.return_value = DownloadStatus(
            progress=50,
            state=DownloadState.DOWNLOADING,
            message="Downloading",
            complete=False,
            file_path=None,
        )

        with (
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_release",
                return_value={
                    "protocol": "torrent",
                    "magnetUrl": "magnet:?xt=urn:btih:abc123",
                },
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.get_client",
                return_value=mock_client,
            ),
            patch(
                "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                0.01,
            ),
        ):
            handler = ProwlarrHandler()
            task = DownloadTask(
                task_id="cancel-test",
                source="prowlarr",
                title="Test Book",
            )
            cancel_flag = Event()
            recorder = ProgressRecorder()

            # Set cancel immediately
            cancel_flag.set()

            result = handler.download(
                task=task,
                cancel_flag=cancel_flag,
                progress_callback=recorder.progress_callback,
                status_callback=recorder.status_callback,
            )

            assert result is None
            assert "cancelled" in recorder.statuses
            mock_client.remove.assert_not_called()


class TestProwlarrHandlerCancel:
    """Tests for ProwlarrHandler.cancel()."""

    def test_cancel_removes_from_cache(self):
        """Test that cancel removes release from cache."""
        with patch("shelfmark.release_sources.prowlarr.handler.remove_release") as mock_remove:
            handler = ProwlarrHandler()
            result = handler.cancel("test-task-id")

            assert result is True
            mock_remove.assert_called_once_with("test-task-id")

    def test_cancel_handles_missing_task(self):
        """Test that cancel handles non-existent task gracefully."""
        with patch("shelfmark.release_sources.prowlarr.handler.remove_release"):
            handler = ProwlarrHandler()
            result = handler.cancel("nonexistent-task-id")

            assert result is True


class TestProwlarrHandlerFileStaging:
    """Tests for file staging behavior."""

    def test_stages_single_file(self):
        """Test staging a single file download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("test content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.return_value = DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=str(source_file),
            )
            mock_client.get_download_path.return_value = str(source_file)

            with (
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_release",
                    return_value={
                        "protocol": "torrent",
                        "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    },
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_client",
                    return_value=mock_client,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.remove_release",
                ),
                patch(
                    "shelfmark.download.staging.get_staging_dir",
                    return_value=staging_dir,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                    0.01,
                ),
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="staging-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                staged_file = Path(result)
                assert staged_file.exists()
                assert staged_file.read_text() == "test content"

    def test_stages_directory(self):
        """Test staging a directory download."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_dir = Path(tmp_dir) / "source" / "book_folder"
            source_dir.mkdir(parents=True)
            (source_dir / "book.epub").write_text("epub content")
            (source_dir / "cover.jpg").write_bytes(b"image data")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.return_value = DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=str(source_dir),
            )
            mock_client.get_download_path.return_value = str(source_dir)

            with (
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_release",
                    return_value={
                        "protocol": "torrent",
                        "magnetUrl": "magnet:?xt=urn:btih:abc123",
                    },
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_client",
                    return_value=mock_client,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.remove_release",
                ),
                patch(
                    "shelfmark.download.staging.get_staging_dir",
                    return_value=staging_dir,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                    0.01,
                ),
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="dir-staging-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                staged_dir = Path(result)
                assert staged_dir.is_dir()
                assert (staged_dir / "book.epub").exists()
                assert (staged_dir / "cover.jpg").exists()

    def test_handles_duplicate_filename(self):
        """Usenet downloads return the original file path (no staging)."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_file = Path(tmp_dir) / "source" / "book.epub"
            source_file.parent.mkdir(parents=True)
            source_file.write_text("new content")

            staging_dir = Path(tmp_dir) / "staging"
            staging_dir.mkdir()
            # Create existing file with same name
            (staging_dir / "book.epub").write_text("old content")

            mock_client = MagicMock()
            mock_client.name = "test_client"
            mock_client.find_existing.return_value = None
            mock_client.add_download.return_value = "download_id"
            mock_client.get_status.return_value = DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Complete",
                complete=True,
                file_path=str(source_file),
            )
            mock_client.get_download_path.return_value = str(source_file)

            # Use usenet protocol - torrents skip staging and return original path directly
            with (
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_release",
                    return_value={
                        "protocol": "usenet",
                        "downloadUrl": "https://indexer.example.com/download/123",
                    },
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.get_client",
                    return_value=mock_client,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.remove_release",
                ),
                patch(
                    "shelfmark.download.staging.get_staging_dir",
                    return_value=staging_dir,
                ),
                patch(
                    "shelfmark.release_sources.prowlarr.handler.POLL_INTERVAL",
                    0.01,
                ),
            ):
                handler = ProwlarrHandler()
                task = DownloadTask(
                    task_id="dup-staging-test",
                    source="prowlarr",
                    title="Test Book",
                )
                cancel_flag = Event()
                recorder = ProgressRecorder()

                result = handler.download(
                    task=task,
                    cancel_flag=cancel_flag,
                    progress_callback=recorder.progress_callback,
                    status_callback=recorder.status_callback,
                )

                assert result is not None
                returned_file = Path(result)
                assert returned_file == source_file
                assert returned_file.exists()
                assert returned_file.read_text() == "new content"


class TestProwlarrHandlerPostProcessCleanup:
    def test_torrent_change_category_sets_post_import_category(self):
        handler = ProwlarrHandler()
        task = DownloadTask(task_id="torrent-category", source="prowlarr", title="Test")

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.set_category.return_value = True
        handler._cleanup_refs[task.task_id] = (mock_client, "abc123", "torrent")

        config_values = {
            "PROWLARR_TORRENT_ACTION": "change_category",
            "PROWLARR_TORRENT_POST_IMPORT_CATEGORY": "imported",
        }
        with patch(
            "shelfmark.download.clients.base_handler.config.get",
            side_effect=lambda key, default="": config_values.get(key, default),
        ):
            handler.post_process_cleanup(task, success=True)

        mock_client.set_category.assert_called_once_with("abc123", "imported")
        mock_client.remove.assert_not_called()

    def test_torrent_keep_does_not_change_category(self):
        handler = ProwlarrHandler()
        task = DownloadTask(task_id="torrent-no-category", source="prowlarr", title="Test")

        mock_client = MagicMock()
        handler._cleanup_refs[task.task_id] = (mock_client, "abc123", "torrent")

        with patch("shelfmark.download.clients.base_handler.config.get", return_value="keep"):
            handler.post_process_cleanup(task, success=True)

        mock_client.set_category.assert_not_called()
        mock_client.remove.assert_not_called()

    def test_torrent_change_category_ignores_empty_category(self):
        handler = ProwlarrHandler()
        task = DownloadTask(task_id="torrent-empty-category", source="prowlarr", title="Test")

        mock_client = MagicMock()
        handler._cleanup_refs[task.task_id] = (mock_client, "abc123", "torrent")

        config_values = {
            "PROWLARR_TORRENT_ACTION": "change_category",
            "PROWLARR_TORRENT_POST_IMPORT_CATEGORY": "",
        }
        with patch(
            "shelfmark.download.clients.base_handler.config.get",
            side_effect=lambda key, default="": config_values.get(key, default),
        ):
            handler.post_process_cleanup(task, success=True)

        mock_client.set_category.assert_not_called()
        mock_client.remove.assert_not_called()

    def test_usenet_move_triggers_client_cleanup(self):
        handler = ProwlarrHandler()
        task = DownloadTask(task_id="cleanup-test", source="prowlarr", title="Test")

        mock_client = MagicMock()
        mock_client.name = "nzbget"
        handler._cleanup_refs[task.task_id] = (mock_client, "123", "usenet")

        with patch("shelfmark.download.clients.base_handler.config.get", return_value="move"):
            handler.post_process_cleanup(task, success=True)

        mock_client.remove.assert_called_once_with("123", delete_files=True)

    def test_usenet_copy_does_not_cleanup(self):
        handler = ProwlarrHandler()
        task = DownloadTask(task_id="cleanup-test", source="prowlarr", title="Test")

        mock_client = MagicMock()
        mock_client.name = "nzbget"
        handler._cleanup_refs[task.task_id] = (mock_client, "123", "usenet")

        with patch("shelfmark.download.clients.base_handler.config.get", return_value="copy"):
            handler.post_process_cleanup(task, success=True)

        mock_client.remove.assert_not_called()

    def test_usenet_move_logs_cleanup_failure(self):
        handler = ProwlarrHandler()
        task = DownloadTask(task_id="cleanup-failure", source="prowlarr", title="Test")

        mock_client = MagicMock()
        mock_client.name = "nzbget"
        handler._cleanup_refs[task.task_id] = (mock_client, "123", "usenet")
        handler._delete_local_download_data = MagicMock(side_effect=ConnectionError("offline"))

        with (
            patch("shelfmark.download.clients.base_handler.config.get", return_value="move"),
            patch("shelfmark.download.clients.base_handler.logger.warning") as mock_warning,
        ):
            handler.post_process_cleanup(task, success=True)

        mock_warning.assert_called_once()
        args = mock_warning.call_args.args
        assert args[0] == "Failed to cleanup usenet download %s in %s: %s"
        assert args[1] == "123"
        assert args[2] == "nzbget"
        assert str(args[3]) == "offline"

    def test_torrent_remove_logs_cleanup_failure(self):
        handler = ProwlarrHandler()
        task = DownloadTask(task_id="torrent-cleanup-failure", source="prowlarr", title="Test")

        mock_client = MagicMock()
        mock_client.name = "qbittorrent"
        mock_client.remove.side_effect = ConnectionError("offline")
        handler._cleanup_refs[task.task_id] = (mock_client, "123", "torrent")

        with (
            patch("shelfmark.download.clients.base_handler.config.get", return_value="remove"),
            patch("shelfmark.download.clients.base_handler.logger.warning") as mock_warning,
        ):
            handler.post_process_cleanup(task, success=True)

        mock_warning.assert_called_once()
        args = mock_warning.call_args.args
        assert args[0] == "Failed to remove torrent %s from %s: %s"
        assert args[1] == "123"
        assert args[2] == "qbittorrent"
        assert str(args[3]) == "offline"

    def test_delete_local_download_data_ignores_path_lookup_failure(self):
        handler = ProwlarrHandler()
        mock_client = MagicMock()
        mock_client.name = "nzbget"
        mock_client.get_download_path.side_effect = ConnectionError("path lookup failed")

        with patch("shelfmark.download.clients.base_handler.logger.debug") as mock_debug:
            handler._delete_local_download_data(mock_client, "123")

        mock_debug.assert_called_once()
        args = mock_debug.call_args.args
        assert args[0] == "Failed to resolve download path for %s %s: %s"
        assert args[1] == "nzbget"
        assert args[2] == "123"
        assert str(args[3]) == "path lookup failed"

    def test_delete_local_download_data_logs_delete_failure(self, tmp_path, monkeypatch):
        import shelfmark.core.path_mappings as path_mappings
        import shelfmark.download.clients.base_handler as base_handler

        handler = ProwlarrHandler()
        download_file = tmp_path / "downloads" / "book.epub"
        download_file.parent.mkdir(parents=True)
        download_file.write_text("content")

        mock_client = MagicMock()
        mock_client.name = "nzbget"
        mock_client.get_download_path.return_value = str(download_file)

        monkeypatch.setattr(path_mappings, "get_client_host_identifier", lambda _client: "")
        monkeypatch.setattr(path_mappings, "parse_remote_path_mappings", lambda _value: [])
        monkeypatch.setattr(
            path_mappings,
            "remap_remote_to_local_with_match",
            lambda *, mappings, host, remote_path: (remote_path, None),
        )

        def fake_run_blocking_io(func, *args, **kwargs):
            name = getattr(func, "__name__", "")
            if name == "exists":
                return True
            if name == "is_dir":
                return False
            if name == "unlink":
                raise ConnectionError("delete failed")
            return func(*args, **kwargs)

        monkeypatch.setattr(base_handler, "run_blocking_io", fake_run_blocking_io)

        with patch("shelfmark.download.clients.base_handler.logger.warning") as mock_warning:
            handler._delete_local_download_data(mock_client, "123")

        mock_warning.assert_called_once()
        args = mock_warning.call_args.args
        assert args[0] == "Failed to delete local download data for %s %s: %s"
        assert args[1] == "nzbget"
        assert args[2] == "123"
        assert str(args[3]) == "delete failed"


class TestRawReleaseMatchesTask:
    """Refreshing a stale release has to find it by whichever id form the task holds."""

    RAW = {
        "guid": "https://tracker.example/torrent/555",
        "infoUrl": "https://tracker.example/details/555",
        "indexerId": 25,
    }

    def test_matches_the_indexer_qualified_source_id(self):
        assert ProwlarrHandler._raw_release_matches_task(
            self.RAW, "25:https://tracker.example/torrent/555"
        )

    def test_still_matches_a_bare_guid_from_a_task_queued_before_the_change(self):
        assert ProwlarrHandler._raw_release_matches_task(
            self.RAW, "https://tracker.example/torrent/555"
        )

    def test_still_matches_a_bare_info_url(self):
        assert ProwlarrHandler._raw_release_matches_task(
            self.RAW, "https://tracker.example/details/555"
        )

    def test_does_not_match_the_same_guid_from_a_different_indexer_entry(self):
        assert not ProwlarrHandler._raw_release_matches_task(
            self.RAW, "10:https://tracker.example/torrent/555"
        )

    def test_does_not_match_an_unrelated_release(self):
        assert not ProwlarrHandler._raw_release_matches_task(self.RAW, "25:something-else")


class TestRawReleaseMatchesTaskEdgeCases:
    """Matching the wrong release here grabs the wrong torrent."""

    def test_blank_task_id_never_matches(self):
        raw = {"guid": "g", "infoUrl": "i", "indexerId": 25}

        assert not ProwlarrHandler._raw_release_matches_task(raw, "")
        assert not ProwlarrHandler._raw_release_matches_task(raw, "   ")
        assert not ProwlarrHandler._raw_release_matches_task(raw, None)

    def test_a_release_with_no_identifiers_does_not_match_a_blank_task_id(self):
        assert not ProwlarrHandler._raw_release_matches_task({}, None)
        assert not ProwlarrHandler._raw_release_matches_task({}, "")

    def test_matches_a_qualified_id_built_from_a_guidless_release(self):
        raw = {"indexerId": 25, "indexer": "MAM", "title": "Dune"}
        built = build_source_id(raw)

        assert ProwlarrHandler._raw_release_matches_task(raw, built)
