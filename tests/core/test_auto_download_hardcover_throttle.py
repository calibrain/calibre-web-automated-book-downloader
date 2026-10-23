"""A throttled Hardcover lookup in the auto-download pass is an error, not a skip."""

from shelfmark.core import auto_download
from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers.hardcover import HardcoverProvider, HardcoverRateLimitError


def test_a_throttled_lookup_is_an_error_not_a_skip(monkeypatch):
    """'skipped: book not found in provider' hid every 429 in the cycle summary."""

    def throttled_query(self, query, variables, *, raise_on_error=False):
        if raise_on_error:
            raise HardcoverRateLimitError("Hardcover rate limit still hit after 3 attempts")
        return None

    monkeypatch.setattr(HardcoverProvider, "_execute_query", throttled_query)
    get_metadata_cache().clear()  # get_book is cached by id
    monkeypatch.setattr(
        "shelfmark.metadata_providers.get_provider",
        lambda *a, **k: HardcoverProvider(api_key="hc_pat_" + "a" * 32),
    )
    monkeypatch.setattr("shelfmark.metadata_providers.get_provider_kwargs", lambda *a: {})
    monkeypatch.setattr("shelfmark.metadata_providers.is_provider_registered", lambda *a: True)

    outcome = auto_download.auto_download_request(
        None,  # type: ignore[arg-type]
        {"id": 5, "book_data": {"provider": "hardcover", "provider_id": "7"}},
        sources=[],
        content_type="ebook",
        min_seeders=1,
        queue_release=lambda *a, **k: (True, None),
        admin_user_id=1,
    )

    assert outcome.status == "error"
