# Plugin Settings Integration Guide

This guide explains how to add configuration settings to plugins (Metadata Providers and Release Sources) so they appear in the Settings UI.

## Overview

The settings system uses a decorator-based registration pattern. Plugins register their settings when their module is imported, and the frontend dynamically renders the appropriate UI based on the schema provided by the backend.

**Key features:**
- Settings are defined in Python and automatically rendered in the React frontend
- Values persist across container restarts via JSON config files
- Changes take effect immediately without restart (unless marked otherwise)

## Quick Start

Add settings to your plugin in 3 steps:

```python
from cwa_book_downloader.core.settings_registry import (
    register_settings,
    TextField,
    PasswordField,
    ActionButton,
)

@register_settings(
    name="my_plugin",           # Unique identifier
    display_name="My Plugin",   # Shown in sidebar
    icon="wrench",              # Icon name
    order=100,                  # Sort order (lower = higher in list)
    group="metadata_providers"  # Optional: group in sidebar
)
def my_plugin_settings():
    return [
        PasswordField(
            key="MY_PLUGIN_API_KEY",
            label="API Key",
            description="Your API key from the provider",
            required=True,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            style="primary",
            callback=_test_connection,
        ),
    ]

def _test_connection():
    # Perform connection test
    return {"success": True, "message": "Connected successfully!"}
```

## Available Field Types

### TextField

Single-line text input for strings.

```python
TextField(
    key="MY_SETTING",           # Config key
    label="Setting Name",       # Display label
    description="Help text",    # Optional description below field
    default="",                 # Default value
    placeholder="Enter value",  # Placeholder text
    max_length=100,             # Optional max characters
    required=False,             # Is this field required?
    requires_restart=False,     # Does changing this need a restart?
    show_when=None,             # Conditional visibility (see below)
    disabled_when=None,         # Conditional disable (see below)
)
```

### PasswordField

Masked input for sensitive values (API keys, passwords). Values are never echoed back to the frontend.

```python
PasswordField(
    key="API_KEY",
    label="API Key",
    description="Your secret API key",
    placeholder="sk-...",
    required=True,
)
```

### NumberField

Numeric input with optional min/max constraints.

```python
NumberField(
    key="TIMEOUT",
    label="Timeout (seconds)",
    description="Connection timeout in seconds",
    default=30,
    min_value=5,
    max_value=300,
    step=1,                     # Increment step
    required=False,
)
```

### CheckboxField

Toggle switch for boolean values.

```python
CheckboxField(
    key="ENABLE_FEATURE",
    label="Enable Feature",
    description="Turn this feature on or off",
    default=False,
)
```

### SelectField

Dropdown for single-choice selection.

```python
SelectField(
    key="LOG_LEVEL",
    label="Log Level",
    description="Logging verbosity",
    default="info",
    options=[
        {"value": "debug", "label": "Debug"},
        {"value": "info", "label": "Info"},
        {"value": "warning", "label": "Warning"},
        {"value": "error", "label": "Error"},
    ],
)
```

### MultiSelectField

Multi-choice selection from a list of options.

```python
MultiSelectField(
    key="SUPPORTED_FORMATS",
    label="Supported Formats",
    description="Select which formats to support",
    default=["epub", "mobi"],
    options=[
        {"value": "epub", "label": "EPUB"},
        {"value": "mobi", "label": "MOBI"},
        {"value": "pdf", "label": "PDF"},
        {"value": "azw3", "label": "AZW3"},
    ],
)
```

### ActionButton

Button that executes a callback function. Does not store a value.

```python
ActionButton(
    key="test_connection",      # Unique key for the action
    label="Test Connection",    # Button text
    description="Test the API connection",
    style="primary",            # "default", "primary", or "danger"
    callback=my_callback_fn,    # Function to execute
)

def my_callback_fn():
    """Callback must return dict with 'success' and 'message' keys."""
    try:
        # Perform action
        return {"success": True, "message": "Connection successful!"}
    except Exception as e:
        return {"success": False, "message": f"Failed: {str(e)}"}
```

### HeadingField

Display-only section heading with optional link. Does not store a value.

```python
HeadingField(
    key="section_heading",      # Unique key
    title="Configuration",      # Heading text
    description="Configure the plugin settings below",
    link_url="https://example.com/docs",  # Optional link
    link_text="View Documentation",       # Link text
)
```

## Common Field Properties

All field types support these common properties:

| Property | Type | Default | Description |
|----------|------|---------|-------------|
| `key` | `str` | Required | Unique identifier for this setting |
| `label` | `str` | Required | Display label in the UI |
| `description` | `str` | `""` | Help text shown below the field |
| `default` | `Any` | `None` | Default value if not set |
| `required` | `bool` | `False` | Whether the field must have a value |
| `disabled` | `bool` | `False` | Disable the field (greyed out) |
| `disabled_reason` | `str` | `""` | Explanation shown when disabled |
| `requires_restart` | `bool` | `False` | Whether changes require container restart |
| `show_when` | `dict` | `None` | Conditional visibility (see below) |
| `disabled_when` | `dict` | `None` | Conditional disable (see below) |

## Conditional Visibility

Fields can be shown/hidden based on other field values using `show_when`:

```python
# Only show DNS servers field when custom DNS is selected
TextField(
    key="CUSTOM_DNS_SERVERS",
    label="DNS Servers",
    description="Comma-separated DNS server IPs",
    show_when={"field": "DNS_PROVIDER", "value": "manual"},
)
```

The field will only be visible when the referenced field has the specified value.

## Conditional Disable

Fields can be enabled/disabled based on other field values using `disabled_when`:

```python
# Disable timeout field when feature is disabled
NumberField(
    key="FEATURE_TIMEOUT",
    label="Timeout (seconds)",
    description="Request timeout",
    default=30,
    disabled_when={
        "field": "FEATURE_ENABLED",
        "value": False,
        "reason": "Enable the feature first"
    },
)
```

The field will be greyed out with the specified reason when the condition is met.

## Settings Groups

Register a group to organize related settings tabs in the sidebar:

```python
from cwa_book_downloader.core.settings_registry import register_group

# Register a group (do this once, usually in a central config file)
register_group(
    name="my_group",
    display_name="My Group",
    icon="folder",
    order=50,
)

# Then register settings to the group
@register_settings(
    name="plugin_a",
    display_name="Plugin A",
    icon="puzzle",
    order=51,
    group="my_group",  # Assigns to the group
)
def plugin_a_settings():
    return [...]
```

**Existing groups:**
- `direct_download` (order=20): For download-related settings
- `metadata_providers` (order=50): For metadata provider plugins

## Value Resolution Priority

Settings values are resolved in this order (highest priority first):

1. **Config File** - Stored in `CONFIG_DIR/plugins/<tab_name>.json`
2. **Field Default** - Value specified in the field definition

The `general` tab uses `CONFIG_DIR/settings.json` instead of the plugins subdirectory.

## Reading Setting Values

Use the `config` singleton to read setting values in your plugin code:

```python
from cwa_book_downloader.core.config import config

# Get a setting value with default fallback
api_key = config.get("MY_PLUGIN_API_KEY", "")
timeout = config.get("MY_PLUGIN_TIMEOUT", 30)

# Or access as attributes (raises AttributeError if not found)
api_key = config.MY_PLUGIN_API_KEY

# Check all cached settings
all_settings = config.get_all()
```

The config singleton:
- Automatically resolves values from config files with field defaults as fallback
- Caches values for performance
- Refreshes automatically when settings are updated via the UI

## Complete Example: Metadata Provider

Here's a complete example for a metadata provider plugin:

```python
# cwa_book_downloader/metadata_providers/my_provider.py

from cwa_book_downloader.metadata_providers.base import (
    MetadataProvider,
    register_provider,
)
from cwa_book_downloader.core.settings_registry import (
    register_settings,
    HeadingField,
    TextField,
    PasswordField,
    CheckboxField,
    ActionButton,
)
from cwa_book_downloader.core.config import config


def _test_connection():
    """Test API connection callback."""
    api_key = config.get("MY_PROVIDER_API_KEY", "")
    if not api_key:
        return {"success": False, "message": "API key not configured"}

    try:
        # Perform actual connection test
        # response = requests.get(...)
        return {"success": True, "message": "Connected to My Provider API"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {str(e)}"}


@register_settings(
    name="my_provider",
    display_name="My Provider",
    icon="book",
    order=53,
    group="metadata_providers",
)
def my_provider_settings():
    """Define settings for this metadata provider."""
    return [
        HeadingField(
            key="my_provider_heading",
            title="My Provider",
            description="A metadata provider for book information",
            link_url="https://myprovider.com",
            link_text="Visit My Provider",
        ),
        PasswordField(
            key="MY_PROVIDER_API_KEY",
            label="API Key",
            description="Your My Provider API key",
            placeholder="Enter your API key",
            required=True,
        ),
        CheckboxField(
            key="MY_PROVIDER_INCLUDE_COVERS",
            label="Include Cover Images",
            description="Fetch cover images when searching",
            default=True,
        ),
        TextField(
            key="MY_PROVIDER_BASE_URL",
            label="API Base URL",
            description="Override the default API endpoint",
            default="https://api.myprovider.com/v1",
            required=False,
        ),
        ActionButton(
            key="test_connection",
            label="Test Connection",
            description="Verify your API key works",
            style="primary",
            callback=_test_connection,
        ),
    ]


@register_provider("my_provider")
class MyProvider(MetadataProvider):
    """My Provider metadata implementation."""

    name = "my_provider"
    display_name = "My Provider"
    requires_auth = True

    def __init__(self, api_key: str = None):
        self.api_key = api_key or config.get("MY_PROVIDER_API_KEY", "")
        self.base_url = config.get(
            "MY_PROVIDER_BASE_URL",
            "https://api.myprovider.com/v1"
        )

    def is_available(self) -> bool:
        return bool(self.api_key)

    def search(self, query: str):
        # Implementation...
        pass

    def get_book(self, book_id: str):
        # Implementation...
        pass
```

## Complete Example: Release Source

Here's a complete example for a release source plugin:

```python
# cwa_book_downloader/release_sources/my_source.py

from cwa_book_downloader.release_sources.base import (
    ReleaseSource,
    DownloadHandler,
    register_source,
    register_handler,
)
from cwa_book_downloader.core.settings_registry import (
    register_settings,
    HeadingField,
    TextField,
    NumberField,
    CheckboxField,
    SelectField,
    ActionButton,
)
from cwa_book_downloader.core.config import config


def _test_source():
    """Test source availability callback."""
    base_url = config.get("MY_SOURCE_URL", "https://mysource.com")
    try:
        # Test connectivity
        return {"success": True, "message": f"Source available at {base_url}"}
    except Exception as e:
        return {"success": False, "message": f"Source unavailable: {str(e)}"}


@register_settings(
    name="my_source",
    display_name="My Source",
    icon="download",
    order=25,
    group="direct_download",
)
def my_source_settings():
    """Define settings for this release source."""
    return [
        HeadingField(
            key="my_source_heading",
            title="My Source Configuration",
            description="Configure the My Source download provider",
        ),
        CheckboxField(
            key="MY_SOURCE_ENABLED",
            label="Enable My Source",
            description="Include My Source in download fallback chain",
            default=True,
        ),
        TextField(
            key="MY_SOURCE_URL",
            label="Source URL",
            description="Base URL for the source",
            default="https://mysource.com",
            show_when={"field": "MY_SOURCE_ENABLED", "value": True},
        ),
        NumberField(
            key="MY_SOURCE_TIMEOUT",
            label="Timeout (seconds)",
            description="Request timeout",
            default=30,
            min_value=10,
            max_value=120,
            show_when={"field": "MY_SOURCE_ENABLED", "value": True},
        ),
        SelectField(
            key="MY_SOURCE_PRIORITY",
            label="Priority",
            description="Where in the fallback chain to try this source",
            default="normal",
            options=[
                {"value": "high", "label": "High (try first)"},
                {"value": "normal", "label": "Normal"},
                {"value": "low", "label": "Low (try last)"},
            ],
            show_when={"field": "MY_SOURCE_ENABLED", "value": True},
        ),
        ActionButton(
            key="test_source",
            label="Test Source",
            description="Check if the source is accessible",
            style="primary",
            callback=_test_source,
        ),
    ]


@register_source("my_source")
class MySource(ReleaseSource):
    """My Source release source implementation."""

    name = "my_source"
    display_name = "My Source"

    def __init__(self):
        self.enabled = config.get("MY_SOURCE_ENABLED", True)
        self.base_url = config.get("MY_SOURCE_URL", "https://mysource.com")
        self.timeout = config.get("MY_SOURCE_TIMEOUT", 30)

    def is_available(self) -> bool:
        return self.enabled

    def search(self, book):
        # Implementation...
        pass


@register_handler("my_source")
class MySourceHandler(DownloadHandler):
    """Handler for downloading from My Source."""

    name = "my_source"

    def download(self, release, output_path):
        # Implementation...
        pass
```

## Best Practices

1. **Use descriptive keys**: Keys should be uppercase and prefixed with your plugin name (e.g., `MY_PLUGIN_API_KEY`)

2. **Provide helpful descriptions**: Include enough detail in descriptions to help users understand what each setting does

3. **Set sensible defaults**: Users should be able to get started without configuring everything

4. **Use conditional visibility**: Hide advanced options behind enabling checkboxes to reduce UI clutter

5. **Include a test button**: ActionButtons that test connections help users verify their configuration

6. **Mark restart-required settings**: Use `requires_restart=True` for settings that can't be applied live

7. **Group related settings**: Use HeadingField to visually separate sections, and put plugins in appropriate groups

8. **Handle missing values gracefully**: Always provide fallbacks when reading settings in your code

## API Reference

### Backend Routes

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/settings` | Get all settings tabs, groups, and values |
| GET | `/api/settings/<tab_name>` | Get a specific settings tab |
| PUT | `/api/settings/<tab_name>` | Update settings for a tab |
| POST | `/api/settings/<tab_name>/action/<action_key>` | Execute an action button callback |

### Response Format

**GET /api/settings**
```json
{
  "groups": [
    {"name": "direct_download", "displayName": "Direct Download", "icon": "download", "order": 20}
  ],
  "tabs": [
    {
      "name": "my_plugin",
      "displayName": "My Plugin",
      "icon": "book",
      "order": 53,
      "group": "metadata_providers",
      "fields": [
        {
          "type": "password",
          "key": "MY_PLUGIN_API_KEY",
          "label": "API Key",
          "description": "Your API key",
          "hasValue": true,
          "value": "",
          "required": true,
          "disabled": false,
          "requiresRestart": false
        }
      ]
    }
  ]
}
```

**PUT /api/settings/<tab_name>**
```json
// Request
{"MY_PLUGIN_API_KEY": "new-value", "MY_PLUGIN_TIMEOUT": 60}

// Response
{
  "success": true,
  "message": "Settings updated",
  "updated": ["MY_PLUGIN_API_KEY", "MY_PLUGIN_TIMEOUT"],
  "requiresRestart": false
}
```

**POST /api/settings/<tab_name>/action/<action_key>**
```json
// Response
{
  "success": true,
  "message": "Connection successful!"
}
```
