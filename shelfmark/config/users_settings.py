"""Users settings tab registration.

This registers a 'users' tab in the settings sidebar.
The actual user management is handled by a custom frontend component
that talks to /api/admin/users endpoints.
"""

from typing import Any

from shelfmark.core.languages import normalize_language
from shelfmark.core.request_policy import (
    get_source_content_type_capabilities,
    parse_policy_mode,
    validate_policy_rules,
)
from shelfmark.core.settings_registry import (
    CheckboxField,
    CustomComponentField,
    HeadingField,
    MultiSelectField,
    NumberField,
    SelectField,
    SettingsField,
    TableField,
    register_on_save,
    register_settings,
)

_REQUEST_DEFAULT_MODE_OPTIONS = [
    {
        "value": "download",
        "label": "Download",
        "description": "Everything can be downloaded directly.",
    },
    {
        "value": "request_release",
        "label": "Request Release",
        "description": "Users must request a specific release.",
    },
    {
        "value": "request_book",
        "label": "Request Book",
        "description": "Users request a book, admin picks the release.",
    },
    {
        "value": "blocked",
        "label": "Blocked",
        "description": "No downloads or requests allowed.",
    },
]

_REQUEST_MATRIX_MODE_OPTIONS = [
    option for option in _REQUEST_DEFAULT_MODE_OPTIONS if option["value"] != "request_book"
]

_SELF_SETTINGS_SECTION_OPTIONS = [
    {
        "value": "delivery",
        "label": "Delivery Preferences",
        "description": "Show personal delivery output and destination settings.",
    },
    {
        "value": "search",
        "label": "Search Preferences",
        "description": "Show personal search mode, language, and provider settings.",
    },
    {
        "value": "notifications",
        "label": "Notifications",
        "description": "Show personal notification route settings.",
    },
]
_SELF_SETTINGS_SECTION_VALUES = {option["value"] for option in _SELF_SETTINGS_SECTION_OPTIONS}
_SELF_SETTINGS_SECTION_DEFAULTS = [option["value"] for option in _SELF_SETTINGS_SECTION_OPTIONS]
_SEARCH_MODE_VALUES = {"direct", "universal"}
_SEARCH_PREFERENCE_PROVIDER_KEYS = {
    "METADATA_PROVIDER",
    "METADATA_PROVIDER_AUDIOBOOK",
    "METADATA_PROVIDER_COMBINED",
}
_SEARCH_PREFERENCE_BOOLEAN_KEYS = {
    "SHOW_COMBINED_SELECTOR",
    "FORCE_COMBINED_SEARCH",
    "SHOW_SERIES_COLUMN",
    "SHOW_NARRATOR_COLUMN",
    "SHOW_BITRATE_COLUMN",
}
SEARCH_PREFERENCE_VALIDATABLE_KEYS = {
    "SEARCH_MODE",
    "BOOK_LANGUAGE",
    "DEFAULT_RELEASE_SOURCE",
    "DEFAULT_RELEASE_SOURCE_AUDIOBOOK",
    *_SEARCH_PREFERENCE_BOOLEAN_KEYS,
    *_SEARCH_PREFERENCE_PROVIDER_KEYS,
}

_USERS_HEADING_DESCRIPTION_BY_AUTH_MODE = {
    "builtin": (
        "Create and manage user accounts directly. Passwords are stored locally and users sign in "
        "with their username and password."
    ),
    "oidc": (
        "Users sign in through your identity provider. New accounts can be created automatically on "
        "first login when auto-provisioning is enabled, or you can pre-create users here and they\u2019ll "
        "be linked by email on first sign-in."
    ),
    "proxy": (
        "Users are authenticated by your reverse proxy. Accounts are automatically created on first "
        "sign-in. If a local user with a matching username already exists, it will be linked instead."
    ),
    "cwa": (
        "User accounts are synced from your Calibre-Web database. Users are matched by email, and new "
        "accounts are created here when new CWA users are found."
    ),
    "none": "Authentication is disabled. Anyone can access Shelfmark without signing in.",
    "unavailable": (
        "AUTH_METHOD is not a recognized authentication method, so sign-in is refused until it "
        "is fixed."
    ),
    "default": "Authentication is disabled. Anyone can access Shelfmark without signing in.",
}


def _get_request_source_options() -> list[dict[str, str]]:
    """Build request-policy source options from registered release sources."""
    from shelfmark.release_sources import list_available_sources

    return [
        {
            "value": source["name"],
            "label": source["display_name"],
        }
        for source in list_available_sources()
    ]


def _get_valid_release_source_names_for_content_type(content_type: str) -> set[str]:
    """Return registered release source names that support the requested content type."""
    from shelfmark.release_sources import list_available_sources

    valid_sources: set[str] = set()
    for source in list_available_sources():
        supported_types = source.get("supported_content_types", ["ebook", "audiobook"])
        if content_type in supported_types:
            valid_sources.add(source["name"])
    return valid_sources


def _get_request_policy_rule_columns() -> list[dict[str, object]]:
    source_capabilities = get_source_content_type_capabilities()
    content_type_options = []

    for source_name, supported_types in source_capabilities.items():
        normalized_types = [t for t in ("ebook", "audiobook") if t in supported_types]
        content_type_options.extend(
            {
                "value": content_type,
                "label": "Ebook" if content_type == "ebook" else "Audiobook",
                "childOf": source_name,
            }
            for content_type in normalized_types
        )

    return [
        {
            "key": "source",
            "label": "Source",
            "type": "select",
            "options": _get_request_source_options(),
            "defaultValue": "",
            "placeholder": "Select source...",
        },
        {
            "key": "content_type",
            "label": "Content Type",
            "type": "select",
            "options": content_type_options,
            "defaultValue": "",
            "placeholder": "Select content type...",
            "filterByField": "source",
        },
        {
            "key": "mode",
            "label": "Mode",
            "type": "select",
            "options": _REQUEST_MATRIX_MODE_OPTIONS,
            "defaultValue": "",
            "placeholder": "Select mode...",
        },
    ]


def _validate_book_languages(value: Any) -> tuple[Any, str | None]:
    """Validate a per-user default language list against the known languages.

    Accepts the list the settings UI sends as well as a comma-separated string, so an
    API client can spell the value the way the env var does. Blank entries are skipped
    rather than rejected, which makes "" and "en," mean the same as [] and ["en"]. An
    empty result is a deliberate override meaning "no default language filter", so it
    is kept as-is; ``None`` clears the override further up the chain.
    """
    entries = value.split(",") if isinstance(value, str) else value
    if not isinstance(entries, (list, tuple)):
        return value, "BOOK_LANGUAGE must be a list of language codes"

    normalized: list[str] = []
    for entry in entries:
        if entry is None or (isinstance(entry, str) and not entry.strip()):
            continue
        code = normalize_language(entry)
        if code is None:
            return value, f"BOOK_LANGUAGE contains an unsupported language: {entry}"
        if code not in normalized:
            normalized.append(code)

    return normalized, None


def validate_search_preference_value(key: str, value: Any) -> tuple[Any, str | None]:
    """Validate and normalize a search preference value for user overrides."""
    if key not in SEARCH_PREFERENCE_VALIDATABLE_KEYS:
        return value, None

    if value is None:
        return None, None

    if key == "BOOK_LANGUAGE":
        return _validate_book_languages(value)

    normalized_value = str(value).strip()

    if key == "SEARCH_MODE":
        normalized_mode = normalized_value.lower()
        if normalized_mode not in _SEARCH_MODE_VALUES:
            return value, "SEARCH_MODE must be 'direct' or 'universal'"
        return normalized_mode, None

    if key in _SEARCH_PREFERENCE_PROVIDER_KEYS:
        if normalized_value == "":
            return "", None
        from shelfmark.metadata_providers import is_provider_registered

        if not is_provider_registered(normalized_value):
            return (
                value,
                f"{key} must be a valid metadata provider name or empty",
            )
        return normalized_value, None

    if key in {"DEFAULT_RELEASE_SOURCE", "DEFAULT_RELEASE_SOURCE_AUDIOBOOK"}:
        if normalized_value == "":
            return "", None
        valid_sources = _get_valid_release_source_names_for_content_type(
            "audiobook" if key == "DEFAULT_RELEASE_SOURCE_AUDIOBOOK" else "ebook"
        )
        if normalized_value not in valid_sources:
            return (
                value,
                f"{key} must be a valid release source name or empty",
            )
        return normalized_value, None

    if key in _SEARCH_PREFERENCE_BOOLEAN_KEYS:
        if isinstance(value, bool):
            return value, None
        return bool(value), None

    return value, None


def _on_save_users(values: dict[str, object]) -> dict[str, object]:
    """Validate users/request-policy settings before persistence."""
    if "VISIBLE_SELF_SETTINGS_SECTIONS" in values:
        raw_sections = values["VISIBLE_SELF_SETTINGS_SECTIONS"]
        if raw_sections is None:
            candidate_sections: list[str] = []
        elif isinstance(raw_sections, str):
            candidate_sections = [s.strip() for s in raw_sections.split(",") if s.strip()]
        elif isinstance(raw_sections, (list, tuple, set)):
            candidate_sections = [
                str(section).strip() for section in raw_sections if str(section).strip()
            ]
        else:
            return {
                "error": True,
                "message": "VISIBLE_SELF_SETTINGS_SECTIONS must be a list of section identifiers",
                "values": values,
            }

        normalized_sections: list[str] = []
        for section in candidate_sections:
            if section not in _SELF_SETTINGS_SECTION_VALUES:
                allowed = ", ".join(sorted(_SELF_SETTINGS_SECTION_VALUES))
                return {
                    "error": True,
                    "message": (
                        "VISIBLE_SELF_SETTINGS_SECTIONS contains an unsupported section "
                        f"'{section}'. Supported values: {allowed}"
                    ),
                    "values": values,
                }
            if section not in normalized_sections:
                normalized_sections.append(section)

        values["VISIBLE_SELF_SETTINGS_SECTIONS"] = normalized_sections

    if (
        "REQUEST_POLICY_DEFAULT_EBOOK" in values
        and parse_policy_mode(values["REQUEST_POLICY_DEFAULT_EBOOK"]) is None
    ):
        return {
            "error": True,
            "message": "REQUEST_POLICY_DEFAULT_EBOOK must be a valid policy mode",
            "values": values,
        }

    if (
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK" in values
        and parse_policy_mode(values["REQUEST_POLICY_DEFAULT_AUDIOBOOK"]) is None
    ):
        return {
            "error": True,
            "message": "REQUEST_POLICY_DEFAULT_AUDIOBOOK must be a valid policy mode",
            "values": values,
        }

    if "REQUEST_POLICY_RULES" in values:
        normalized_rules, errors = validate_policy_rules(values["REQUEST_POLICY_RULES"])
        if errors:
            return {
                "error": True,
                "message": "; ".join(errors),
                "values": values,
            }
        values["REQUEST_POLICY_RULES"] = normalized_rules

    for key in SEARCH_PREFERENCE_VALIDATABLE_KEYS:
        if key not in values:
            continue
        normalized_value, validation_error = validate_search_preference_value(key, values[key])
        if validation_error:
            return {
                "error": True,
                "message": validation_error,
                "values": values,
            }
        values[key] = normalized_value

    return {"error": False, "values": values}


register_on_save("users", _on_save_users)


@register_settings("users", "Users & Requests", icon="users", order=6)
def users_settings() -> list[SettingsField]:
    """User management tab - rendered as a custom component on the frontend."""
    return [
        HeadingField(
            key="users_heading",
            title="Users",
            description=_USERS_HEADING_DESCRIPTION_BY_AUTH_MODE["default"],
            description_by_auth_mode=_USERS_HEADING_DESCRIPTION_BY_AUTH_MODE,
        ),
        CustomComponentField(
            key="users_management",
            component="users_management",
        ),
        MultiSelectField(
            key="VISIBLE_SELF_SETTINGS_SECTIONS",
            label="Visible Self-Settings Sections",
            description=(
                "Choose which personal settings sections are shown in My Account for non-admin users."
            ),
            options=_SELF_SETTINGS_SECTION_OPTIONS,
            default=_SELF_SETTINGS_SECTION_DEFAULTS,
            variant="dropdown",
            env_supported=False,
        ),
        HeadingField(
            key="requests_heading",
            title="Requests",
            description=("Choose what users can download directly and what needs approval first."),
        ),
        CheckboxField(
            key="REQUESTS_ENABLED",
            label="Enable Requests",
            description=(
                "Turn this off to let everyone download directly without needing approval."
            ),
            default=False,
            user_overridable=True,
        ),
        CustomComponentField(
            key="request_policy_editor",
            component="request_policy_grid",
            label="Request Rules",
            description=(
                "Fine-tune access per source. Source rules can only be the same or more restrictive than the default above."
            ),
            show_when={"field": "REQUESTS_ENABLED", "value": True},
            wrap_in_field_wrapper=True,
            value_fields=[
                SelectField(
                    key="REQUEST_POLICY_DEFAULT_EBOOK",
                    label="Default Ebook Mode",
                    description=("Sets the baseline for all ebook sources."),
                    options=_REQUEST_DEFAULT_MODE_OPTIONS,
                    default="download",
                    user_overridable=True,
                ),
                SelectField(
                    key="REQUEST_POLICY_DEFAULT_AUDIOBOOK",
                    label="Default Audiobook Mode",
                    description=("Sets the baseline for all audiobook sources."),
                    options=_REQUEST_DEFAULT_MODE_OPTIONS,
                    default="download",
                    user_overridable=True,
                ),
                TableField(
                    key="REQUEST_POLICY_RULES",
                    label="Request Rules",
                    description=(
                        "Fine-tune access per source. Source rules can only be the same or more restrictive than the default above."
                    ),
                    columns=_get_request_policy_rule_columns,
                    default=[],
                    add_label="Add Rule",
                    empty_message="No request policy rules configured.",
                    env_supported=False,
                    user_overridable=True,
                ),
            ],
        ),
        NumberField(
            key="MAX_PENDING_REQUESTS_PER_USER",
            label="Max pending requests per user",
            description="How many open requests a user can have at a time.",
            default=20,
            min_value=1,
            max_value=1000,
            user_overridable=True,
            show_when={"field": "REQUESTS_ENABLED", "value": True},
        ),
        CheckboxField(
            key="REQUESTS_ALLOW_NOTES",
            label="Allow notes on requests",
            description="Let users add a note when they submit a request.",
            default=True,
            user_overridable=True,
            show_when={"field": "REQUESTS_ENABLED", "value": True},
        ),
        CheckboxField(
            key="REQUESTS_ALLOW_BOOK_REQUEST_WITH_DOWNLOAD",
            label="Allow book requests alongside downloads",
            description=(
                "Let users who can download also request a book without choosing a release, for "
                "a book nobody has released yet or one no source has. The request waits for an "
                "admin. Admins can always do this."
            ),
            default=False,
            user_overridable=True,
            show_when={"field": "REQUESTS_ENABLED", "value": True},
        ),
    ]
