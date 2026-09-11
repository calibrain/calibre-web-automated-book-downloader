"""Blackhole download client that saves torrent files for an external watcher."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from shelfmark.core.config import config
from shelfmark.core.naming import sanitize_filename
from shelfmark.download.clients import (
    DownloadClient,
    DownloadState,
    DownloadStatus,
    register_client,
)
from shelfmark.download.clients._coercion import config_text
from shelfmark.download.clients.torrent_utils import extract_torrent_info


@register_client("torrent")
class BlackholeClient(DownloadClient):
    """Write fetched torrent files to a directory watched by another downloader."""

    protocol = "torrent"
    name = "blackhole"
    handoff_only = True
    prefers_torrent_file = True

    def __init__(self) -> None:
        directory = config_text(config.get("BLACKHOLE_DIRECTORY", ""))
        if not directory:
            msg = "BLACKHOLE_DIRECTORY is required"
            raise ValueError(msg)
        self._directory = Path(directory)

    @staticmethod
    def is_configured() -> bool:
        return config_text(config.get("PROWLARR_TORRENT_CLIENT", "")) == "blackhole" and bool(
            config_text(config.get("BLACKHOLE_DIRECTORY", ""))
        )

    def test_connection(self) -> tuple[bool, str]:
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            return False, f"Could not create Blackhole directory: {error}"
        return True, f"Blackhole directory is ready: {self._directory}"

    def add_download(
        self,
        url: str,
        name: str,
        category: str | None = None,
        expected_hash: str | None = None,
        **kwargs: object,
    ) -> str:
        torrent_info = extract_torrent_info(url, expected_hash=expected_hash)
        if not torrent_info.torrent_data:
            msg = "Blackhole requires a .torrent file; this release only provides a magnet link"
            raise ValueError(msg)

        self._directory.mkdir(parents=True, exist_ok=True)
        filename = f"{sanitize_filename(name) or 'torrent'}.torrent"
        destination = self._next_destination(filename)
        file_descriptor, temporary_path = tempfile.mkstemp(
            dir=self._directory,
            prefix=".blackhole-",
            suffix=".tmp",
        )
        try:
            with os.fdopen(file_descriptor, "wb") as temporary_file:
                temporary_file.write(torrent_info.torrent_data)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            Path(temporary_path).replace(destination)
        except Exception:
            Path(temporary_path).unlink(missing_ok=True)
            raise

        return str(destination)

    def get_status(self, download_id: str) -> DownloadStatus:
        file_path = Path(download_id)
        if file_path.is_file():
            return DownloadStatus(
                progress=100,
                state=DownloadState.COMPLETE,
                message="Torrent file saved",
                complete=True,
                file_path=str(file_path),
            )
        return DownloadStatus.error("Blackhole torrent file was not created")

    def remove(self, download_id: str, *, delete_files: bool = False) -> bool:
        return False

    def get_download_path(self, download_id: str) -> str | None:
        return download_id if Path(download_id).is_file() else None

    def _next_destination(self, filename: str) -> Path:
        candidate = self._directory / filename
        if not candidate.exists():
            return candidate

        stem = Path(filename).stem
        suffix = Path(filename).suffix
        index = 1
        while True:
            candidate = self._directory / f"{stem}_{index}{suffix}"
            if not candidate.exists():
                return candidate
            index += 1
