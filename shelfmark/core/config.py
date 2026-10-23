"""Configuration singleton with ENV > config file > default resolution."""

import os
import sqlite3
import time
from importlib import import_module
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    from types import ModuleType

    from shelfmark.core.user_db import UserDB

# Import lazily to avoid circular imports
_registry_module = None
_env_module = None
_user_db_module = None

_SETTINGS_REFRESH_COOLDOWN_SECONDS = 0.05


def _get_registry() -> ModuleType:
    """Lazy import of settings registry to avoid circular imports."""
    global _registry_module
    if _registry_module is None:
        from shelfmark.core import settings_registry

        _registry_module = settings_registry
    return _registry_module


def _get_env() -> ModuleType:
    """Lazy import of env module for fallback values."""
    global _env_module
    if _env_module is None:
        from shelfmark.config import env

        _env_module = env
    return _env_module


def _get_user_db_module() -> type[UserDB]:
    """Lazy import of user DB module to avoid optional dependency loops."""
    global _user_db_module
    if _user_db_module is None:
        from shelfmark.core.user_db import UserDB

        _user_db_module = UserDB
    return _user_db_module


class Config:
    """Dynamic configuration singleton that provides live settings access.

    Settings are resolved with priority: ENV var > config file > default.
    Values are cached for performance and can be refreshed when settings change.
    """

    _instance: Self | None = None
    _lock = Lock()

    def __new__(cls) -> Self:
        """Return the shared configuration singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        instance = cls._instance
        if instance is None:
            msg = "Config singleton failed to initialize"
            raise RuntimeError(msg)
        return instance

    def __init__(self) -> None:
        """Initialize caches and backing stores for the singleton."""
        if self._initialized:
            return
        self._cache: dict[str, Any] = {}
        self._field_map: dict[str, tuple] = {}  # key -> (field, tab_name)
        self._cache_lock = Lock()
        self._user_settings_cache: dict[int, dict[str, Any]] = {}
        self._user_settings_cache_lock = Lock()
        self._user_db = None
        self._user_db_load_attempted = False
        self._initialized = True
        self._loaded = False
        self._last_refresh_time: float = 0.0

    def _ensure_loaded(self) -> None:
        """Ensure settings are loaded from the registry."""
        if self._loaded:
            return
        with self._cache_lock:
            if self._loaded:
                return
            self._load_settings()

    def _load_settings(self) -> None:
        """Load all settings from the registry."""
        # Ensure all settings modules are imported before loading
        # This handles cases where config is accessed before settings are registered
        try:
            import_module("shelfmark.config.notifications_settings")
            import_module("shelfmark.config.security")
            import_module("shelfmark.config.settings")
            import_module("shelfmark.config.users_settings")
            import_module("shelfmark.metadata_providers")
            import_module("shelfmark.release_sources")
        except ImportError:
            pass

        registry = _get_registry()

        # On first load, sync ENV values to config files
        # This ensures ENV values persist even if ENV vars are later removed
        if not hasattr(self, "_env_synced"):
            registry.sync_env_to_config()
            self._env_synced = True

        # Build field map from all registered tabs
        self._field_map.clear()
        self._cache.clear()

        with registry.config_files_read_once():
            for key, (field, tab_name) in registry.get_settings_field_map().items():
                self._field_map[key] = (field, tab_name)
                self._cache[key] = registry.get_setting_value(field, tab_name)

        self._loaded = True

    def refresh(self, *, force: bool = False) -> None:
        """Refresh all cached settings from config files.

        Call this after settings are updated via the UI to ensure
        the config singleton reflects the new values.

        Multiple calls within a short window (50 ms) are coalesced to
        avoid redundant disk I/O when several helpers each call refresh()
        during the same request.  Pass ``force=True`` to bypass the guard
        (e.g. after a settings write).
        """
        now = time.monotonic()
        if not force and (now - self._last_refresh_time) < _SETTINGS_REFRESH_COOLDOWN_SECONDS:
            return

        with self._cache_lock:
            self._loaded = False
            self._load_settings()
        with self._user_settings_cache_lock:
            self._user_settings_cache.clear()
        self._user_db = None
        self._user_db_load_attempted = False
        self._last_refresh_time = time.monotonic()

    def _get_user_db(self) -> UserDB | None:
        """Get or initialize a UserDB handle if available."""
        if self._user_db is not None:
            return self._user_db
        if self._user_db_load_attempted:
            return None

        self._user_db_load_attempted = True
        try:
            user_db_cls = _get_user_db_module()
            db_path = str(Path(os.environ.get("CONFIG_DIR", "/config")) / "users.db")
            user_db = user_db_cls(db_path)
            user_db.initialize()
        except ImportError, OSError, sqlite3.Error:
            # Multi-user support is optional; fall back to global config when unavailable.
            return None
        else:
            self._user_db = user_db
            return self._user_db

    def _get_user_settings(self, user_id: int) -> dict[str, Any]:
        """Get cached per-user settings from user DB."""
        with self._user_settings_cache_lock:
            if user_id in self._user_settings_cache:
                return self._user_settings_cache[user_id]

        user_db = self._get_user_db()
        if user_db is None:
            return {}

        try:
            settings = user_db.get_user_settings(user_id)
        except sqlite3.OperationalError, OSError, ValueError, TypeError:
            return {}

        if not isinstance(settings, dict):
            settings = {}

        with self._user_settings_cache_lock:
            self._user_settings_cache[user_id] = settings
        return settings

    def _get_user_override(self, user_id: int, key: str) -> object:
        """Get a user override for a specific key."""
        user_settings = self._get_user_settings(user_id)
        return user_settings.get(key)

    def get(self, key: str, default: object = None, user_id: int | None = None) -> object:
        """Get a setting value by key.

        Args:
            key: The setting key (e.g., 'MAX_RETRY')
            default: Default value if setting not found
            user_id: Optional DB user ID for per-user setting overrides

        Returns:
            The setting value, or default if not found

        """
        return self.get_for_user(key, default, user_id=user_id)[0]

    def get_for_user(
        self, key: str, default: object = None, user_id: int | None = None
    ) -> tuple[object, bool]:
        """Like ``get``, plus whether the value is the user's own override.

        Both come from a single read, so a settings save in between cannot make them
        disagree. False means the deployment-wide value set by an admin or ENV.
        """
        self._ensure_loaded()

        if key in self._field_map:
            field, _ = self._field_map[key]
            registry = _get_registry()

            # Deployment-level ENV values always win.
            if field.env_supported and registry.is_value_from_env(field):
                return self._cache.get(key, default), False

            # User overrides are only available for explicitly overridable fields.
            if user_id is not None and getattr(field, "user_overridable", False):
                user_value = self._get_user_override(user_id, key)
                if user_value is not None:
                    return user_value, True

        return self._cache.get(key, default), False

    def __getattr__(self, name: str) -> object:
        """Allow attribute-style access to settings.

        Example: config.MAX_RETRY instead of config.get('MAX_RETRY')
        """
        # Avoid recursion for internal attributes
        if name.startswith("_"):
            msg = f"'{type(self).__name__}' object has no attribute '{name}'"
            raise AttributeError(msg)

        self._ensure_loaded()

        if name in self._cache:
            return self._cache[name]

        # Fallback to env module for settings not in registry
        # This ensures backward compatibility during migration
        env = _get_env()
        if hasattr(env, name):
            return getattr(env, name)

        msg = f"Setting '{name}' not found in config or env"
        raise AttributeError(msg)

    def is_from_env(self, key: str) -> bool:
        """Check if a setting's value comes from an environment variable.

        Args:
            key: The setting key

        Returns:
            True if the value is set via ENV var, False otherwise

        """
        self._ensure_loaded()

        if key not in self._field_map:
            return False

        field, _ = self._field_map[key]
        registry = _get_registry()
        return registry.is_value_from_env(field)

    def get_all(self) -> dict[str, Any]:
        """Get all cached settings as a dictionary.

        Returns:
            Dict of all setting keys to their current values

        """
        self._ensure_loaded()
        return dict(self._cache)


# Global singleton instance
config = Config()
