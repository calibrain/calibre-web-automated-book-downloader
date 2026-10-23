"""The app databases wait out a busy writer instead of failing at SQLite's 5 s default."""

import pytest

from shelfmark.core.activity_view_state_service import ActivityViewStateService
from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.user_db import UserDB

# SQLite's own default is 5000 ms. A start-up migration or a restart reconcile that meets a
# writer for a few seconds should wait, not raise.
_MIN_BUSY_TIMEOUT_MS = 15_000


@pytest.mark.parametrize(
    "service_cls",
    [UserDB, DownloadHistoryService, ActivityViewStateService],
)
def test_app_database_connections_wait_for_a_busy_writer(tmp_path, service_cls):
    service = service_cls(str(tmp_path / "users.db"))

    conn = service._connect()
    try:
        busy_timeout_ms = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert busy_timeout_ms >= _MIN_BUSY_TIMEOUT_MS
