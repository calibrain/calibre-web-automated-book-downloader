"""Webshare.cz settings registration."""

from typing import Any

from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.core.settings_registry import (
    ActionButton,
    CheckboxField,
    NumberField,
    PasswordField,
    SettingsField,
    TextField,
    register_settings,
)


def _resolve_setting_text(current_values: dict[str, Any], key: str) -> str:
    """Prefer current form values, then fall back to persisted config text."""
    from shelfmark.core.config import config

    current_value = normalize_optional_text(current_values.get(key))
    if current_value is not None:
        return current_value
    return normalize_optional_text(config.get(key, "")) or ""


def _test_webshare_connection(current_values: dict[str, Any] | None = None) -> dict[str, Any]:
    """Log in with the current form credentials and report the account's VIP state."""
    from shelfmark.release_sources.webshare.api import WebshareClient, WebshareError

    current_values = current_values or {}
    username = _resolve_setting_text(current_values, "WEBSHARE_USERNAME")
    password = _resolve_setting_text(current_values, "WEBSHARE_PASSWORD")
    if not username or not password:
        return {"success": False, "message": "Username and password are required"}

    try:
        client = WebshareClient(username, password)
        client.login()
        account = client.account()
    except WebshareError as exc:
        return {"success": False, "message": f"Login failed: {exc}"}

    if account.vip:
        days = f" ({account.vip_days} days left)" if account.vip_days is not None else ""
        return {"success": True, "message": f"Logged in as {account.username}, VIP{days}"}
    return {
        "success": True,
        "message": f"Logged in as {account.username} (free account - downloads are slow)",
    }


@register_settings("webshare_config", "Webshare.cz", icon="download", order=47)
def webshare_config_settings() -> list[SettingsField]:
    """Webshare.cz configuration settings."""
    enabled = {"field": "WEBSHARE_ENABLED", "value": True}
    return [
        CheckboxField(
            key="WEBSHARE_ENABLED",
            label="Enable Webshare.cz",
            description=(
                "Search Webshare.cz by file name and download matching ebooks and "
                "audiobooks. Requires a Webshare account; a VIP account is recommended "
                "for full download speed."
            ),
            default=False,
        ),
        TextField(
            key="WEBSHARE_USERNAME",
            label="Username or Email",
            description="Your Webshare.cz login.",
            required=True,
            show_when=enabled,
        ),
        PasswordField(
            key="WEBSHARE_PASSWORD",
            label="Password",
            description="Your Webshare.cz password.",
            required=True,
            show_when=enabled,
        ),
        ActionButton(
            key="test_webshare",
            label="Test Login",
            description="Log in to Webshare.cz and show the account's VIP status.",
            style="primary",
            callback=_test_webshare_connection,
            show_when=enabled,
        ),
        NumberField(
            key="WEBSHARE_MAX_RESULTS",
            label="Max Results",
            description=(
                "Maximum number of files to request per search (1-200). Results are then "
                "filtered to your supported ebook or audiobook formats."
            ),
            default=50,
            min_value=1,
            max_value=200,
            show_when=enabled,
        ),
    ]
