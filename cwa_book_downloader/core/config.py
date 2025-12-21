"""Configuration singleton with ENV > config file > default resolution."""

from threading import Lock
from typing import Any, Dict, Optional

# Import lazily to avoid circular imports
_registry_module = None
_env_module = None


def _get_registry():
    """Lazy import of settings registry to avoid circular imports."""
    global _registry_module
    if _registry_module is None:
        from cwa_book_downloader.core import settings_registry
        _registry_module = settings_registry
    return _registry_module


def _get_env():
    """Lazy import of env module for fallback values."""
    global _env_module
    if _env_module is None:
        from cwa_book_downloader.config import env
        _env_module = env
    return _env_module


class Config:
    """
    Dynamic configuration singleton that provides live settings access.

    Settings are resolved with priority: ENV var > config file > default.
    Values are cached for performance and can be refreshed when settings change.
    """

    _instance: Optional['Config'] = None
    _lock = Lock()

    def __new__(cls) -> 'Config':
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._cache: Dict[str, Any] = {}
        self._field_map: Dict[str, tuple] = {}  # key -> (field, tab_name)
        self._cache_lock = Lock()
        self._initialized = True
        self._loaded = False

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
        # Ensure all plugin settings are registered before loading
        # This handles cases where config is accessed before plugins are imported
        try:
            import cwa_book_downloader.release_sources  # noqa: F401
            import cwa_book_downloader.metadata_providers  # noqa: F401
        except ImportError:
            pass

        registry = _get_registry()

        # On first load, sync ENV values to config files
        # This ensures ENV values persist even if ENV vars are later removed
        if not hasattr(self, '_env_synced'):
            registry.sync_env_to_config()
            self._env_synced = True

        # Build field map from all registered tabs
        self._field_map.clear()
        self._cache.clear()

        for tab in registry.get_all_settings_tabs():
            for field in tab.fields:
                # Skip action buttons and headings - they don't have values
                if isinstance(field, (registry.ActionButton, registry.HeadingField)):
                    continue

                key = field.key
                self._field_map[key] = (field, tab.name)

                # Load current value
                value = registry.get_setting_value(field, tab.name)
                self._cache[key] = value

        self._loaded = True

    def refresh(self) -> None:
        """
        Refresh all cached settings from config files.

        Call this after settings are updated via the UI to ensure
        the config singleton reflects the new values.
        """
        with self._cache_lock:
            self._loaded = False
            self._load_settings()

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value by key.

        Args:
            key: The setting key (e.g., 'MAX_RETRY')
            default: Default value if setting not found

        Returns:
            The setting value, or default if not found
        """
        self._ensure_loaded()
        return self._cache.get(key, default)

    def __getattr__(self, name: str) -> Any:
        """
        Allow attribute-style access to settings.

        Example: config.MAX_RETRY instead of config.get('MAX_RETRY')
        """
        # Avoid recursion for internal attributes
        if name.startswith('_'):
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

        self._ensure_loaded()

        if name in self._cache:
            return self._cache[name]

        # Fallback to env module for settings not in registry
        # This ensures backward compatibility during migration
        env = _get_env()
        if hasattr(env, name):
            return getattr(env, name)

        raise AttributeError(f"Setting '{name}' not found in config or env")

    def is_from_env(self, key: str) -> bool:
        """
        Check if a setting's value comes from an environment variable.

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

    def get_all(self) -> Dict[str, Any]:
        """
        Get all cached settings as a dictionary.

        Returns:
            Dict of all setting keys to their current values
        """
        self._ensure_loaded()
        return dict(self._cache)


# Global singleton instance
config = Config()
