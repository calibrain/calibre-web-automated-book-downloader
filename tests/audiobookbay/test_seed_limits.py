"""Seed time and ratio for AudiobookBay torrents are handed to the torrent client."""

from unittest.mock import patch

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.release_sources.audiobookbay.handler import AudiobookBayHandler, _seed_limits

MAGNET = "magnet:?xt=urn:btih:abc123"


def _config(**values):
    settings = {"ABB_HOSTNAME": "audiobookbay.lu", **values}

    def _get(key, default=None, user_id=None):
        del user_id
        return settings.get(key, default)

    return patch("shelfmark.release_sources.audiobookbay.handler.config.get", side_effect=_get)


def _task(**kwargs):
    return DownloadTask(
        task_id="https://audiobookbay.lu/abss/test-book/",
        source="audiobookbay",
        title="Test Book",
        content_type="audiobook",
        **kwargs,
    )


def _resolve(task):
    return AudiobookBayHandler()._resolve_download(task, lambda status, message=None: None)


class TestSeedLimits:
    def test_nothing_set_leaves_the_clients_own_limits(self):
        with _config():
            assert _seed_limits() == (None, None)

    def test_zero_means_the_clients_own_limit(self):
        with _config(ABB_SEED_TIME_MINUTES=0, ABB_SEED_RATIO=0.0):
            assert _seed_limits() == (None, None)

    def test_both_are_read(self):
        with _config(ABB_SEED_TIME_MINUTES=1440, ABB_SEED_RATIO=1.5):
            assert _seed_limits() == (1440, 1.5)

    def test_the_two_are_independent(self):
        with _config(ABB_SEED_TIME_MINUTES=60):
            assert _seed_limits() == (60, None)
        with _config(ABB_SEED_RATIO=2):
            assert _seed_limits() == (None, 2.0)

    @pytest.mark.parametrize("value", ["soon", None, [], {}, float("nan"), float("inf"), -1, "-2"])
    def test_a_bad_ratio_is_ignored(self, value):
        with _config(ABB_SEED_RATIO=value):
            assert _seed_limits()[1] is None

    @pytest.mark.parametrize("value", ["soon", None, [], -5])
    def test_a_bad_seed_time_is_ignored(self, value):
        with _config(ABB_SEED_TIME_MINUTES=value):
            assert _seed_limits()[0] is None

    def test_a_ratio_given_as_text_is_read(self):
        with _config(ABB_SEED_RATIO="1.25"):
            assert _seed_limits()[1] == 1.25


class TestRequest:
    @patch("shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link")
    def test_a_new_download_carries_the_limits(self, mock_extract):
        mock_extract.return_value = MAGNET
        with _config(ABB_SEED_TIME_MINUTES=720, ABB_SEED_RATIO=2.0):
            request = _resolve(_task())

        assert request is not None
        assert request.seeding_time_limit == 720
        assert request.ratio_limit == 2.0

    @patch("shelfmark.release_sources.audiobookbay.handler.scraper.extract_magnet_link")
    def test_a_new_download_without_limits_carries_none(self, mock_extract):
        mock_extract.return_value = MAGNET
        with _config():
            request = _resolve(_task())

        assert request is not None
        assert (request.seeding_time_limit, request.ratio_limit) == (None, None)

    def test_a_retry_from_the_cached_magnet_carries_the_limits_too(self):
        task = _task(retry_source_context={"magnet_link": MAGNET})
        with _config(ABB_SEED_TIME_MINUTES=720, ABB_SEED_RATIO=2.0):
            request = _resolve(task)

        assert request is not None
        assert request.url == MAGNET
        assert (request.seeding_time_limit, request.ratio_limit) == (720, 2.0)
