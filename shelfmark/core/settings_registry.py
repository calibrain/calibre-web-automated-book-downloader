"""Plugin settings registry with config file persistence."""

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from werkzeug.utils import secure_filename

from shelfmark.config.env import normalize_log_level
from shelfmark.core.logger import setup_logger
from shelfmark.core.request_helpers import coerce_bool, normalize_optional_text

logger = setup_logger(__name__)
_SETTINGS_LIVE_APPLY_ERRORS = (OSError, RuntimeError, TypeError, ValueError)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from shelfmark.core.config import Config


@dataclass
class FieldBase:
    """Base class for all settings fields."""

    key: str  # Environment variable / config key
    label: str  # Display label in UI
    description: str = ""  # Help text
    default: Any = None  # Default value if not set
    required: bool = False  # Whether field must have a value
    env_var: str | None = None  # Override env var name (defaults to key)
    env_supported: bool = True  # Whether this setting can be set via ENV var (False = UI-only)
    user_overridable: bool = False  # Whether admins can set per-user overrides for this field
    disabled: bool = False  # Whether field is disabled/greyed out
    disabled_reason: str = ""  # Explanation shown when disabled
    show_when: dict[str, Any] | list[dict[str, Any]] | None = (
        None  # Conditional visibility: {"field": "key", "value": "expected"} or list of conditions
    )
    disabled_when: dict[str, Any] | None = (
        None  # Conditional disable: {"field": "key", "value": "expected", "reason": "..."}
    )
    requires_restart: bool = False  # Whether changing this setting requires a container restart
    universal_only: bool = False  # Only show in Universal search mode (hide in Direct mode)
    hidden_in_ui: bool = False  # Keep field in schema/save path but hide default renderer

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
    max_length: int | None = None


@dataclass
class PasswordField(FieldBase):
    """Password input (masked in UI, not returned in API responses)."""

    placeholder: str = ""


@dataclass
class NumberField(FieldBase):
    """Numeric input."""

    min_value: float | None = None
    max_value: float | None = None
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
    options: object = field(default_factory=list)  # [{value: "", label: ""}] or callable
    filter_by_field: str | None = None  # Field key whose value filters options via childOf property


@dataclass
class MultiSelectField(FieldBase):
    """Multiple-choice selection."""

    # Options can be a list or a callable that returns a list (for lazy evaluation)
    options: object = field(default_factory=list)  # [{value: "", label: ""}] or callable
    default: list[str] = field(default_factory=list)
    variant: str = "pills"  # "pills" (default) or "dropdown" for checkbox dropdown style


@dataclass
class TagListField(FieldBase):
    """Editable list of free-form string values (tag/chip input)."""

    placeholder: str = ""
    default: list[str] = field(default_factory=list)
    normalize_urls: bool = True


@dataclass
class OrderableListField(FieldBase):
    """Settings field for ordered, toggleable option lists."""

    # Options can be a list or a callable that returns a list (for lazy evaluation)
    # Each option: {id, label, description?, disabledReason?, isLocked?, section?, isPinned?}
    # - isLocked: toggle is disabled (can't enable/disable)
    # - isPinned: can't be reordered (but toggle may still work if not also isLocked)
    options: object = field(default_factory=list)
    # Default value: [{id, enabled}, ...] in priority order
    default: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class TableField(FieldBase):
    """Editable table of structured rows."""

    # Column definitions: [{key, label, type, placeholder?, options?, defaultValue?}, ...]
    columns: object = field(default_factory=list)  # list or callable

    # Value format: list of objects
    default: list[dict[str, Any]] = field(default_factory=list)

    add_label: str = "Add"
    empty_message: str = ""


@dataclass
class CustomComponentField:
    """Render a custom frontend component inside settings content."""

    key: str
    component: str  # Frontend component registry key
    label: str = ""
    description: str = ""
    bind_keys: list[str] = field(default_factory=list)  # Related value keys this component edits
    value_fields: list[Any] = field(default_factory=list)  # Backing value schema for this component
    wrap_in_field_wrapper: bool = False  # Whether to render with standard FieldWrapper layout
    disabled: bool = False
    disabled_reason: str = ""
    show_when: dict[str, Any] | list[dict[str, Any]] | None = None
    universal_only: bool = False

    def get_field_type(self) -> str:
        """Return the serialized field type for this custom component."""
        return "CustomComponentField"

    def get_bind_keys(self) -> list[str]:
        """Return the config keys this custom component reads or writes."""
        if self.bind_keys:
            return self.bind_keys
        return [f.key for f in self.value_fields if getattr(f, "key", None)]


@dataclass
class ActionButton:
    """Definition for a custom action button in the settings UI."""

    key: str  # Action identifier
    label: str  # Button text
    description: str = ""  # Help text
    style: str = "default"  # "default", "primary", "danger"
    callback: Callable[..., dict[str, Any]] | None = (
        None  # Returns {"success": bool, "message": str}
    )
    disabled: bool = False  # Whether button is disabled/greyed out
    disabled_reason: str = ""  # Explanation shown when disabled
    show_when: dict[str, Any] | list[dict[str, Any]] | None = (
        None  # Conditional visibility: {"field": "key", "value": "expected"} or list of conditions
    )
    disabled_when: dict[str, Any] | None = (
        None  # Conditional disable: {"field": "key", "value": "expected", "reason": "..."}
    )
    universal_only: bool = False  # Only show in Universal search mode (hide in Direct mode)

    def get_field_type(self) -> str:
        """Return the serialized field type for this action button."""
        return "ActionButton"


@dataclass
class HeadingField:
    """Display-only heading with title and description.

    Used to add section titles and descriptive text to settings pages.
    Not an input field - purely for display.
    """

    key: str  # Unique identifier
    title: str  # Heading title
    description: str = ""  # Description text (supports markdown-style links)
    description_by_auth_mode: dict[str, str] | None = (
        None  # Optional auth-mode specific description map
    )
    link_url: str = ""  # Optional URL for a link
    link_text: str = ""  # Text for the link (defaults to URL if not provided)
    show_when: dict[str, Any] | list[dict[str, Any]] | None = (
        None  # Conditional visibility: {"field": "key", "value": "expected"} or list of conditions
    )
    universal_only: bool = False  # Only show in Universal search mode (hide in Direct mode)

    def get_field_type(self) -> str:
        """Return the serialized field type for this heading field."""
        return "HeadingField"


# Type alias for all field types
ValueField = (
    TextField
    | PasswordField
    | NumberField
    | CheckboxField
    | SelectField
    | MultiSelectField
    | TagListField
    | OrderableListField
    | TableField
)

SettingsField = ValueField | CustomComponentField | ActionButton | HeadingField


@dataclass
class SettingsTab:
    """A tab/section in the settings UI."""

    name: str  # Internal name (used in URLs)
    display_name: str  # Display name in UI
    fields: list[SettingsField] = field(default_factory=list)
    icon: str | None = None  # Icon name for UI
    order: int = 100  # Sort order (lower = earlier)
    group: str | None = None  # Group name this tab belongs to


@dataclass
class SettingsGroup:
    """A collapsible group of settings tabs in the UI."""

    name: str  # Internal name
    display_name: str  # Display name in UI
    icon: str | None = None  # Icon name for UI
    order: int = 100  # Sort order (lower = earlier)


_SETTINGS_REGISTRY: dict[str, SettingsTab] = {}
_GROUPS_REGISTRY: dict[str, SettingsGroup] = {}
_ON_SAVE_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}
_REGISTRY_LOCK = Lock()


def register_group(name: str, display_name: str, icon: str | None = None, order: int = 100) -> None:
    """Register a settings group used to organize tabs in the UI."""
    with _REGISTRY_LOCK:
        group = SettingsGroup(
            name=name,
            display_name=display_name,
            icon=icon,
            order=order,
        )
        _GROUPS_REGISTRY[name] = group
        logger.debug("Registered settings group: %s", name)


def register_settings(
    name: str,
    display_name: str,
    icon: str | None = None,
    order: int = 100,
    group: str | None = None,
) -> Callable[[Callable[[], list[SettingsField]]], Callable[[], list[SettingsField]]]:
    """Register a settings tab and its field factory."""

    def decorator(func: Callable[[], list[SettingsField]]) -> Callable[[], list[SettingsField]]:
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
            logger.debug(
                "Registered settings tab: %s (%s fields)%s",
                name,
                len(fields),
                f" in group {group}" if group else "",
            )
        return func

    return decorator


def register_on_save(tab_name: str, handler: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Register an on-save hook for a settings tab."""
    with _REGISTRY_LOCK:
        _ON_SAVE_HANDLERS[tab_name] = handler
        logger.debug("Registered on_save handler for tab: %s", tab_name)


def get_on_save_handler(
    tab_name: str,
) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
    """Get the on_save handler for a settings tab, if any."""
    return _ON_SAVE_HANDLERS.get(tab_name)


def get_settings_tab(name: str) -> SettingsTab | None:
    """Get a specific settings tab by name."""
    return _SETTINGS_REGISTRY.get(name)


def get_all_settings_tabs() -> list[SettingsTab]:
    """Get all registered settings tabs, sorted by order."""
    return sorted(_SETTINGS_REGISTRY.values(), key=lambda t: (t.order, t.name))


def iter_value_fields(tab: SettingsTab) -> Iterator[FieldBase]:
    """Yield value-bearing fields for a tab."""
    for settings_field in tab.fields:
        if isinstance(settings_field, CustomComponentField):
            for value_field in settings_field.value_fields:
                if isinstance(value_field, FieldBase):
                    yield value_field
            continue
        if isinstance(settings_field, FieldBase):
            yield settings_field


def get_settings_field_map(
    tab_name: str | None = None,
) -> dict[str, tuple[FieldBase, str]]:
    """Return key -> (field, tab_name) map for value-bearing settings fields."""
    tabs: list[SettingsTab]
    if tab_name:
        tab = get_settings_tab(tab_name)
        if not tab:
            return {}
        tabs = [tab]
    else:
        tabs = get_all_settings_tabs()

    field_map: dict[str, tuple[FieldBase, str]] = {}
    for tab in tabs:
        for settings_field in iter_value_fields(tab):
            field_map[settings_field.key] = (settings_field, tab.name)
    return field_map


def get_user_overridable_fields(
    tab_name: str | None = None,
) -> dict[str, tuple[FieldBase, str]]:
    """Return key -> (field, tab_name) map for fields marked user_overridable."""
    field_map = get_settings_field_map(tab_name=tab_name)
    return {
        key: (field, tab)
        for key, (field, tab) in field_map.items()
        if getattr(field, "user_overridable", False)
    }


def list_registered_settings() -> list[str]:
    """List all registered settings tab names."""
    return list(_SETTINGS_REGISTRY.keys())


def _get_config_dir() -> Path:
    """Get the config directory path."""
    from shelfmark.config.env import CONFIG_DIR

    return Path(CONFIG_DIR)


def _get_config_file_path(tab_name: str) -> Path:
    """Get the config file path for a settings tab."""
    config_dir = _get_config_dir()
    # Core settings tabs share the main settings.json file
    if tab_name in ("general", "search_mode"):
        return config_dir / "settings.json"

    # Plugin config file names should match their tab names exactly after
    # filename sanitization, so request input cannot escape the plugins folder.
    safe_name = secure_filename(tab_name)
    if not safe_name or safe_name != tab_name:
        msg = f"Invalid tab name: {tab_name}"
        raise ValueError(msg)

    plugins_dir = (config_dir / "plugins").resolve(strict=False)
    config_path = (plugins_dir / f"{safe_name}.json").resolve(strict=False)
    if not config_path.is_relative_to(plugins_dir):
        msg = f"Invalid tab name: {tab_name}"
        raise ValueError(msg)

    return config_path


def _ensure_config_dir(tab_name: str) -> None:
    """Ensure the config directory exists."""
    config_path = _get_config_file_path(tab_name)
    config_path.parent.mkdir(parents=True, exist_ok=True)


# Set while the whole settings cache is being rebuilt. Every field of a tab used to
# re-read and re-parse that tab's file, so a rebuild parsed each file once per field.
_config_file_memo: ContextVar[dict[str, dict[str, Any]] | None] = ContextVar(
    "config_file_memo", default=None
)


@contextmanager
def config_files_read_once() -> Iterator[None]:
    """Read each tab's config file at most once inside the block.

    Only for a read-only pass such as rebuilding the settings cache; a save inside the
    block would not be seen by reads after it.
    """
    token = _config_file_memo.set({})
    try:
        yield
    finally:
        _config_file_memo.reset(token)


def load_config_file(tab_name: str) -> dict[str, Any]:
    """Load a settings tab config file, returning an empty dict on failure."""
    memo = _config_file_memo.get()
    if memo is None:
        return _read_config_file(tab_name)
    config_path = _get_config_file_path(tab_name)
    key = str(config_path)  # several tabs share settings.json
    if key not in memo:
        memo[key] = _read_config_file(tab_name)
    return memo[key]


def _read_config_file(tab_name: str) -> dict[str, Any]:
    config_path = _get_config_file_path(tab_name)

    if not config_path.exists():
        return {}

    try:
        with config_path.open() as f:
            return json.load(f)
    except json.JSONDecodeError:
        logger.exception("Invalid JSON in config file %s", config_path)
        return {}


def save_config_file(tab_name: str, values: dict[str, Any]) -> bool:
    """Merge and save persisted settings values for a tab."""
    try:
        _ensure_config_dir(tab_name)
        config_path = _get_config_file_path(tab_name)

        # Load existing config and merge
        existing = load_config_file(tab_name)
        existing.update(values)

        with config_path.open("w") as f:
            json.dump(existing, f, indent=2)

        logger.info("Saved settings to %s", config_path)
    except Exception:
        logger.exception("Error saving config file for %s", tab_name)
        return False
    else:
        return True


def initialize_default_configs() -> bool:
    """Initialize config files with default values on first startup.

    Creates config files for all settings tabs that don't have one yet,
    populating them with field default values. This ensures config files
    exist from first startup rather than only being created on explicit save.

    Returns:
        True if initialization succeeded or was skipped (already initialized),
        False if there was an error accessing the config directory.

    """
    try:
        config_dir = _get_config_dir()

        # Check if config directory exists and is writable
        if not config_dir.exists():
            logger.warning("Config directory does not exist: %s", config_dir)
            return False

        # Test writability
        test_file = config_dir / ".write_test"
        try:
            test_file.touch()
            test_file.unlink()
        except (OSError, PermissionError) as e:
            logger.warning("Config directory is not writable: %s - %s", config_dir, e)
            return False

        initialized_tabs = []

        for tab in get_all_settings_tabs():
            config_path = _get_config_file_path(tab.name)

            # Skip if config file already exists
            if config_path.exists():
                continue

            # Collect default values for all fields
            defaults = {}
            for field in iter_value_fields(tab):
                # Only include fields that have a non-None default
                if field.default is not None:
                    defaults[field.key] = field.default

            # Create config file with defaults if we have any
            if defaults:
                _ensure_config_dir(tab.name)
                try:
                    with config_path.open("w") as f:
                        json.dump(defaults, f, indent=2)
                    initialized_tabs.append(tab.name)
                except Exception:
                    logger.exception("Failed to initialize config for %s", tab.name)

        if initialized_tabs:
            logger.info("Initialized default configs for: %s", initialized_tabs)

    except Exception:
        logger.exception("Error during config initialization")
        return False
    else:
        return True


def sync_env_to_config() -> None:
    """Sync supported environment-backed settings into config files."""
    config_dir = _get_config_dir()
    plugins_dir = config_dir / "plugins"
    had_existing_search_page_title = "SEARCH_PAGE_TITLE" in load_config_file("general")
    had_existing_install_state = (
        (config_dir / "settings.json").exists()
        or (config_dir / "users.db").exists()
        or (plugins_dir.exists() and any(plugins_dir.glob("*.json")))
    )

    # Initialize default configs first (for fresh installs)
    initialize_default_configs()

    for tab in get_all_settings_tabs():
        values_to_sync = {}

        for settings_field in iter_value_fields(tab):
            # Skip fields that don't support ENV vars
            if not getattr(settings_field, "env_supported", True):
                continue

            has_env_value, parsed_value = _get_env_value_for_field(settings_field)
            if has_env_value:
                values_to_sync[settings_field.key] = parsed_value

        # Save synced values to config file (merge with existing)
        if values_to_sync:
            save_config_file(tab.name, values_to_sync)
            logger.debug(
                "Synced %s ENV values to %s config: %s",
                len(values_to_sync),
                tab.name,
                list(values_to_sync.keys()),
            )

    migrate_legacy_settings()
    migrate_download_to_browser_settings()
    migrate_mirror_settings()
    migrate_direct_download_upgrade(existing_install=had_existing_install_state)
    migrate_search_page_title(
        existing_install=had_existing_install_state,
        had_existing_value=had_existing_search_page_title,
    )


def migrate_direct_download_upgrade(*, existing_install: bool) -> None:
    """Preserve the direct-download enabled flag for existing installs only."""
    if not existing_install:
        return

    download_sources_config = load_config_file("download_sources")

    download_updates: dict[str, Any] = {}

    if "DIRECT_DOWNLOAD_ENABLED" not in download_sources_config:
        download_updates["DIRECT_DOWNLOAD_ENABLED"] = True

    if download_updates:
        save_config_file("download_sources", download_updates)


def migrate_search_page_title(*, existing_install: bool, had_existing_value: bool) -> None:
    """Keep the legacy homepage search title for existing installs only."""
    if not existing_install or had_existing_value or os.getenv("SEARCH_PAGE_TITLE") is not None:
        return

    save_config_file("general", {"SEARCH_PAGE_TITLE": "Book Search & Download"})


def migrate_mirror_settings() -> None:
    """Normalize canonical mirror-list settings and migrate legacy config forward safely."""
    from shelfmark.core.utils import normalize_http_url

    def _normalize_list(values: list[str]) -> list[str]:
        out: list[str] = []
        for item in values:
            if str(item).strip().lower() == "auto":
                continue
            norm = normalize_http_url(str(item), default_scheme="https")
            if norm and norm not in out:
                out.append(norm)
        return out

    mirrors_config = load_config_file("mirrors")
    mirror_updates: dict[str, Any] = {}

    def _queue_update(key: str, normalized: list[str]) -> None:
        current = mirrors_config.get(key)
        if current != normalized:
            mirror_updates[key] = normalized

    def _resolve_canonical_list(
        canonical_key: str,
        *,
        legacy_list_key: str | None = None,
        legacy_primary_key: str | None = None,
        legacy_additional_key: str | None = None,
    ) -> None:
        raw_list = mirrors_config.get(canonical_key)

        if isinstance(raw_list, list):
            _queue_update(canonical_key, _normalize_list([str(v) for v in raw_list]))
            return

        if isinstance(raw_list, str) and raw_list.strip():
            parts = [p.strip() for p in raw_list.split(",") if p.strip()]
            _queue_update(canonical_key, _normalize_list(parts))
            return

        combined: list[str] = []
        if legacy_primary_key:
            raw_primary = mirrors_config.get(legacy_primary_key, "")
            if isinstance(raw_primary, str) and raw_primary.strip():
                combined.append(raw_primary.strip())
        if legacy_list_key:
            raw_legacy_list = mirrors_config.get(legacy_list_key, "")
            if isinstance(raw_legacy_list, str) and raw_legacy_list.strip():
                combined.extend([p.strip() for p in raw_legacy_list.split(",") if p.strip()])
            elif isinstance(raw_legacy_list, list):
                combined.extend([str(p).strip() for p in raw_legacy_list if str(p).strip()])
        if legacy_additional_key:
            raw_additional = mirrors_config.get(legacy_additional_key, "")
            if isinstance(raw_additional, str) and raw_additional.strip():
                combined.extend([p.strip() for p in raw_additional.split(",") if p.strip()])
            elif isinstance(raw_additional, list):
                combined.extend([str(p).strip() for p in raw_additional if str(p).strip()])

        normalized = _normalize_list(combined)
        if normalized:
            _queue_update(canonical_key, normalized)

    _resolve_canonical_list(
        "AA_MIRROR_URLS",
        legacy_list_key="AA_ADDITIONAL_URLS",
    )
    _resolve_canonical_list(
        "LIBGEN_MIRROR_URLS",
        legacy_list_key="LIBGEN_ADDITIONAL_URLS",
    )
    _resolve_canonical_list(
        "ZLIB_MIRROR_URLS",
        legacy_primary_key="ZLIB_PRIMARY_URL",
        legacy_additional_key="ZLIB_ADDITIONAL_URLS",
    )
    _resolve_canonical_list(
        "WELIB_MIRROR_URLS",
        legacy_primary_key="WELIB_PRIMARY_URL",
        legacy_additional_key="WELIB_ADDITIONAL_URLS",
    )

    if mirror_updates:
        save_config_file("mirrors", mirror_updates)


def migrate_legacy_settings() -> None:
    """Migrate legacy settings to new unified file destination format.

    Maps stable legacy settings to the current download model:
    - INGEST_DIR -> DESTINATION
    - USE_BOOK_TITLE -> FILE_ORGANIZATION
    - USE_CONTENT_TYPE_DIRECTORIES -> AA_CONTENT_TYPE_ROUTING
    - INGEST_DIR_* -> AA_CONTENT_TYPE_DIR_*
    - TORRENT_HARDLINK -> HARDLINK_TORRENTS / HARDLINK_TORRENTS_AUDIOBOOK

    Intentionally ignores the short-lived pre-1.0 library-mode settings
    (`PROCESSING_MODE`, `LIBRARY_PATH`, `LIBRARY_TEMPLATE`, etc.), which were
    replaced before the first stable release shipped.
    """
    # Load existing downloads config
    downloads_config = load_config_file("downloads")

    # Skip migration if already using new settings
    if "FILE_ORGANIZATION" in downloads_config or "DESTINATION" in downloads_config:
        return

    # Skip migration if no legacy settings exist (fresh install)
    legacy_keys = {
        "INGEST_DIR",
        "USE_BOOK_TITLE",
        "INGEST_DIR_AUDIOBOOK",
        "TORRENT_HARDLINK",
        "USE_CONTENT_TYPE_DIRECTORIES",
        "INGEST_DIR_BOOK_FICTION",
        "INGEST_DIR_BOOK_NON_FICTION",
        "INGEST_DIR_BOOK_UNKNOWN",
        "INGEST_DIR_MAGAZINE",
        "INGEST_DIR_COMIC_BOOK",
        "INGEST_DIR_STANDARDS_DOCUMENT",
        "INGEST_DIR_MUSICAL_SCORE",
        "INGEST_DIR_OTHER",
    }
    if not any(key in downloads_config for key in legacy_keys):
        return

    migrated_downloads = {}
    migrated_sources = {}

    old_ingest_dir = downloads_config.get("INGEST_DIR", "/cwa-book-ingest")
    old_use_book_title = downloads_config.get("USE_BOOK_TITLE", True)

    migrated_downloads["DESTINATION"] = old_ingest_dir
    if old_use_book_title:
        migrated_downloads["FILE_ORGANIZATION"] = "rename"
    else:
        migrated_downloads["FILE_ORGANIZATION"] = "none"

    # === HARDLINK MIGRATION ===
    old_torrent_hardlink = downloads_config.get("TORRENT_HARDLINK")
    if old_torrent_hardlink is not None:
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
        logger.info("Migrated download settings: %s", list(migrated_downloads.keys()))

    if migrated_sources:
        save_config_file("download_sources", migrated_sources)
        logger.info("Migrated content-type routing settings: %s", list(migrated_sources.keys()))


def migrate_download_to_browser_settings() -> None:
    """Migrate the legacy download-to-browser toggle to content-type selection."""
    downloads_config = load_config_file("downloads")
    legacy_key = "DOWNLOAD_TO_BROWSER"
    new_key = "DOWNLOAD_TO_BROWSER_CONTENT_TYPES"
    config_path = _get_config_file_path("downloads")

    legacy_value: object = None
    legacy_present = False

    if legacy_key in downloads_config:
        legacy_value = downloads_config.get(legacy_key)
        legacy_present = True
    elif (
        new_key not in downloads_config
        and os.environ.get(new_key) is None
        and legacy_key in os.environ
    ):
        legacy_value = os.environ.get(legacy_key)
        legacy_present = True

    if not legacy_present and legacy_key not in downloads_config:
        return

    updated_downloads = dict(downloads_config)
    changed = False

    if new_key not in updated_downloads and legacy_present:
        enabled = False
        if isinstance(legacy_value, bool):
            enabled = legacy_value
        elif isinstance(legacy_value, str):
            enabled = legacy_value.strip().lower() in {"true", "1", "yes", "on"}
        else:
            enabled = bool(legacy_value)

        updated_downloads[new_key] = ["book", "audiobook"] if enabled else []
        changed = True

    if legacy_key in updated_downloads:
        updated_downloads.pop(legacy_key, None)
        changed = True

    if not changed:
        return

    try:
        _ensure_config_dir("downloads")
        with config_path.open("w") as f:
            json.dump(updated_downloads, f, indent=2)
        logger.info("Migrated download-to-browser setting to content-type selection")
    except Exception:
        logger.exception("Failed to migrate download-to-browser settings")


def get_setting_value(field: FieldBase, tab_name: str) -> object:
    """Resolve the effective value for a settings field."""
    # 1. Check environment variable (if supported for this field)
    has_env_value, parsed_env_value = _get_env_value_for_field(field)
    if has_env_value:
        return parsed_env_value

    # 2. Check config file
    config = load_config_file(tab_name)
    if field.key in config:
        return config[field.key]

    # 3. Return default
    return field.default


def _parse_env_value(value: str, field: FieldBase) -> object:
    """Parse an environment variable value to the appropriate type."""
    if isinstance(field, CheckboxField):
        return value.lower() in ("true", "1", "yes", "on")
    if isinstance(field, NumberField):
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return field.default
    elif isinstance(field, (MultiSelectField, TagListField)):
        return [v.strip() for v in value.split(",") if v.strip()]
    elif isinstance(field, OrderableListField):
        # Parse JSON array: [{"id": "...", "enabled": true}, ...]
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            logger.warning("Invalid JSON for %s, using default", field.key)
            return field.default
    elif isinstance(field, TableField):
        # Parse JSON array: [{"col": "value"}, ...]
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else field.default
        except json.JSONDecodeError:
            logger.warning("Invalid JSON for %s, using default", field.key)
            return field.default
    else:
        return value


def _normalize_mirror_env_urls(values: list[str]) -> list[str]:
    """Normalize mirror URL env values to stable https URLs without duplicates."""
    from shelfmark.core.utils import normalize_http_url

    normalized: list[str] = []
    for raw_value in values:
        stripped = raw_value.strip()
        if not stripped:
            continue
        normalized_url = normalize_http_url(stripped, default_scheme="https")
        if normalized_url and normalized_url not in normalized:
            normalized.append(normalized_url)
    return normalized


def _get_env_value_for_field(field: FieldBase) -> tuple[bool, object | None]:
    """Return parsed env-backed value for a field, including legacy mirror aliases."""
    if not field.env_supported:
        return False, None

    env_var_name = field.get_env_var_name()
    env_value = os.environ.get(env_var_name)
    if env_value is not None:
        parsed = _parse_env_value(env_value, field)
        if field.key in {
            "AA_MIRROR_URLS",
            "LIBGEN_MIRROR_URLS",
            "ZLIB_MIRROR_URLS",
            "WELIB_MIRROR_URLS",
            "OCEANOFPDF_MIRROR_URLS",
        } and isinstance(parsed, list):
            parsed = _normalize_mirror_env_urls(parsed)
        if field.key == "LOG_LEVEL" and isinstance(parsed, str):
            # LOG_LEVEL is commonly set lowercase; the field options are uppercase.
            parsed = normalize_log_level(parsed)
        return True, parsed

    if field.key == "AA_MIRROR_URLS":
        legacy_additional = os.environ.get("AA_ADDITIONAL_URLS")
        if legacy_additional is not None:
            return True, _normalize_mirror_env_urls(legacy_additional.split(","))

    if field.key == "LIBGEN_MIRROR_URLS":
        legacy_additional = os.environ.get("LIBGEN_ADDITIONAL_URLS")
        if legacy_additional is not None:
            return True, _normalize_mirror_env_urls(legacy_additional.split(","))

    if field.key == "ZLIB_MIRROR_URLS":
        legacy_primary = os.environ.get("ZLIB_PRIMARY_URL")
        legacy_additional = os.environ.get("ZLIB_ADDITIONAL_URLS")
        if legacy_primary is not None or legacy_additional is not None:
            combined = []
            if legacy_primary is not None:
                combined.append(legacy_primary)
            if legacy_additional is not None:
                combined.extend(legacy_additional.split(","))
            return True, _normalize_mirror_env_urls(combined)

    if field.key == "WELIB_MIRROR_URLS":
        legacy_primary = os.environ.get("WELIB_PRIMARY_URL")
        legacy_additional = os.environ.get("WELIB_ADDITIONAL_URLS")
        if legacy_primary is not None or legacy_additional is not None:
            combined = []
            if legacy_primary is not None:
                combined.append(legacy_primary)
            if legacy_additional is not None:
                combined.extend(legacy_additional.split(","))
            return True, _normalize_mirror_env_urls(combined)

    return False, None


def is_value_from_env(field: FieldBase) -> bool:
    """Check if a field's value comes from an environment variable."""
    has_env_value, _parsed = _get_env_value_for_field(field)
    return has_env_value


def serialize_field(
    field: SettingsField,
    tab_name: str,
    *,
    include_value: bool = True,
) -> dict[str, Any]:
    """Serialize a field for API response.

    Args:
        field: The settings field.
        tab_name: The settings tab name.
        include_value: Whether to include the current value.

    Returns:
        Dict representation of the field.

    """
    # CustomComponentField has a custom structure - handle separately
    if isinstance(field, CustomComponentField):
        component_result: dict[str, Any] = {
            "key": field.key,
            "label": field.label,
            "type": field.get_field_type(),
            "description": field.description,
            "component": field.component,
            "bindKeys": field.get_bind_keys(),
            "wrapInFieldWrapper": field.wrap_in_field_wrapper,
            "disabled": field.disabled,
            "disabledReason": field.disabled_reason,
        }
        if field.value_fields:
            bound_fields = []
            for value_field in field.value_fields:
                serialized_bound_field = serialize_field(
                    value_field,
                    tab_name,
                    include_value=include_value,
                )
                serialized_bound_field["hiddenInUi"] = True
                bound_fields.append(serialized_bound_field)
            component_result["boundFields"] = bound_fields
        if field.show_when:
            component_result["showWhen"] = field.show_when
        if field.universal_only:
            component_result["universalOnly"] = True
        return component_result

    # HeadingField has a different structure - handle separately
    if isinstance(field, HeadingField):
        heading_result: dict[str, Any] = {
            "key": field.key,
            "type": field.get_field_type(),
            "title": field.title,
            "description": field.description,
        }
        if field.description_by_auth_mode:
            heading_result["descriptionByAuthMode"] = field.description_by_auth_mode
        if field.link_url:
            heading_result["linkUrl"] = field.link_url
            heading_result["linkText"] = field.link_text or field.link_url
        if field.show_when:
            heading_result["showWhen"] = field.show_when
        if field.universal_only:
            heading_result["universalOnly"] = True
        return heading_result

    result: dict[str, Any] = {
        "key": field.key,
        "label": field.label,
        "type": field.get_field_type(),
        "description": getattr(field, "description", ""),
        "required": getattr(field, "required", False),
        "disabled": getattr(field, "disabled", False),
        "disabledReason": getattr(field, "disabled_reason", ""),
        "requiresRestart": getattr(field, "requires_restart", False),
        "userOverridable": getattr(field, "user_overridable", False),
        "hiddenInUi": getattr(field, "hidden_in_ui", False),
    }

    # Add optional properties if set
    if getattr(field, "show_when", None):
        result["showWhen"] = field.show_when
    if getattr(field, "disabled_when", None):
        result["disabledWhen"] = field.disabled_when
    if getattr(field, "universal_only", False):
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
        if field.default is not None:
            result["default"] = field.default
        if field.filter_by_field:
            result["filterByField"] = field.filter_by_field
    elif isinstance(field, MultiSelectField):
        # Support callable options for lazy evaluation (avoids circular imports)
        options = field.options() if callable(field.options) else field.options
        result["options"] = options
        result["variant"] = field.variant
    elif isinstance(field, TagListField):
        result["placeholder"] = field.placeholder
        result["normalizeUrls"] = field.normalize_urls
    elif isinstance(field, OrderableListField):
        # Support callable options for lazy evaluation (avoids circular imports)
        options = field.options() if callable(field.options) else field.options
        result["options"] = options
    elif isinstance(field, TableField):
        columns = field.columns() if callable(field.columns) else field.columns
        result["columns"] = columns
        result["addLabel"] = field.add_label
        result["emptyMessage"] = field.empty_message
    elif isinstance(field, ActionButton):
        result["style"] = field.style
        result["description"] = field.description

    if include_value and not isinstance(field, (ActionButton, HeadingField, CustomComponentField)):
        value = get_setting_value(field, tab_name)

        # Ensure select values are serialized as strings so the frontend can
        # reliably match against string option values.
        if isinstance(field, SelectField) and value is not None:
            value = str(value)
        elif isinstance(field, MultiSelectField):
            if value is None:
                value = []
            elif isinstance(value, list):
                value = [str(v) for v in value]
            elif isinstance(value, str):
                # Support legacy/manual configs where MultiSelect values were saved
                # as comma-separated strings.
                value = [v.strip() for v in value.split(",") if v.strip()]
            else:
                value = []
        elif isinstance(field, TagListField):
            if value is None:
                value = []
            elif isinstance(value, list):
                value = [str(v) for v in value]
            elif isinstance(value, str):
                # Support legacy/manual configs where lists were saved as comma-separated strings.
                value = [v.strip() for v in value.split(",") if v.strip()]
            else:
                value = []
        elif isinstance(field, TableField) and (value is None or not isinstance(value, list)):
            value = []

        result["value"] = value if value is not None else ""
        result["fromEnv"] = is_value_from_env(field)

    return result


def serialize_tab(
    tab: SettingsTab,
    *,
    include_values: bool = True,
) -> dict[str, Any]:
    """Serialize a settings tab for API response."""
    return {
        "name": tab.name,
        "displayName": tab.display_name,
        "icon": tab.icon,
        "order": tab.order,
        "group": tab.group,
        "fields": [serialize_field(f, tab.name, include_value=include_values) for f in tab.fields],
    }


def serialize_group(group: SettingsGroup) -> dict[str, Any]:
    """Serialize a settings group for API response."""
    return {
        "name": group.name,
        "displayName": group.display_name,
        "icon": group.icon,
        "order": group.order,
    }


def get_all_groups() -> list[SettingsGroup]:
    """Get all registered settings groups, sorted by order."""
    return sorted(_GROUPS_REGISTRY.values(), key=lambda g: (g.order, g.name))


def serialize_all_settings(*, include_values: bool = True) -> dict[str, Any]:
    """Serialize all settings for API response."""
    tabs = get_all_settings_tabs()
    groups = get_all_groups()
    return {
        "tabs": [serialize_tab(t, include_values=include_values) for t in tabs],
        "groups": [serialize_group(g) for g in groups],
    }


def execute_action(
    tab_name: str, action_key: str, current_values: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Execute an action button's callback.

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

    for settings_field in tab.fields:
        if isinstance(settings_field, ActionButton) and settings_field.key == action_key:
            if settings_field.callback:
                try:
                    # Check if callback accepts current_values parameter
                    sig = inspect.signature(settings_field.callback)
                    if "current_values" in sig.parameters:
                        return settings_field.callback(current_values=current_values or {})
                    return settings_field.callback()
                except Exception as e:
                    logger.exception("Action %s failed", action_key)
                    return {"success": False, "message": str(e)}
            else:
                return {"success": False, "message": "Action has no callback defined"}

    return {"success": False, "message": f"Unknown action: {action_key}"}


def _sync_metadata_provider_selection() -> None:
    """Sync the METADATA_PROVIDER setting based on enabled providers.

    Called after saving metadata provider settings to auto-select
    the first enabled provider if the current selection is invalid.
    """
    try:
        from shelfmark.metadata_providers import sync_metadata_provider_selection

        sync_metadata_provider_selection()
    except ImportError:
        pass  # Metadata providers module not available


def _apply_dns_settings(config: Config) -> None:
    """Apply DNS settings changes to the network module.

    This ensures DNS changes take effect immediately without requiring
    a container restart.
    """
    try:
        from shelfmark.download import network

        provider = normalize_optional_text(config.get("CUSTOM_DNS", "auto")) or "auto"
        use_doh = coerce_bool(config.get("USE_DOH", False), default=False)
        manual_servers = None

        if provider == "manual":
            manual_dns = normalize_optional_text(config.get("CUSTOM_DNS_MANUAL", ""))
            if manual_dns:
                # Parse comma-separated server list
                manual_servers = [s.strip() for s in manual_dns.split(",") if s.strip()]

        network.set_dns_provider(provider, manual_servers, use_doh=use_doh)
    except ImportError:
        pass  # Network module not available
    except _SETTINGS_LIVE_APPLY_ERRORS as e:
        logger.warning("Failed to apply DNS settings: %s", e)


def _apply_aa_mirror_settings(config: Config) -> None:
    """Apply AA mirror settings changes to the network module.

    This ensures AA_BASE_URL / AA_MIRROR_URLS changes take effect immediately
    without requiring a container restart.
    """
    try:
        from shelfmark.download import network

        # Reload AA mirror list and configured base URL from refreshed config.
        network.init_aa(force=True)
    except ImportError:
        pass  # Network module not available
    except _SETTINGS_LIVE_APPLY_ERRORS as e:
        logger.warning("Failed to apply AA mirror settings: %s", e)


def update_settings(tab_name: str, values: dict[str, Any]) -> dict[str, Any]:
    """Validate, persist, and post-process updates for a settings tab."""
    tab = get_settings_tab(tab_name)
    if not tab:
        return {
            "success": False,
            "message": f"Unknown settings tab: {tab_name}",
            "updated": [],
            "requiresRestart": False,
        }

    # Build a map of field keys to fields (exclude non-value fields)
    field_map = {
        key: field for key, (field, _) in get_settings_field_map(tab_name=tab_name).items()
    }

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
        if getattr(field, "requires_restart", False):
            restart_required_keys.append(key)

    if not values_to_save:
        message = "No settings to update"
        if skipped_env:
            message += f". Skipped (set via env): {', '.join(skipped_env)}"
        return {
            "success": True,
            "message": message,
            "updated": [],
            "requiresRestart": False,
        }

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
                    "requiresRestart": False,
                }
            # Use the transformed values
            values_to_save = result.get("values", values_to_save)
        except Exception as e:
            logger.exception("on_save handler for %s failed", tab_name)
            return {
                "success": False,
                "message": f"Save handler error: {e!s}",
                "updated": [],
                "requiresRestart": False,
            }

    # Save to config file
    if save_config_file(tab_name, values_to_save):
        # Refresh the config singleton so live settings take effect immediately
        config_obj = None
        try:
            from shelfmark.core.config import config as config_obj

            config_obj.refresh()
        except ImportError:
            config_obj = None  # Config module not yet available during initial setup

        # Apply DNS settings changes live (network tab)
        dns_keys = {"CUSTOM_DNS", "CUSTOM_DNS_MANUAL", "USE_DOH"}
        if (
            config_obj is not None
            and tab_name == "network"
            and dns_keys.intersection(values_to_save.keys())
        ):
            _apply_dns_settings(config_obj)

        # Apply certificate validation changes live (network tab)
        if (
            config_obj is not None
            and tab_name == "network"
            and "CERTIFICATE_VALIDATION" in values_to_save
        ):
            try:
                from shelfmark.download.network import (
                    _apply_ssl_warning_suppression,
                )

                _apply_ssl_warning_suppression()
            except _SETTINGS_LIVE_APPLY_ERRORS as e:
                logger.warning("Failed to apply certificate validation setting: %s", e)

        # Apply AA mirror settings changes live (mirrors tab)
        aa_keys = {"AA_BASE_URL", "AA_MIRROR_URLS", "AA_ADDITIONAL_URLS"}
        if (
            config_obj is not None
            and tab_name == "mirrors"
            and aa_keys.intersection(values_to_save.keys())
        ):
            _apply_aa_mirror_settings(config_obj)

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
    return {
        "success": False,
        "message": "Failed to save settings",
        "updated": [],
        "requiresRestart": False,
    }
