"""Centralized mirror configuration for direct-download sources."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shelfmark.core.utils import normalize_http_url

if TYPE_CHECKING:
    from shelfmark.core.config import Config

_config_module = None


def _get_config() -> Config:
    """Lazy import of config module to avoid circular imports."""
    global _config_module
    if _config_module is None:
        from shelfmark.core.config import config

        _config_module = config
    return _config_module


# Mirror URLs are intentionally user-supplied only.
DEFAULT_AA_MIRRORS: list[str] = []
DEFAULT_LIBGEN_MIRRORS: list[str] = []
DEFAULT_ZLIB_MIRRORS: list[str] = []
DEFAULT_WELIB_MIRRORS: list[str] = []

_DOWNLOAD_SOURCE_MIRROR_LABELS = {
    "aa-fast": "Anna's Archive",
    "aa-slow": "Anna's Archive",
    "aa-slow-nowait": "Anna's Archive",
    "aa-slow-wait": "Anna's Archive",
    "libgen": "LibGen",
    "zlib": "Z-Library",
    "welib": "Welib",
    "oceanofpdf": "OceanofPDF",
}


def _normalize_mirror_url(url: str) -> str:
    return normalize_http_url(url, default_scheme="https")


def _string_config_value(value: object) -> str:
    """Normalize mirror-related config values to strings."""
    return value if isinstance(value, str) else str(value or "")


def _normalize_configured_urls(value: object) -> list[str]:
    """Normalize list or comma-separated mirror config into unique URLs."""
    if isinstance(value, list):
        parts = value
    elif isinstance(value, str) and value.strip():
        parts = value.split(",")
    else:
        return []

    normalized_urls: list[str] = []
    for raw_url in parts:
        normalized = _normalize_mirror_url(str(raw_url))
        if normalized and normalized not in normalized_urls:
            normalized_urls.append(normalized)
    return normalized_urls


def _get_primary_mirror_url(key: str) -> str | None:
    """Return a configured primary mirror URL, if present."""
    config = _get_config()
    primary = _normalize_mirror_url(_string_config_value(config.get(key, "")))
    return primary or None


def _build_primary_and_additional_mirrors(primary_key: str, additional_key: str) -> list[str]:
    """Build an ordered mirror list from primary + additional config values."""
    config = _get_config()
    mirrors: list[str] = []

    primary = _get_primary_mirror_url(primary_key)
    if primary:
        mirrors.append(primary)

    for url in _normalize_configured_urls(config.get(additional_key, "")):
        if url not in mirrors:
            mirrors.append(url)

    return mirrors


def get_aa_mirrors() -> list[str]:
    """Get Anna's Archive mirrors.

    Returns:
        Ordered list of user-configured AA mirror URLs.

    Notes:
        - The list is used to populate the AA mirror dropdown in Settings.
        - When AA_BASE_URL is set to 'auto', mirrors are tried in the order listed.

    """
    config = _get_config()
    configured_list = _normalize_configured_urls(config.get("AA_MIRROR_URLS", None))
    if configured_list:
        return configured_list
    return _normalize_configured_urls(config.get("AA_ADDITIONAL_URLS", ""))


def has_aa_mirror_configuration() -> bool:
    """Return True when direct-download search has at least one AA base URL to use."""
    if get_aa_mirrors():
        return True

    configured_base_url = normalize_http_url(
        _string_config_value(_get_config().get("AA_BASE_URL", "auto")),
        default_scheme="https",
        allow_special=("auto",),
    )
    return bool(configured_base_url and configured_base_url != "auto")


def get_libgen_mirrors() -> list[str]:
    """Get user-configured LibGen mirrors.

    Returns:
        List of LibGen mirror URLs.

    """
    config = _get_config()
    configured_list = _normalize_configured_urls(config.get("LIBGEN_MIRROR_URLS", None))
    if configured_list:
        return configured_list
    return _normalize_configured_urls(config.get("LIBGEN_ADDITIONAL_URLS", ""))


def has_libgen_mirror_configuration() -> bool:
    """Return True when at least one LibGen mirror URL is configured."""
    return bool(get_libgen_mirrors())


def get_zlib_mirrors() -> list[str]:
    """Get user-configured Z-Library mirrors, with primary first.

    Returns:
        List of Z-Library mirror URLs, primary first.

    """
    config = _get_config()
    configured_list = _normalize_configured_urls(config.get("ZLIB_MIRROR_URLS", None))
    if configured_list:
        return configured_list
    return _build_primary_and_additional_mirrors("ZLIB_PRIMARY_URL", "ZLIB_ADDITIONAL_URLS")


def has_zlib_mirror_configuration() -> bool:
    """Return True when at least one Z-Library mirror URL is configured."""
    return bool(get_zlib_mirrors())


def get_zlib_primary_url() -> str | None:
    """Get the primary Z-Library mirror URL.

    Returns:
        Primary Z-Library mirror URL, if configured.

    """
    mirrors = get_zlib_mirrors()
    return mirrors[0] if mirrors else None


def get_zlib_url_template() -> str | None:
    """Get Z-Library URL template using configured primary mirror.

    Returns:
        URL template with {md5} placeholder, if configured.

    """
    primary = get_zlib_primary_url()
    return f"{primary}/md5/{{md5}}" if primary else None


def get_welib_mirrors() -> list[str]:
    """Get user-configured Welib mirrors, with primary first.

    Returns:
        List of Welib mirror URLs, primary first.

    """
    config = _get_config()
    configured_list = _normalize_configured_urls(config.get("WELIB_MIRROR_URLS", None))
    if configured_list:
        return configured_list
    return _build_primary_and_additional_mirrors("WELIB_PRIMARY_URL", "WELIB_ADDITIONAL_URLS")


def has_welib_mirror_configuration() -> bool:
    """Return True when at least one Welib mirror URL is configured."""
    return bool(get_welib_mirrors())


def get_oceanofpdf_mirrors() -> list[str]:
    """Get user-configured OceanofPDF mirrors, with the search mirror first."""
    return _normalize_configured_urls(_get_config().get("OCEANOFPDF_MIRROR_URLS", None))


def has_oceanofpdf_mirror_configuration() -> bool:
    """Return True when at least one OceanofPDF mirror URL is configured."""
    return bool(get_oceanofpdf_mirrors())


def get_oceanofpdf_primary_url() -> str | None:
    """Return the first configured OceanofPDF mirror, if present."""
    mirrors = get_oceanofpdf_mirrors()
    return mirrors[0] if mirrors else None


def has_download_source_mirror_configuration(source_id: str) -> bool:
    """Return True when the requested direct-download source has mirror config."""
    if source_id in {"aa-fast", "aa-slow", "aa-slow-nowait", "aa-slow-wait"}:
        return has_aa_mirror_configuration()
    if source_id == "libgen":
        return has_libgen_mirror_configuration()
    if source_id == "zlib":
        return has_zlib_mirror_configuration()
    if source_id == "welib":
        return has_welib_mirror_configuration()
    if source_id == "oceanofpdf":
        return has_oceanofpdf_mirror_configuration()
    return False


def get_download_source_missing_mirror_reason(source_id: str) -> str | None:
    """Return a user-facing reason when a direct-download source has no mirror config."""
    if has_download_source_mirror_configuration(source_id):
        return None

    label = _DOWNLOAD_SOURCE_MIRROR_LABELS.get(source_id)
    if not label:
        return None

    return f"Add at least one {label} mirror in Mirrors"


def get_welib_primary_url() -> str | None:
    """Get the primary Welib mirror URL.

    Returns:
        Primary Welib mirror URL, if configured.

    """
    mirrors = get_welib_mirrors()
    return mirrors[0] if mirrors else None


def get_welib_url_template() -> str | None:
    """Get Welib URL template using configured primary mirror.

    Returns:
        URL template with {md5} placeholder, if configured.

    """
    primary = get_welib_primary_url()
    return f"{primary}/md5/{{md5}}" if primary else None


def get_zlib_cookie_domains() -> set:
    """Get set of Z-Library domains that need full cookie handling.

    Used by internal_bypasser for CF bypass cookie management.

    Returns:
        Set of domain strings (without protocol).

    """
    domains = set()

    for url in get_zlib_mirrors():
        domain = url.replace("https://", "").replace("http://", "").split("/")[0]
        domains.add(domain)

    return domains
