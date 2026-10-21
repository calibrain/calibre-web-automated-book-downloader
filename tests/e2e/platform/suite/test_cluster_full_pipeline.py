"""The `full` profile — maximum-realism, heavy, nightly/manual only.

Test book: *Moby-Dick* by Herman Melville (public domain). Validated live.

  * **Real Chrome solves Cloudflare, end to end (VERIFIED).** The AA mirror is the
    Cloudflare gate (mock-cf): it passes search/detail through but challenges the
    AA *slow-download* page. Downloading therefore forces the in-image headless
    Chromium (seleniumbase CDP internal bypasser) to execute the challenge JS,
    harvest ``cf_clearance``, and fetch the gated slow-download page. Moby-Dick
    landing in ``/books`` is only possible if Chrome actually solved the gate —
    that is the literal "spin a chrome browser" path.
  * **DoH** enabled at boot (``USE_DOH=true``) without breaking startup.

The real torrent-client download (the other half of the `full` profile) lives in
test_cluster_clients.py, which runs under `full` and the `client-*` profiles.
Only runs under the ``full`` profile booted by ``run-e2e.sh env/full.env``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.profiles("full")

BOOK = "Moby Dick"


def _boot_log() -> str:
    path = os.environ.get("E2E_SHELFMARK_LOG")
    if not path or not Path(path).exists():
        return ""
    return Path(path).read_text(encoding="utf-8", errors="ignore")


def _books_dir() -> Path | None:
    raw = os.environ.get("E2E_BOOKS_DIR")
    return Path(raw) if raw else None


def _book_files(books: Path) -> set[str]:
    return {
        p.name for p in books.rglob("*") if p.is_file() and p.suffix.lower() in {".epub", ".pdf"}
    }


def _wait_for_new_book(books: Path, before: set[str], timeout: int = 40) -> set[str]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        new = _book_files(books) - before
        if new:
            return new
        time.sleep(2)
    return set()


def _track_id(release: dict) -> str:
    return str(release.get("source_id") or release.get("id") or release.get("guid") or "")


# --------------------------------------------------------------------------- #
# 1. Real Chrome solves Cloudflare end-to-end (the "spin a chrome browser" path)
# --------------------------------------------------------------------------- #
def test_real_chrome_solves_cloudflare_end_to_end(client) -> None:
    """Download an AA book whose slow-download is behind Cloudflare; the internal
    headless Chrome must solve the gate for the file to arrive in /books."""
    books = _books_dir()
    if books is None or not books.exists():
        pytest.skip("E2E_BOOKS_DIR not visible to the test runner")
    before = _book_files(books)

    resp = client.direct_search(BOOK)
    assert resp.status_code == 200, f"AA search failed: {resp.status_code} {resp.text[:300]}"
    releases = client.releases_from(resp)
    assert releases, "AA search returned no releases for Moby Dick"

    queued = client.queue_download(releases[0])
    assert queued.status_code in (200, 201, 202), (
        f"queue refused the AA release: {queued.status_code} {queued.text[:300]}"
    )
    book_id = _track_id(releases[0])
    assert book_id, f"release missing a trackable id: {releases[0]!r}"

    state, info = client.wait_for_terminal(book_id)
    assert state in {"complete", "done", "available"}, (
        f"AA download via the Chrome-solved Cloudflare gate did not complete: "
        f"state={state} info={info!r}"
    )

    new_files = _wait_for_new_book(books, before)
    assert new_files, (
        "download reported complete but no file landed in /books — the Cloudflare "
        "gate in front of the slow-download was not solved by Chrome"
    )
    assert all(Path(n).suffix for n in new_files), f"file written without extension: {new_files}"

    # Secondary, explicit signal that the internal bypasser (Chrome) was engaged.
    _assert_bypasser_engaged()


def _assert_bypasser_engaged() -> None:
    """Confirm shelfmark actually routed through the internal Chrome bypasser.

    Best-effort: reads the live shelfmark container logs. The file landing in /books
    is already proof (the slow-download was CF-gated), but this pins the mechanism.
    """
    if shutil.which("docker") is None:
        return
    result = subprocess.run(
        ["docker", "logs", "e2e-shelfmark"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    blob = (result.stdout + result.stderr).lower()
    if not blob.strip():
        return
    assert "bypass" in blob, (
        "no evidence the internal bypasser engaged during the download — the file "
        "may have arrived via an unexpected (non-Chrome) path"
    )


# --------------------------------------------------------------------------- #
# 2. DoH
# --------------------------------------------------------------------------- #
def test_doh_enabled_and_app_healthy(client) -> None:
    """DoH is enabled at boot and the app stays healthy (DoH init has historically
    broken startup). ``/api/config`` doesn't expose ``USE_DOH``, so verify via the
    boot log + health."""
    assert client.get("/api/health").status_code == 200
    log = _boot_log()
    if not log:
        pytest.skip("E2E_SHELFMARK_LOG not available to assert DoH")
    assert "USE_DOH=true" in log or "'USE_DOH'" in log, "USE_DOH was not synced into config at boot"


# NOTE: the real torrent-client download (Prowlarr -> qBittorrent/transmission/
# deluge/rtorrent -> /books) lives in test_cluster_clients.py, which runs under the
# `full` profile *and* the `client-*` profiles from one client-agnostic test.
