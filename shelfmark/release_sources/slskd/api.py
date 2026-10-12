"""slskd API client - talks to a slskd (Soulseek daemon) instance over its REST API.

Only the small slice of the v0 API that Shelfmark needs is wrapped here: searches,
download transfers, and the two read-only endpoints used to test the connection and
learn where slskd writes completed files.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import requests

from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
from shelfmark.download.network import get_ssl_verify

logger = setup_logger(__name__)

API_PREFIX = "/api/v0"
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_METHOD_NOT_ALLOWED = 405

# slskd transfer states are "Phase" or "Completed, Outcome" strings.
TRANSFER_STATE_COMPLETED_PREFIX = "Completed"
TRANSFER_STATE_SUCCEEDED = "Completed, Succeeded"


class SlskdError(Exception):
    """Raised when slskd returns an error or an unexpected payload."""


class SlskdAuthError(SlskdError):
    """Raised when slskd rejects the configured API key."""


class SlskdBatchUnsupportedError(SlskdError):
    """slskd has no batch download endpoint (it is older than 0.26)."""


def transfer_is_complete(state: str | None) -> bool:
    """Whether a slskd transfer state string is terminal."""
    return str(state or "").startswith(TRANSFER_STATE_COMPLETED_PREFIX)


# A search ends on slskd's own clock; this is how far past the wait one may run before we
# stop waiting for it (its responses keep resetting slskd's timer while peers answer).
SEARCH_FINISH_MARGIN_SECONDS = 60.0
# How long a search that reports itself complete may take to show ``endedAt``. slskd sets
# it in the write that saves the responses; a slskd that never reports it is read after this.
SEARCH_SAVE_WAIT_SECONDS = 10.0


def transfer_succeeded(state: str | None) -> bool:
    """Whether a slskd transfer state string is the successful terminal state."""
    return str(state or "").strip() == TRANSFER_STATE_SUCCEEDED


class SlskdClient:
    """Client for the slskd v0 REST API."""

    def __init__(self, url: str, api_key: str, timeout: int = 30) -> None:
        self.base_url = normalize_http_url(url).rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()

    # ── plumbing ──────────────────────────────────────────────────────────────

    def _api_url(self, path: str) -> str:
        base = self.base_url
        base = base.removesuffix(API_PREFIX)
        return f"{base}{API_PREFIX}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = self._api_url(path)
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        logger.debug("slskd API: %s %s", method, url)
        response = self._session.request(
            method,
            url,
            headers=headers,
            json=json,
            params=params,
            timeout=self.timeout,
            verify=get_ssl_verify(url),
        )
        if response.status_code in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
            raise SlskdAuthError("slskd rejected the API key")
        response.raise_for_status()
        return response

    @staticmethod
    def _json(response: requests.Response) -> Any:
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as e:
            raise SlskdError(f"slskd returned a non-JSON response: {e}") from e

    # ── application / options ─────────────────────────────────────────────────

    def get_application(self) -> dict[str, Any]:
        """Return slskd's application state (version, Soulseek server connection...)."""
        data = self._json(self._request("GET", "application"))
        if not isinstance(data, dict):
            raise SlskdError("slskd application state has an unexpected shape")
        return data

    def get_download_directory(self) -> str | None:
        """Return the directory slskd writes completed downloads to (as slskd sees it)."""
        data = self._json(self._request("GET", "options"))
        if not isinstance(data, dict):
            return None
        directories = data.get("directories")
        if not isinstance(directories, dict):
            return None
        downloads = str(directories.get("downloads") or "").strip()
        return downloads or None

    def test_connection(self) -> tuple[bool, str]:
        """Test connectivity and Soulseek login state. Returns (success, message)."""
        logger.info("Testing slskd connection to: %s", self.base_url)
        try:
            app = self.get_application()
        except SlskdAuthError:
            return False, "Invalid API key"
        except requests.exceptions.ConnectionError:
            return False, "Could not connect. Check the URL."
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            return False, f"HTTP error {status}"
        except (requests.exceptions.RequestException, SlskdError) as e:
            return False, f"Connection failed: {e!s}"

        version = app.get("version")
        version_text = ""
        if isinstance(version, dict):
            version_text = str(version.get("current") or version.get("full") or "").strip()
        elif isinstance(version, str):
            version_text = version.strip()
        label = f"slskd {version_text}".strip()

        server = app.get("server")
        server_state = ""
        logged_in = False
        if isinstance(server, dict):
            server_state = str(server.get("state") or "").strip()
            logged_in = bool(server.get("isLoggedIn"))

        if not logged_in:
            state_text = server_state or "unknown"
            return (
                False,
                f"Connected to {label}, but it is not logged in to Soulseek "
                f"(server state: {state_text}). Check the Soulseek credentials in slskd.",
            )
        return True, f"Connected to {label} (logged in to Soulseek)"

    def is_logged_in(self) -> bool:
        """Whether slskd currently has a logged-in Soulseek session."""
        try:
            server = self.get_application().get("server")
        except requests.exceptions.RequestException, SlskdError:
            return False
        return isinstance(server, dict) and bool(server.get("isLoggedIn"))

    # ── searches ──────────────────────────────────────────────────────────────

    def start_search(
        self,
        search_text: str,
        *,
        timeout_ms: int,
        response_limit: int,
        file_limit: int = 10000,
    ) -> str:
        """Start a network search and return its slskd id."""
        payload: dict[str, Any] = {
            "searchText": search_text,
            "searchTimeout": int(timeout_ms),
            "responseLimit": int(response_limit),
            "fileLimit": int(file_limit),
        }
        data = self._json(self._request("POST", "searches", json=payload))
        search_id = data.get("id") if isinstance(data, dict) else None
        if not search_id:
            raise SlskdError("slskd did not return a search id")
        return str(search_id)

    def get_search(self, search_id: str) -> dict[str, Any]:
        """Return the state of a search (without its responses)."""
        data = self._json(self._request("GET", f"searches/{quote(search_id, safe='')}"))
        if not isinstance(data, dict):
            raise SlskdError("slskd search state has an unexpected shape")
        return data

    def get_search_responses(self, search_id: str) -> list[dict[str, Any]]:
        """Return the peer responses collected so far for a search."""
        data = self._json(self._request("GET", f"searches/{quote(search_id, safe='')}/responses"))
        if not isinstance(data, list):
            return []
        return [r for r in data if isinstance(r, dict)]

    def delete_search(self, search_id: str) -> None:
        """Forget a search. Best effort; slskd retains them otherwise."""
        try:
            self._request("DELETE", f"searches/{quote(search_id, safe='')}")
        except (requests.exceptions.RequestException, SlskdError) as e:
            logger.debug("Failed to delete slskd search %s: %s", search_id, e)

    def search(
        self,
        search_text: str,
        *,
        wait_seconds: float,
        response_limit: int,
        poll_interval: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Run a search to the end and return its responses.

        slskd searches are asynchronous: one ends ``searchTimeout`` after its last response,
        or when ``responseLimit`` peers have answered, so a busy one can outlast
        ``wait_seconds``. A started search is never cut short: it is not cancelled, and it
        is not deleted until slskd has saved its responses.

        Complete is not saved. slskd flags a search complete on its last state change and
        saves the responses in a second write, which also sets ``endedAt``.
        ``GET .../responses`` reads what was saved, so reading earlier returns nothing, and
        deleting earlier makes that second write fail. So this waits for ``endedAt``. A
        search still running ``SEARCH_FINISH_MARGIN_SECONDS`` past the wait is left alone
        (slskd keeps its record) and this returns nothing.
        """
        if not search_text.strip():
            return []

        search_id = self.start_search(
            search_text,
            timeout_ms=int(wait_seconds * 1000),
            response_limit=response_limit,
        )

        deadline = time.monotonic() + wait_seconds + SEARCH_FINISH_MARGIN_SECONDS
        completed_at: float | None = None
        responses: list[dict[str, Any]] | None = None
        while True:
            state = self.get_search(search_id)
            now = time.monotonic()
            if state.get("endedAt"):
                responses = self.get_search_responses(search_id)  # saved: endedAt came with them
                break
            if state.get("isComplete") or transfer_is_complete(state.get("state")):
                completed_at = completed_at if completed_at is not None else now
                if now - completed_at >= SEARCH_SAVE_WAIT_SECONDS:
                    # No endedAt: an older slskd. Take its word, unless it heard peers and
                    # nothing is saved.
                    responses = self.get_search_responses(search_id)
                    if not responses and (state.get("responseCount") or 0) > 0:
                        responses = None
                    break
            elif now >= deadline:
                break
            time.sleep(poll_interval)

        if responses is None:
            logger.warning(
                "slskd search '%s' did not finish saving; leaving it for slskd", search_text
            )
            return []

        self.delete_search(search_id)
        logger.debug("slskd search '%s': %d responses", search_text, len(responses))
        return responses

    # ── transfers ─────────────────────────────────────────────────────────────

    def enqueue_downloads(
        self,
        username: str,
        files: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Ask slskd to download ``files`` (``{"filename", "size"}``) from ``username``.

        Returns the transfer records slskd created. Newer slskd versions return them in
        the response body; older ones return an empty 201, in which case the transfers
        are looked up by filename from the user's download list.
        """
        payload = [{"filename": str(f["filename"]), "size": int(f.get("size") or 0)} for f in files]
        response = self._request(
            "POST", f"transfers/downloads/{quote(username, safe='')}", json=payload
        )
        data = self._json(response)

        if isinstance(data, dict):
            failed = data.get("failed")
            if isinstance(failed, list) and failed:
                first = failed[0] if isinstance(failed[0], dict) else {}
                reason = first.get("reason") or first.get("message") or "unknown reason"
                raise SlskdError(f"slskd refused {len(failed)} file(s): {reason}")
            enqueued = data.get("enqueued")
            if isinstance(enqueued, list):
                return [t for t in enqueued if isinstance(t, dict)]

        wanted = {str(f["filename"]) for f in files}
        return [t for t in self.list_downloads(username) if str(t.get("filename") or "") in wanted]

    def enqueue_batch(
        self,
        username: str,
        files: list[dict[str, Any]],
        destination: str,
    ) -> dict[str, Any]:
        """Queue ``files`` from ``username`` as one batch landing in ``destination``.

        ``destination`` is a path relative to slskd's downloads folder, so every download of
        ours gets a folder of its own. Returns ``{"batch_id", "transfers", "failures"}``:
        the transfers slskd created and the files it refused (already queued, say). A slskd
        that predates batches answers 404 or 405, reported as ``SlskdBatchUnsupportedError``
        so the caller can fall back to the plain enqueue.
        """
        payload = {
            "username": username,
            "files": [
                {"filename": str(f["filename"]), "size": int(f.get("size") or 0)} for f in files
            ],
            "options": {"destination": destination},
        }
        try:
            response = self._request("POST", "transfers/downloads/batches", json=payload)
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code in (
                _HTTP_NOT_FOUND,
                _HTTP_METHOD_NOT_ALLOWED,
            ):
                raise SlskdBatchUnsupportedError("slskd has no batch download endpoint") from e
            raise
        data = self._json(response)
        data = data if isinstance(data, dict) else {}
        raw_batch = data.get("batch")
        batch = raw_batch if isinstance(raw_batch, dict) else {}
        failures = data.get("failures")
        return {
            "batch_id": str(batch.get("id") or ""),
            "transfers": [t for t in batch.get("transfers") or [] if isinstance(t, dict)],
            "failures": [f for f in failures or [] if isinstance(f, dict)],
        }

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        """One batch with all its transfers, removed ones included; None when it is gone."""
        try:
            response = self._request(
                "GET", f"transfers/downloads/batches/{quote(batch_id, safe='')}"
            )
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == _HTTP_NOT_FOUND:
                return None
            raise
        data = self._json(response)
        return data if isinstance(data, dict) else None

    def get_events(self, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        """slskd's stored events, NEWEST first (``DownloadFileComplete`` and so on).

        They live in slskd's database, so they outlive anything clearing the transfer list.
        A slskd without the endpoint yields nothing.
        """
        try:
            response = self._request("GET", "events", params={"offset": offset, "limit": limit})
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == _HTTP_NOT_FOUND:
                return []
            raise
        data = self._json(response)
        return [e for e in data if isinstance(e, dict)] if isinstance(data, list) else []

    def list_downloads(self, username: str) -> list[dict[str, Any]]:
        """Return every download transfer slskd tracks for ``username``, flattened."""
        try:
            response = self._request("GET", f"transfers/downloads/{quote(username, safe='')}")
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == _HTTP_NOT_FOUND:
                return []
            raise
        data = self._json(response)
        return _flatten_transfers(data)

    def get_transfer(self, username: str, transfer_id: str) -> dict[str, Any] | None:
        """Return one transfer record, or None when slskd no longer tracks it."""
        try:
            response = self._request(
                "GET",
                f"transfers/downloads/{quote(username, safe='')}/{quote(transfer_id, safe='')}",
            )
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == _HTTP_NOT_FOUND:
                return None
            raise
        data = self._json(response)
        return data if isinstance(data, dict) else None

    def cancel_transfer(self, username: str, transfer_id: str, *, remove: bool) -> bool:
        """Cancel a transfer; with ``remove`` also drop it from slskd's list."""
        try:
            self._request(
                "DELETE",
                f"transfers/downloads/{quote(username, safe='')}/{quote(transfer_id, safe='')}",
                params={"remove": "true" if remove else "false"},
            )
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == _HTTP_NOT_FOUND:
                return False
            raise
        return True


def _flatten_transfers(data: Any) -> list[dict[str, Any]]:
    """Flatten slskd's ``{username, directories: [{directory, files: [...]}]}`` shape."""
    if isinstance(data, list):
        # GET /transfers/downloads returns one entry per user.
        transfers: list[dict[str, Any]] = []
        for entry in data:
            transfers.extend(_flatten_transfers(entry))
        return transfers
    if not isinstance(data, dict):
        return []
    directories = data.get("directories")
    if not isinstance(directories, list):
        return []
    transfers = []
    for directory in directories:
        if not isinstance(directory, dict):
            continue
        files = directory.get("files")
        if not isinstance(files, list):
            continue
        transfers.extend(f for f in files if isinstance(f, dict))
    return transfers
