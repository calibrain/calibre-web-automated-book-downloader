"""Shared helpers for user-overridable settings metadata and payloads."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from shelfmark.core.settings_registry import (
    CheckboxField,
    MultiSelectField,
    NumberField,
    SelectField,
    load_config_file,
)

if TYPE_CHECKING:
    from types import ModuleType

    from shelfmark.core.user_db import UserDB


def get_settings_registry() -> ModuleType:
    """Load settings modules and return the shared settings registry module."""
    # Ensure settings modules are loaded before reading registry metadata.
    import_module("shelfmark.config.notifications_settings")
    import_module("shelfmark.config.security")
    import_module("shelfmark.config.settings")
    import_module("shelfmark.config.users_settings")
    from shelfmark.core import settings_registry

    return settings_registry


def get_ordered_user_overridable_fields(tab_name: str) -> list[tuple[str, Any]]:
    """Return user-overridable fields for a tab in UI display order."""
    settings_registry = get_settings_registry()
    tab = settings_registry.get_settings_tab(tab_name)
    if not tab:
        return []
    overridable_map = settings_registry.get_user_overridable_fields(tab_name=tab_name)
    return [(field.key, field) for field in tab.fields if field.key in overridable_map]


# Global values a self-service user may see when they have not set the key
# themselves. These are mode switches and option picks the settings form needs
# to lay itself out, and they carry nothing private. Everything else (free text
# such as paths and email addresses, tables such as notification routes whose
# URLs embed tokens) stays with the admin.
_SELF_SERVICE_VISIBLE_FIELD_TYPES = (CheckboxField, NumberField, SelectField, MultiSelectField)
# Select fields whose value identifies something in the admin's own accounts.
_SELF_SERVICE_HIDDEN_KEYS = frozenset({"BOOKLORE_LIBRARY_ID", "BOOKLORE_PATH_ID"})


def is_global_value_visible_to_user(key: str, field: object) -> bool:
    """Return whether a self-service user may see the global value of a field."""
    if key in _SELF_SERVICE_HIDDEN_KEYS:
        return False
    return isinstance(field, _SELF_SERVICE_VISIBLE_FIELD_TYPES)


def build_user_preferences_payload(
    user_db: UserDB,
    user_id: int,
    tab_name: str,
    *,
    for_self_service: bool = False,
) -> dict[str, Any]:
    """Build the effective user-preferences payload for a settings tab.

    With ``for_self_service`` the payload is meant for the user it describes:
    global values they did not set themselves are replaced with ``None`` unless
    `is_global_value_visible_to_user` allows them. Admin callers get full data.
    """
    from shelfmark.core.config import config as app_config

    settings_registry = get_settings_registry()
    ordered_fields = get_ordered_user_overridable_fields(tab_name)
    if not ordered_fields:
        tab_label = tab_name.capitalize()
        msg = f"{tab_label} settings tab not found"
        raise ValueError(msg)

    tab_config = load_config_file(tab_name)
    user_settings = user_db.get_user_settings(user_id)
    ordered_keys = [key for key, _ in ordered_fields]

    fields_payload: list[dict[str, Any]] = []
    global_values: dict[str, Any] = {}
    effective: dict[str, dict[str, Any]] = {}

    for key, field in ordered_fields:
        serialized = settings_registry.serialize_field(field, tab_name, include_value=False)
        serialized["fromEnv"] = bool(
            field.env_supported and settings_registry.is_value_from_env(field)
        )
        fields_payload.append(serialized)

        show_global = not for_self_service or is_global_value_visible_to_user(key, field)
        global_values[key] = app_config.get(key, field.default) if show_global else None

        source = "default"
        value = app_config.get(key, field.default, user_id=user_id)
        if field.env_supported and settings_registry.is_value_from_env(field):
            source = "env_var"
        elif key in user_settings and user_settings[key] is not None:
            source = "user_override"
            value = user_settings[key]
        elif key in tab_config:
            source = "global_config"

        if source != "user_override" and not show_global:
            value = None

        effective[key] = {"value": value, "source": source}

    user_overrides = {
        key: user_settings[key]
        for key in ordered_keys
        if key in user_settings and user_settings[key] is not None
    }

    return {
        "tab": tab_name,
        "keys": ordered_keys,
        "fields": fields_payload,
        "globalValues": global_values,
        "userOverrides": user_overrides,
        "effective": effective,
    }
