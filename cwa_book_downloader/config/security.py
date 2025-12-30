"""Authentication settings registration."""

from typing import Any, Dict

from werkzeug.security import generate_password_hash

from cwa_book_downloader.core.logger import setup_logger
from cwa_book_downloader.core.settings_registry import (
    register_settings,
    register_on_save,
    load_config_file,
    TextField,
    PasswordField,
    CheckboxField,
    ActionButton,
)

logger = setup_logger(__name__)


def _clear_builtin_credentials() -> Dict[str, Any]:
    """Clear built-in credentials to allow public access."""
    try:
        config = load_config_file("security")
        config.pop("BUILTIN_USERNAME", None)
        config.pop("BUILTIN_PASSWORD_HASH", None)

        # Save the cleared config
        from cwa_book_downloader.core.settings_registry import _get_config_file_path, _ensure_config_dir
        import json

        _ensure_config_dir("security")
        config_path = _get_config_file_path("security")
        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        logger.info("Cleared credentials")
        return {"success": True, "message": "Credentials cleared. The app is now publicly accessible."}

    except Exception as e:
        logger.error(f"Failed to clear credentials: {e}")
        return {"success": False, "message": f"Failed to clear credentials: {str(e)}"}


def _on_save_security(values: Dict[str, Any]) -> Dict[str, Any]:
    """
    Custom save handler for security settings.

    Handles password validation and hashing:
    - If new password is provided, validate confirmation and hash it
    - If password fields are empty, preserve existing hash
    - Never store raw passwords
    - Ensure username is present if password is set

    Returns:
        Dict with processed values to save and any validation errors.
    """
    password = values.get("BUILTIN_PASSWORD", "")
    password_confirm = values.get("BUILTIN_PASSWORD_CONFIRM", "")

    # Remove raw password fields - they should never be persisted
    values.pop("BUILTIN_PASSWORD", None)
    values.pop("BUILTIN_PASSWORD_CONFIRM", None)

    # If password is provided, validate and hash it
    if password:
        if not values.get("BUILTIN_USERNAME"):
            return {
                "error": True,
                "message": "Username cannot be empty",
                "values": values
            }

        if password != password_confirm:
            return {
                "error": True,
                "message": "Passwords do not match",
                "values": values
            }

        if len(password) < 4:
            return {
                "error": True,
                "message": "Password must be at least 4 characters",
                "values": values
            }

        # Hash the password
        values["BUILTIN_PASSWORD_HASH"] = generate_password_hash(password)
        logger.info("Password hash updated")

    # If no password provided but username is being set, preserve existing hash
    elif "BUILTIN_USERNAME" in values:
        existing = load_config_file("security")
        if "BUILTIN_PASSWORD_HASH" in existing:
            values["BUILTIN_PASSWORD_HASH"] = existing["BUILTIN_PASSWORD_HASH"]

    return {"error": False, "values": values}


@register_settings("security", "Security", icon="shield", order=5)
def security_settings():
    """Security and authentication settings."""
    from cwa_book_downloader.config.env import CWA_DB_PATH
    import os

    cwa_db_available = CWA_DB_PATH and os.path.exists(CWA_DB_PATH)

    fields = [
        TextField(
            key="BUILTIN_USERNAME",
            label="Username",
            description="Set a username and password to require login. Leave both empty for public access.",
            placeholder="Enter username",
            env_supported=False,
            disabled_when={"field": "USE_CWA_AUTH", "value": True, "reason": "Using Calibre-Web database for authentication."},
        ),
        PasswordField(
            key="BUILTIN_PASSWORD",
            label="Set Password",
            description="Fill in to set or change the password.",
            placeholder="Enter new password",
            env_supported=False,
            disabled_when={"field": "USE_CWA_AUTH", "value": True, "reason": "Using Calibre-Web database for authentication."},
        ),
        PasswordField(
            key="BUILTIN_PASSWORD_CONFIRM",
            label="Confirm Password",
            placeholder="Confirm new password",
            env_supported=False,
            disabled_when={"field": "USE_CWA_AUTH", "value": True, "reason": "Using Calibre-Web database for authentication."},
        ),
        ActionButton(
            key="clear_credentials",
            label="Clear Credentials",
            description="Remove login requirement and make the app publicly accessible.",
            style="danger",
            callback=_clear_builtin_credentials,
            disabled_when={"field": "USE_CWA_AUTH", "value": True, "reason": "Using Calibre-Web database for authentication."},
        ),
        CheckboxField(
            key="USE_CWA_AUTH",
            label="Use Calibre-Web Database",
            description=(
                "Use your existing Calibre-Web user credentials for authentication."
            ),
            default=False,
            env_supported=False,
            disabled=not cwa_db_available,
            disabled_reason="Mount your Calibre-Web app.db to /auth/app.db in docker compose to enable.",
        ),
    ]

    return fields


# Register the on_save handler for this tab
register_on_save("security", _on_save_security)
