"""Hardcover allows a 10 request burst refilling at 60/min and answers over-limit
calls with HTTP 429 plus a Retry-After header. Every request goes through
``_execute_query``, so that is where the client paces itself and backs off."""

from typing import Any

import pytest
import requests

from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import hardcover
from shelfmark.metadata_providers.hardcover import (
    HardcoverProvider,
    HardcoverRateLimitError,
    _TokenBucket,
)

PAT = "hc_pat_" + "a" * 32
BOOK = {
    "books": [
        {
            "id": 7,
            "title": "Dune",
            "contributions": [{"author": {"name": "Frank Herbert"}}],
        }
    ]
}


class FakeClock:
    """Monotonic clock whose sleep just moves time forward."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeResponse:
    def __init__(self, status: int, body: dict | None = None, headers: dict | None = None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self) -> dict:
        return self._body


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.posts = 0

    def post(self, *_args: Any, **_kwargs: Any) -> FakeResponse:
        self.posts += 1
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def ok(data: dict | None = None) -> FakeResponse:
    return FakeResponse(200, {"data": data if data is not None else BOOK})


def throttled(retry_after: str | None = None) -> FakeResponse:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return FakeResponse(429, {"error": "Too Many Requests"}, headers)


@pytest.fixture(autouse=True)
def _fresh_metadata_cache():
    """get_book is cached by id; keep one test's book from answering the next."""
    get_metadata_cache().clear()
    yield
    get_metadata_cache().clear()


@pytest.fixture
def clock(monkeypatch) -> FakeClock:
    """Swap the shared limiter for a fake clock; nothing really sleeps."""
    fake = FakeClock()
    monkeypatch.setattr(hardcover, "_rate_limiter", _TokenBucket(5, 1.0, fake, fake.sleep))
    monkeypatch.setattr(hardcover, "_retry_jitter", lambda: 0.0)
    return fake


def provider_with(*responses: FakeResponse) -> tuple[HardcoverProvider, FakeSession]:
    provider = HardcoverProvider(api_key=PAT)
    session = FakeSession(list(responses))
    provider.session = session  # type: ignore[assignment]
    return provider, session


class TestTokenBucket:
    def test_allows_a_burst_without_waiting(self):
        fake = FakeClock()
        bucket = _TokenBucket(5, 1.0, fake, fake.sleep)

        for _ in range(5):
            bucket.acquire()

        assert fake.sleeps == []

    def test_waits_for_a_token_once_the_burst_is_spent(self):
        fake = FakeClock()
        bucket = _TokenBucket(5, 0.5, fake, fake.sleep)
        for _ in range(5):
            bucket.acquire()

        bucket.acquire()

        assert sum(fake.sleeps) == pytest.approx(2.0)

    def test_refills_with_time(self):
        fake = FakeClock()
        bucket = _TokenBucket(5, 1.0, fake, fake.sleep)
        for _ in range(5):
            bucket.acquire()
        fake.now += 3.0

        for _ in range(3):
            bucket.acquire()

        assert fake.sleeps == []

    def test_idle_time_does_not_bank_more_than_the_burst(self):
        fake = FakeClock()
        bucket = _TokenBucket(5, 1.0, fake, fake.sleep)
        fake.now += 10_000.0
        for _ in range(5):
            bucket.acquire()
        assert fake.sleeps == []

        bucket.acquire()

        assert sum(fake.sleeps) == pytest.approx(1.0)

    def test_sustained_rate_never_exceeds_the_refill_rate(self):
        fake = FakeClock()
        bucket = _TokenBucket(5, 1.0, fake, fake.sleep)
        start = fake.now

        for _ in range(65):
            bucket.acquire()

        # 5 free, then one per second.
        assert fake.now - start == pytest.approx(60.0)

    def test_penalize_blocks_the_next_acquire(self):
        fake = FakeClock()
        bucket = _TokenBucket(5, 1.0, fake, fake.sleep)

        bucket.penalize(12.0)
        bucket.acquire()

        assert sum(fake.sleeps) >= 12.0


class TestExecuteQueryThrottling:
    def test_every_request_takes_a_token(self, clock):
        provider, session = provider_with(ok())

        for _ in range(8):
            provider._execute_query("query { me { id } }", {})

        assert session.posts == 8
        # 5 burst + 3 paced at one per second.
        assert sum(clock.sleeps) == pytest.approx(3.0)

    def test_429_is_retried_after_the_retry_after_delay(self, clock):
        provider, session = provider_with(throttled("7"), ok())

        result = provider._execute_query("query { me { id } }", {})

        assert result == BOOK
        assert session.posts == 2
        assert sum(clock.sleeps) >= 7.0

    def test_429_without_retry_after_backs_off_and_grows(self, clock):
        provider, session = provider_with(throttled(), throttled(), ok())

        result = provider._execute_query("query { me { id } }", {})

        assert result == BOOK
        assert session.posts == 3
        waits = [s for s in clock.sleeps if s > 0]
        assert len(waits) == 2
        assert waits[1] > waits[0] > 0

    def test_unparseable_retry_after_falls_back_to_backoff(self, clock):
        provider, session = provider_with(throttled("Wed, 21 Oct 2026 07:28:00 GMT"), ok())

        assert provider._execute_query("query { me { id } }", {}) == BOOK
        assert session.posts == 2
        assert sum(clock.sleeps) > 0

    def test_retries_are_bounded(self, clock):
        provider, session = provider_with(throttled("1"))

        with pytest.raises(HardcoverRateLimitError):
            provider._execute_query("query { me { id } }", {}, raise_on_error=True)

        assert session.posts == hardcover.HARDCOVER_RATE_LIMIT_ATTEMPTS

    def test_exhausted_429_returns_none_without_raise_on_error(self, clock):
        provider, session = provider_with(throttled("1"))

        assert provider._execute_query("query { me { id } }", {}) is None
        assert session.posts == hardcover.HARDCOVER_RATE_LIMIT_ATTEMPTS

    def test_long_retry_after_is_not_waited_out(self, clock):
        """A spent daily quota says to wait until midnight UTC; don't hold a worker for that."""
        provider, session = provider_with(throttled("3600"))

        with pytest.raises(HardcoverRateLimitError):
            provider._execute_query("query { me { id } }", {}, raise_on_error=True)

        assert session.posts == 1
        assert all(s < 60 for s in clock.sleeps)

    def test_a_429_holds_back_other_callers_too(self, clock):
        provider, _session = provider_with(throttled("20"), ok())
        provider._execute_query("query { me { id } }", {})
        clock.sleeps.clear()
        before = clock.now

        hardcover._rate_limiter.acquire()

        # The retry already waited 20s; the shared bucket starts empty afterwards.
        assert clock.now - before <= 1.0 + 1e-9

    @pytest.mark.parametrize("status", [401, 403, 500])
    def test_other_http_errors_are_not_retried(self, clock, status):
        provider, session = provider_with(FakeResponse(status, {"error": "no"}))

        assert provider._execute_query("query { me { id } }", {}) is None
        assert session.posts == 1


class TestGetBookDuringThrottle:
    def test_get_book_recovers_from_a_single_429(self, clock):
        provider, session = provider_with(throttled("1"), ok())

        book = provider.get_book("7")

        assert book is not None
        assert book.title == "Dune"
        assert session.posts == 2

    def test_get_book_raises_instead_of_reporting_not_found(self, clock):
        provider, _session = provider_with(throttled("1"))

        with pytest.raises(HardcoverRateLimitError):
            provider.get_book("7")

    def test_get_book_still_returns_none_when_graphql_rejects_it(self, clock):
        provider, _session = provider_with(FakeResponse(200, {"errors": [{"message": "nope"}]}))

        assert provider.get_book("7") is None

    def test_get_book_still_returns_none_for_a_missing_book(self, clock):
        provider, _session = provider_with(ok({"books": []}))

        assert provider.get_book("7") is None
