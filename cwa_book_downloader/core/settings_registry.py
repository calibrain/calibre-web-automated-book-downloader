"""Plugin settings registry with config file persistence."""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type, Union
from threading import Lock

from cwa_book_downloader.core.logger import setup_logger

logger = setup_logger(__name__)


@dataclass
class FieldBase:
    """Base class for all settings fields."""
    key: str                              # Environment variable / config key
    label: str                            # Display label in UI
    description: str = ""                 # Help text
    default: Any = None                   # Default value if not set
    required: bool = False                # Whether field must have a value
    env_var: Optional[str] = None         # Override env var name (defaults to key)
    env_supported: bool = True            # Whether this setting can be set via ENV var (False = UI-only)
    disabled: bool = False                # Whether field is disabled/greyed out
    disabled_reason: str = ""             # Explanation shown when disabled
    show_when: Optional[Dict[str, Any]] = None  # Conditional visibility: {"field": "key", "value": "expected"} or {"field": "key", "notEmpty": True}
    disabled_when: Optional[Dict[str, Any]] = None  # Conditional disable: {"field": "key", "value": "expected", "reason": "..."}
    requires_restart: bool = False        # Whether changing this setting requires a container restart
    universal_only: bool = False          # Only show in Universal search mode (hide in Direct mode)

    def get_env_var_name(self) -> str:
        """Get the environment variable name for this field."""
        return self.env_var or self.key

    def get_field_type(self) -> str:
        """Get the field type name for serialization."""
        return self.__class__.__name__


@dataclass
class TextField(FieldBase):
    """Single-line text input."""
    placeholder: str = ""
    max_length: Optional[int] = None


@dataclass
class PasswordField(FieldBase):
    """Password input (masked in UI, not returned in API responses)."""
    placeholder: str = ""


@dataclass
class NumberField(FieldBase):
    """Numeric input."""
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    step: float = 1
    default: float = 0


@dataclass
class CheckboxField(FieldBase):
    """Boolean checkbox."""
    default: bool = False


@dataclass
class SelectField(FieldBase):
    """Single-choice dropdown."""
    # Options can be a list or a callable that returns a list (for lazy evaluation)
    options: Any = field(default_factory=list)  # [{value: "", label: ""}] or callable


@dataclass
class MultiSelectField(FieldBase):
    """Multiple-choice selection."""
    # Options can be a list or a callable that returns a list (for lazy evaluation)
    options: Any = field(default_factory=list)  # [{value: "", label: ""}] or callable
    default: List[str] = field(default_factory=list)
    variant: str = "pills"  # "pills" (default) or "dropdown" for checkbox dropdown style


@dataclass
class OrderableListField(FieldBase):
    # Options can be a list or a callable that returns a list (for lazy evaluation)
    # Each option: {id, label, description?, disabledReason?, isLocked?}
    options: Any = field(default_factory=list)
    # Default value: [{id, enabled}, ...] in priority order
    default: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class ActionButton:
    key: str                              # Action identifier
    label: str                            # Button text
    description: str = ""                 # Help text
    style: str = "default"                # "default", "primary", "danger"
    callback: Optional[Callable[[], Dict[str, Any]]] = None  # Returns {"success": bool, "message": str}
    disabled: bool = False                # Whether button is disabled/greyed out
    disabled_reason: str = ""             # Explanation shown when disabled
    show_when: Optional[Dict[str, Any]] = None  # Conditional visibility: {"field": "key", "value": "expected"} or {"field": "key", "notEmpty": True}
    disabled_when: Optional[Dict[str, Any]] = None  # Conditional disable: {"field": "key", "value": "expected", "reason": "..."}

    def get_field_type(self) -> str:
        return "ActionButton"


@dataclass
class HeadingField:
    """
    Display-only heading with title and description.

    Used to add section titles and descriptive text to settings pages.
    Not an input field - purely for display.
    """
    key: str                              # Unique identifier
    title: str                            # Heading title
    description: str = ""                 # Description text (supports markdown-style links)
    link_url: str = ""                    # Optional URL for a link
    link_text: str = ""                   # Text for the link (defaults to URL if not provided)
    show_when: Optional[Dict[str, Any]] = None  # Conditional visibility: {"field": "key", "value": "expected"} or {"field": "key", "notEmpty": True}
    universal_only: bool = False          # Only show in Universal search mode (hide in Direct mode)

    def get_field_type(self) -> str:
        return "HeadingField"


# Type alias for all field types
SettingsField = Union[TextField, PasswordField, NumberField, CheckboxField, SelectField, MultiSelectField, OrderableListField, ActionButton, HeadingField]


@dataclass
class SettingsTab:
    """A tab/section in the settings UI."""
    name: str                             # Internal name (used in URLs)
    display_name: str                     # Display name in UI
    fields: List[SettingsField] = field(default_factory=list)
    icon: Optional[str] = None            # Icon name for UI
    order: int = 100                      # Sort order (lower = earlier)
    group: Optional[str] = None           # Group name this tab belongs to


@dataclass
class SettingsGroup:
    """A collapsible group of settings tabs in the UI."""
    name: str                             # Internal name
    display_name: str                     # Display name in UI
    icon: Optional[str] = None            # Icon name for UI
    order: int = 100                      # Sort order (lower = earlier)


_SETTINGS_REGISTRY: Dict[str, SettingsTab] = {}
_GROUPS_REGISTRY: Dict[str, SettingsGroup] = {}
_ON_SAVE_HANDLERS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}
_REGISTRY_LOCK = Lock()


def register_group(
    name: str,
    display_name: str,
    icon: Optional[str] = None,
    order: int = 100
) -> None:
    with _REGISTRY_LOCK:
        group = SettingsGroup(
            name=name,
            display_name=display_name,
            icon=icon,
            order=order,
        )
        _GROUPS_REGISTRY[name] = group
        logger.debug(f"Registered settings group: {name}")


def register_settings(
    name: str,
    display_name: str,
    icon: Optional[str] = None,
    order: int = 100,
    group: Optional[str] = None
):
    def decorator(func: Callable[[], List[SettingsField]]):
        with _REGISTRY_LOCK:
            fields = func()
            tab = SettingsTab(
                name=name,
                display_name=display_name,
                fields=fields,
                icon=icon,
                order=order,
                group=group,
            )
            _SETTINGS_REGISTRY[name] = tab
            logger.debug(f"Registered settings tab: {name} ({len(fields)} fields)" +
                        (f" in group {group}" if group else ""))
        return func
    return decorator


def register_on_save(
    tab_name: str,
    handler: Callable[[Dict[str, Any]], Dict[str, Any]]
) -> None:
    with _REGISTRY_LOCK:
        _ON_SAVE_HANDLERS[tab_name] = handler
        logger.debug(f"Registered on_save handler for tab: {tab_name}")


def get_on_save_handler(tab_name: str) -> Optional[Callable[[Dict[str, Any]], Dict[str, Any]]]:
    """Get the on_save handler for a settings tab, if any."""
    return _ON_SAVE_HANDLERS.get(tab_name)


def get_settings_tab(name: str) -> Optional[SettingsTab]:
    """Get a specific settings tab by name."""
    return _SETTINGS_REGISTRY.get(name)


def get_all_settings_tabs() -> List[SettingsTab]:
    """Get all registered settings tabs, sorted by order."""
    return sorted(_SETTINGS_REGISTRY.values(), key=lambda t: (t.order, t.name))


def list_registered_settings() -> List[str]:
    """List all registered settings tab names."""
    return list(_SETTINGS_REGISTRY.keys())


def _get_config_dir() -> Path:
    """Get the config directory path."""
    from cwa_book_downloader.config.env import CONFIG_DIR
    return Path(CONFIG_DIR)


def _get_config_file_path(tab_name: str) -> Path:
    """Get the config file path for a settings tab."""
    config_dir = _get_config_dir()
    # Core settings tabs share the main settings.json file
    if tab_name in ("general", "search_mode"):
        return config_dir / "settings.json"
    else:
        plugins_dir = config_dir / "plugins"
        return plugins_dir / f"{tab_name}.json"


def _ensure_config_dir(tab_name: str) -> None:
    """Ensure the config directory exists."""
    config_path = _get_config_file_path(tab_name)
    config_path.parent.mkdir(parents=True, exist_ok=True)


def load_config_file(tab_name: str) -> Dict[str, Any]:
    config_path = _get_config_file_path(tab_name)

    if not config_path.exists():
        return {}

    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in config file {config_path}: {e}")
        return {}


def save_config_file(tab_name: str, values: Dict[str, Any]) -> bool:
    try:
        _ensure_config_dir(tab_name)
        config_path = _get_config_file_path(tab_name)

        # Load existing config and merge
        existing = load_config_file(tab_name)
        existing.update(values)

        with open(config_path, 'w') as f:
            json.dump(existing, f, indent=2)

        logger.info(f"Saved settings to {config_path}")
        return True
    except Exception as e:
        logger.error(f"Error saving config file for {tab_name}: {e}")
        return False


def sync_env_to_config() -> None:
    for tab in get_all_settings_tabs():
        values_to_sync = {}

        for field in tab.fields:
            # Skip non-value fields
            if isinstance(field, (ActionButton, HeadingField)):
                continue

            # Skip fields that don't support ENV vars
            if not getattr(field, 'env_supported', True):
                continue

            # Check if ENV var is set
            env_var_name = field.get_env_var_name()
            env_value = os.environ.get(env_var_name)

            if env_value is not None:
                # Parse the ENV value to the appropriate type
                parsed_value = _parse_env_value(env_value, field)
                values_to_sync[field.key] = parsed_value

        # Save synced values to config file (merge with existing)
        if values_to_sync:
            save_config_file(tab.name, values_to_sync)
            logger.debug(f"Synced {len(values_to_sync)} ENV values to {tab.name} config: {list(values_to_sync.keys())}")

    # Migrate legacy settings to new format
    migrate_legacy_settings()


def migrate_legacy_settings() -> None:
    """Migrate legacy settings to new unified file destination format.

    Maps old settings to new:
    - PROCESSING_MODE + USE_BOOK_TITLE -> FILE_ORGANIZATION
    - INGEST_DIR / LIBRARY_PATH -> DESTINATION
    - LIBRARY_TEMPLATE -> TEMPLATE
    - USE_CONTENT_TYPE_DIRECTORIES -> AA_CONTENT_TYPE_ROUTING
    - INGEST_DIR_* -> AA_CONTENT_TYPE_DIR_*
    - TORRENT_HARDLINK -> HARDLINK_TORRENTS / HARDLINK_TORRENTS_AUDIOBOOK
    """
    # Load existing downloads config
    downloads_config = load_config_file("downloads")
    source_config = load_config_file("download_sources")

    # Skip migration if already using new settings
    if "FILE_ORGANIZATION" in downloads_config or "DESTINATION" in downloads_config:
        return

    migrated_downloads = {}
    migrated_sources = {}

    # === BOOKS MIGRATION ===
    old_mode = downloads_config.get("PROCESSING_MODE", "ingest")
    old_ingest_dir = downloads_config.get("INGEST_DIR", "/cwa-book-ingest")
    old_library_path = downloads_config.get("LIBRARY_PATH", "")
    old_use_book_title = downloads_config.get("USE_BOOK_TITLE", True)
    old_library_template = downloads_config.get("LIBRARY_TEMPLATE", "{Author}/{Title}")

    # Map PROCESSING_MODE + USE_BOOK_TITLE -> FILE_ORGANIZATION
    if old_mode == "library":
        migrated_downloads["FILE_ORGANIZATION"] = "organize"
        migrated_downloads["DESTINATION"] = old_library_path or "/books"
        migrated_downloads["TEMPLATE"] = old_library_template
    else:
        if old_use_book_title:
            migrated_downloads["FILE_ORGANIZATION"] = "rename"
            migrated_downloads["TEMPLATE"] = "{Author} - {Title} ({Year})"
        else:
            migrated_downloads["FILE_ORGANIZATION"] = "none"
        migrated_downloads["DESTINATION"] = old_ingest_dir

    # === AUDIOBOOKS MIGRATION ===
    old_mode_ab = downloads_config.get("PROCESSING_MODE_AUDIOBOOK", "ingest")
    old_ingest_dir_ab = downloads_config.get("INGEST_DIR_AUDIOBOOK", "")
    old_library_path_ab = downloads_config.get("LIBRARY_PATH_AUDIOBOOK", "")
    old_library_template_ab = downloads_config.get("LIBRARY_TEMPLATE_AUDIOBOOK", "{Author}/{Title}")

    if old_mode_ab == "library":
        migrated_downloads["FILE_ORGANIZATION_AUDIOBOOK"] = "organize"
        migrated_downloads["DESTINATION_AUDIOBOOK"] = old_library_path_ab or ""
        migrated_downloads["TEMPLATE_AUDIOBOOK"] = old_library_template_ab
    else:
        migrated_downloads["FILE_ORGANIZATION_AUDIOBOOK"] = "rename"
        migrated_downloads["TEMPLATE_AUDIOBOOK"] = "{Author} - {Title}"
        if old_ingest_dir_ab:
            migrated_downloads["DESTINATION_AUDIOBOOK"] = old_ingest_dir_ab

    # === HARDLINK MIGRATION ===
    old_torrent_hardlink = downloads_config.get("TORRENT_HARDLINK")
    if old_torrent_hardlink is not None:
        # Books default to False (ingest folder use case)
        # Audiobooks default to True (library folder use case)
        # But if explicitly set, apply to both
        migrated_downloads["HARDLINK_TORRENTS"] = old_torrent_hardlink
        migrated_downloads["HARDLINK_TORRENTS_AUDIOBOOK"] = old_torrent_hardlink

    # === CONTENT-TYPE ROUTING MIGRATION ===
    old_use_content_type = downloads_config.get("USE_CONTENT_TYPE_DIRECTORIES", False)
    if old_use_content_type:
        migrated_sources["AA_CONTENT_TYPE_ROUTING"] = True

        # Map old keys to new keys
        content_type_mapping = {
            "INGEST_DIR_BOOK_FICTION": "AA_CONTENT_TYPE_DIR_FICTION",
            "INGEST_DIR_BOOK_NON_FICTION": "AA_CONTENT_TYPE_DIR_NON_FICTION",
            "INGEST_DIR_BOOK_UNKNOWN": "AA_CONTENT_TYPE_DIR_UNKNOWN",
            "INGEST_DIR_MAGAZINE": "AA_CONTENT_TYPE_DIR_MAGAZINE",
            "INGEST_DIR_COMIC_BOOK": "AA_CONTENT_TYPE_DIR_COMIC",
            "INGEST_DIR_STANDARDS_DOCUMENT": "AA_CONTENT_TYPE_DIR_STANDARDS",
            "INGEST_DIR_MUSICAL_SCORE": "AA_CONTENT_TYPE_DIR_MUSICAL_SCORE",
            "INGEST_DIR_OTHER": "AA_CONTENT_TYPE_DIR_OTHER",
        }

        for old_key, new_key in content_type_mapping.items():
            old_value = downloads_config.get(old_key, "")
            if old_value:
                migrated_sources[new_key] = old_value

    # Save migrated settings
    if migrated_downloads:
        save_config_file("downloads", migrated_downloads)
        logger.info(f"Migrated download settings: {list(migrated_downloads.keys())}")

    if migrated_sources:
        save_config_file("download_sources", migrated_sources)
        logger.info(f"Migrated content-type routing settings: {list(migrated_sources.keys())}")


def get_setting_value(field: SettingsField, tab_name: str) -> Any:
    if isinstance(field, (ActionButton, HeadingField)):
        return None  # Actions and headings don't have values

    # 1. Check environment variable (if supported for this field)
    if field.env_supported:
        env_var_name = field.get_env_var_name()
        env_value = os.environ.get(env_var_name)
        if env_value is not None:
            return _parse_env_value(env_value, field)

    # 2. Check config file
    config = load_config_file(tab_name)
    if field.key in config:
        return config[field.key]

    # 3. Return default
    return field.default


def _parse_env_value(value: str, field: SettingsField) -> Any:
    """Parse an environment variable value to the appropriate type."""
    if isinstance(field, CheckboxField):
        return value.lower() in ('true', '1', 'yes', 'on')
    elif isinstance(field, NumberField):
        try:
            if '.' in value:
                return float(value)
            return int(value)
        except ValueError:
            return field.default
    elif isinstance(field, MultiSelectField):
        return [v.strip() for v in value.split(',') if v.strip()]
    elif isinstance(field, OrderableListField):
        # Parse JSON array: [{"id": "...", "enabled": true}, ...]
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON for {field.key}, using default")
            return field.default
    else:
        return value


def is_value_from_env(field: SettingsField) -> bool:
    """Check if a field's value comes from an environment variable."""
    if isinstance(field, (ActionButton, HeadingField)):
        return False
    # UI-only settings never come from ENV (env_supported=False)
    # Default to True for backwards compatibility
    env_supported = getattr(field, 'env_supported', True)
    if env_supported is False:
        return False
    env_var_name = field.get_env_var_name()
    return env_var_name in os.environ


def serialize_field(field: SettingsField, tab_name: str, include_value: bool = True) -> Dict[str, Any]:
    """
    Serialize a field for API response.

    Args:
        field: The settings field.
        tab_name: The settings tab name.
        include_value: Whether to include the current value.

    Returns:
        Dict representation of the field.
    """
    # HeadingField has a different structure - handle separately
    if isinstance(field, HeadingField):
        result = {
            "key": field.key,
            "type": field.get_field_type(),
            "title": field.title,
            "description": field.description,
        }
        if field.link_url:
            result["linkUrl"] = field.link_url
            result["linkText"] = field.link_text or field.link_url
        if field.show_when:
            result["showWhen"] = field.show_when
        if field.universal_only:
            result["universalOnly"] = True
        return result

    result = {
        "key": field.key,
        "label": field.label,
        "type": field.get_field_type(),
        "description": getattr(field, 'description', ''),
        "required": getattr(field, 'required', False),
        "disabled": getattr(field, 'disabled', False),
        "disabledReason": getattr(field, 'disabled_reason', ''),
        "requiresRestart": getattr(field, 'requires_restart', False),
    }

    # Add conditional visibility if specified
    show_when = getattr(field, 'show_when', None)
    if show_when:
        result["showWhen"] = show_when

    # Add conditional disable if specified
    disabled_when = getattr(field, 'disabled_when', None)
    if disabled_when:
        result["disabledWhen"] = disabled_when

    # Add universal_only flag if set
    universal_only = getattr(field, 'universal_only', False)
    if universal_only:
        result["universalOnly"] = True

    # Add type-specific properties
    if isinstance(field, TextField):
        result["placeholder"] = field.placeholder
        if field.max_length:
            result["maxLength"] = field.max_length
    elif isinstance(field, PasswordField):
        result["placeholder"] = field.placeholder
    elif isinstance(field, NumberField):
        result["min"] = field.min_value
        result["max"] = field.max_value
        result["step"] = field.step
    elif isinstance(field, SelectField):
        # Support callable options for lazy evaluation (avoids circular imports)
        options = field.options() if callable(field.options) else field.options
        result["options"] = options
    elif isinstance(field, MultiSelectField):
        # Support callable options for lazy evaluation (avoids circular imports)
        options = field.options() if callable(field.options) else field.options
        result["options"] = options
        result["variant"] = field.variant
    elif isinstance(field, OrderableListField):
        # Support callable options for lazy evaluation (avoids circular imports)
        options = field.options() if callable(field.options) else field.options
        result["options"] = options
    elif isinstance(field, ActionButton):
        result["style"] = field.style
        result["description"] = field.description

    if include_value and not isinstance(field, (ActionButton, HeadingField)):
        value = get_setting_value(field, tab_name)
        result["value"] = value if value is not None else ""
        result["fromEnv"] = is_value_from_env(field)

    return result


def serialize_tab(tab: SettingsTab, include_values: bool = True) -> Dict[str, Any]:
    """Serialize a settings tab for API response."""
    return {
        "name": tab.name,
        "displayName": tab.display_name,
        "icon": tab.icon,
        "order": tab.order,
        "group": tab.group,
        "fields": [serialize_field(f, tab.name, include_values) for f in tab.fields],
    }


def serialize_group(group: SettingsGroup) -> Dict[str, Any]:
    """Serialize a settings group for API response."""
    return {
        "name": group.name,
        "displayName": group.display_name,
        "icon": group.icon,
        "order": group.order,
    }


def get_all_groups() -> List[SettingsGroup]:
    """Get all registered settings groups, sorted by order."""
    return sorted(_GROUPS_REGISTRY.values(), key=lambda g: (g.order, g.name))


def serialize_all_settings(include_values: bool = True) -> Dict[str, Any]:
    """Serialize all settings for API response."""
    tabs = get_all_settings_tabs()
    groups = get_all_groups()
    return {
        "tabs": [serialize_tab(t, include_values) for t in tabs],
        "groups": [serialize_group(g) for g in groups],
    }


def execute_action(tab_name: str, action_key: str, current_values: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Execute an action button's callback.

    Args:
        tab_name: The settings tab name.
        action_key: The action key to execute.
        current_values: Optional dict of current form values (unsaved).
                       Passed to callbacks that accept it.

    Returns:
        Dict with "success" (bool) and "message" (str).
    """
    import inspect

    tab = get_settings_tab(tab_name)
    if not tab:
        return {"success": False, "message": f"Unknown settings tab: {tab_name}"}

    for field in tab.fields:
        if isinstance(field, ActionButton) and field.key == action_key:
            if field.callback:
                try:
                    # Check if callback accepts current_values parameter
                    sig = inspect.signature(field.callback)
                    if 'current_values' in sig.parameters:
                        return field.callback(current_values=current_values or {})
                    else:
                        return field.callback()
                except Exception as e:
                    logger.error(f"Action {action_key} failed: {e}")
                    return {"success": False, "message": str(e)}
            else:
                return {"success": False, "message": "Action has no callback defined"}

    return {"success": False, "message": f"Unknown action: {action_key}"}


def _sync_metadata_provider_selection() -> None:
    """
    Sync the METADATA_PROVIDER setting based on enabled providers.

    Called after saving metadata provider settings to auto-select
    the first enabled provider if the current selection is invalid.
    """
    try:
        from cwa_book_downloader.metadata_providers import sync_metadata_provider_selection
        sync_metadata_provider_selection()
    except ImportError:
        pass  # Metadata providers module not available


def _apply_dns_settings(config) -> None:
    """
    Apply DNS settings changes to the network module.

    This ensures DNS changes take effect immediately without requiring
    a container restart.
    """
    try:
        from cwa_book_downloader.download import network

        provider = config.get("CUSTOM_DNS", "auto")
        use_doh = config.get("USE_DOH", False)
        manual_servers = None

        if provider == "manual":
            manual_dns = config.get("CUSTOM_DNS_MANUAL", "")
            if manual_dns:
                # Parse comma-separated server list
                manual_servers = [s.strip() for s in manual_dns.split(",") if s.strip()]

        network.set_dns_provider(provider, manual_servers, use_doh=use_doh)
    except ImportError:
        pass  # Network module not available
    except Exception as e:
        logger.warning(f"Failed to apply DNS settings: {e}")


def update_settings(tab_name: str, values: Dict[str, Any]) -> Dict[str, Any]:
    tab = get_settings_tab(tab_name)
    if not tab:
        return {"success": False, "message": f"Unknown settings tab: {tab_name}", "updated": [], "requiresRestart": False}

    # Build a map of field keys to fields (exclude non-value fields)
    field_map = {f.key: f for f in tab.fields if not isinstance(f, (ActionButton, HeadingField))}

    # Filter out values that are set via env vars or unknown
    values_to_save = {}
    skipped_env = []
    skipped_unknown = []
    restart_required_keys = []

    for key, value in values.items():
        if key not in field_map:
            skipped_unknown.append(key)
            continue

        field = field_map[key]
        if is_value_from_env(field):
            skipped_env.append(key)
            continue

        # Handle password fields - only update if a new value is provided
        if isinstance(field, PasswordField) and not value:
            continue

        values_to_save[key] = value

        # Track if this field requires restart
        if getattr(field, 'requires_restart', False):
            restart_required_keys.append(key)

    if not values_to_save:
        message = "No settings to update"
        if skipped_env:
            message += f". Skipped (set via env): {', '.join(skipped_env)}"
        return {"success": True, "message": message, "updated": [], "requiresRestart": False}

    # Call on_save handler if registered (for custom validation/transformation)
    on_save_handler = get_on_save_handler(tab_name)
    if on_save_handler:
        try:
            result = on_save_handler(values_to_save.copy())
            if result.get("error"):
                return {
                    "success": False,
                    "message": result.get("message", "Validation failed"),
                    "updated": [],
                    "requiresRestart": False
                }
            # Use the transformed values
            values_to_save = result.get("values", values_to_save)
        except Exception as e:
            logger.error(f"on_save handler for {tab_name} failed: {e}")
            return {
                "success": False,
                "message": f"Save handler error: {str(e)}",
                "updated": [],
                "requiresRestart": False
            }

    # Save to config file
    if save_config_file(tab_name, values_to_save):
        # Refresh the config singleton so live settings take effect immediately
        try:
            from cwa_book_downloader.core.config import config
            config.refresh()
        except ImportError:
            pass  # Config module not yet available during initial setup

        # Apply DNS settings changes live (network tab)
        dns_keys = {"CUSTOM_DNS", "CUSTOM_DNS_MANUAL", "USE_DOH"}
        if tab_name == "network" and dns_keys.intersection(values_to_save.keys()):
            _apply_dns_settings(config)

        # Sync metadata provider selection when a provider's enabled state changes
        tab = get_settings_tab(tab_name)
        if tab and tab.group == "metadata_providers":
            _sync_metadata_provider_selection()

        message = f"Updated {len(values_to_save)} setting(s)"
        if skipped_env:
            message += f". Skipped (set via env): {', '.join(skipped_env)}"

        requires_restart = len(restart_required_keys) > 0
        return {
            "success": True,
            "message": message,
            "updated": list(values_to_save.keys()),
            "requiresRestart": requires_restart,
            "restartRequiredFor": restart_required_keys,
        }
    else:
        return {"success": False, "message": "Failed to save settings", "updated": [], "requiresRestart": False}
