"""Request-policy resolution helpers.

This module is intentionally pure and side-effect free so it can be reused by
routes/services and tested independently.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from enum import StrEnum
from typing import Any


class PolicyMode(StrEnum):
    """Allowed request-policy modes.

    Ordered from most to least permissive. The content-type default acts as a
    ceiling — matrix rules can only match or restrict further, never upgrade
    beyond the default.
    """

    DOWNLOAD = "download"
    REQUEST_RELEASE = "request_release"
    REQUEST_BOOK = "request_book"
    BLOCKED = "blocked"


# Permissiveness ordering: lower index = more permissive.
_MODE_PERMISSIVENESS: dict[PolicyMode, int] = {
    PolicyMode.DOWNLOAD: 0,
    PolicyMode.REQUEST_RELEASE: 1,
    PolicyMode.REQUEST_BOOK: 2,
    PolicyMode.BLOCKED: 3,
}

# Modes allowed in REQUEST_POLICY_RULES matrix rows.
MATRIX_ALLOWED_MODES = frozenset(
    {PolicyMode.DOWNLOAD, PolicyMode.REQUEST_RELEASE, PolicyMode.BLOCKED}
)


def cap_mode(mode: PolicyMode, ceiling: PolicyMode) -> PolicyMode:
    """Cap a resolved mode so it cannot be more permissive than the ceiling."""
    if _MODE_PERMISSIVENESS[mode] < _MODE_PERMISSIVENESS[ceiling]:
        return ceiling
    return mode


def _source_results_are_releases(source: Any) -> bool:
    normalized_source = normalize_source(source)
    if normalized_source in {"", "*"}:
        return False
    from shelfmark.release_sources import source_results_are_releases

    return source_results_are_releases(normalized_source)


def _normalize_release_result_mode(source: Any, mode: PolicyMode) -> PolicyMode:
    """Concrete release browse results cannot fall back to request_book semantics."""
    if mode == PolicyMode.REQUEST_BOOK and _source_results_are_releases(source):
        return PolicyMode.REQUEST_RELEASE
    return mode


REQUEST_POLICY_KEYS = frozenset(
    {
        "REQUESTS_ENABLED",
        "REQUEST_POLICY_DEFAULT_EBOOK",
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK",
        "REQUEST_POLICY_RULES",
        "MAX_PENDING_REQUESTS_PER_USER",
        "REQUESTS_ALLOW_NOTES",
        "REQUESTS_ALLOW_BOOK_REQUEST_WITH_DOWNLOAD",
    }
)

REQUEST_POLICY_DEFAULT_FALLBACK_MODE = PolicyMode.REQUEST_BOOK

DEFAULT_SUPPORTED_CONTENT_TYPES = ("ebook", "audiobook")


def filter_request_policy_settings(settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return only uppercase request-policy keys from a settings JSON object."""
    if not isinstance(settings, Mapping):
        return {}
    return {key: settings[key] for key in REQUEST_POLICY_KEYS if key in settings}


def merge_request_policy_settings(
    global_settings: Mapping[str, Any] | None,
    user_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge global settings with per-user request-policy overrides."""
    merged = filter_request_policy_settings(global_settings)
    user_filtered = filter_request_policy_settings(user_settings)

    # Preserve global rules by default and treat user rules as per-cell overlays.
    # This allows per-user REQUEST_POLICY_RULES payloads to store only explicit
    # differences instead of replacing the full global matrix.
    global_rules = list(_iter_rules(merged.get("REQUEST_POLICY_RULES", [])))
    user_has_rules = "REQUEST_POLICY_RULES" in user_filtered

    for key, value in user_filtered.items():
        if key == "REQUEST_POLICY_RULES":
            continue
        merged[key] = value

    if user_has_rules:
        merged_rules: dict[tuple[str, str], tuple[str, str, PolicyMode]] = {
            (source, content_type): (source, content_type, mode)
            for source, content_type, mode in global_rules
        }
        for source, content_type, mode in _iter_rules(
            user_filtered.get("REQUEST_POLICY_RULES", [])
        ):
            merged_rules[(source, content_type)] = (source, content_type, mode)
        merged["REQUEST_POLICY_RULES"] = [
            {"source": source, "content_type": content_type, "mode": mode.value}
            for source, content_type, mode in merged_rules.values()
        ]

    return merged


def normalize_content_type(content_type: Any) -> str:
    """Normalize arbitrary content type values to `ebook` or `audiobook`."""
    if not isinstance(content_type, str):
        return "ebook"

    value = content_type.strip().lower()
    if not value:
        return "ebook"

    if value in {"audiobook", "audiobooks", "audio", "book (audiobook)"}:
        return "audiobook"

    return "ebook"


def normalize_source(source: Any) -> str:
    """Normalize source values for policy matching."""
    if not isinstance(source, str):
        return "*"

    value = source.strip().lower()
    return value or "*"


def parse_policy_mode(mode: Any) -> PolicyMode | None:
    """Parse an arbitrary mode value into a PolicyMode enum member."""
    if isinstance(mode, PolicyMode):
        return mode
    if not isinstance(mode, str):
        return None
    try:
        return PolicyMode(mode.strip().lower())
    except ValueError:
        return None


def _normalize_rule_content_type(content_type: Any) -> str | None:
    if not isinstance(content_type, str):
        return None
    value = content_type.strip().lower()
    if not value:
        return None
    if value in {"*", "any"}:
        return "*"
    if value in {"ebook", "book", "books", "book (fiction)"}:
        return "ebook"
    if value in {"audiobook", "audiobooks", "audio", "book (audiobook)"}:
        return "audiobook"
    return None


def _normalize_rule_source(source: Any) -> str | None:
    if not isinstance(source, str):
        return None
    value = source.strip().lower()
    if not value:
        return None
    if value in {"*", "any"}:
        return "*"
    return value


def get_source_content_type_capabilities() -> dict[str, set[str]]:
    """Return source -> supported content type map from registered sources."""
    try:
        from shelfmark.release_sources import list_available_sources
    except ImportError:
        return {}

    capabilities: dict[str, set[str]] = {}
    for source in list_available_sources():
        raw_name = source.get("name")
        name = normalize_source(raw_name)
        if not name or name == "*":
            continue

        raw_types = source.get("supported_content_types", DEFAULT_SUPPORTED_CONTENT_TYPES)
        if isinstance(raw_types, str) or not isinstance(raw_types, Sequence):
            raw_types = DEFAULT_SUPPORTED_CONTENT_TYPES

        normalized_types: set[str] = set()
        for content_type in raw_types:
            normalized_type = _normalize_rule_content_type(content_type)
            if normalized_type and normalized_type != "*":
                normalized_types.add(normalized_type)

        if not normalized_types:
            normalized_types = set(DEFAULT_SUPPORTED_CONTENT_TYPES)
        capabilities[name] = normalized_types
    return capabilities


def validate_policy_rules(
    rules: Any,
    source_capabilities: Mapping[str, set[str]] | None = None,
) -> tuple[list[dict[str, str]], list[str]]:
    """Validate and normalize policy rule rows.

    Validation covers:
    - row shape and required keys
    - valid mode/content_type values
    - known source names
    - source/content-type compatibility from source declarations
    """
    capabilities = (
        source_capabilities
        if source_capabilities is not None
        else get_source_content_type_capabilities()
    )
    normalized_capabilities = {
        normalize_source(source): {
            normalize_content_type(content_type) for content_type in content_types
        }
        for source, content_types in capabilities.items()
    }

    normalized_rules: list[dict[str, str]] = []
    errors: list[str] = []

    if rules is None:
        return normalized_rules, errors
    if not isinstance(rules, list):
        return normalized_rules, ["REQUEST_POLICY_RULES must be a list"]

    for index, rule in enumerate(rules):
        row_label = f"Rule {index + 1}"
        if not isinstance(rule, Mapping):
            errors.append(f"{row_label}: must be an object")
            continue

        source = _normalize_rule_source(rule.get("source"))
        raw_content_type = rule.get("content_type")
        content_type = _normalize_rule_content_type(rule.get("content_type"))
        raw_mode = rule.get("mode")
        mode = parse_policy_mode(rule.get("mode"))

        if source is None:
            errors.append(f"{row_label}: source is required")
            continue
        if raw_content_type is None or (
            isinstance(raw_content_type, str) and not raw_content_type.strip()
        ):
            errors.append(f"{row_label}: content_type is required")
            continue
        if content_type is None:
            errors.append(f"{row_label}: invalid content_type '{rule.get('content_type')}'")
            continue
        if raw_mode is None or (isinstance(raw_mode, str) and not raw_mode.strip()):
            errors.append(f"{row_label}: mode is required")
            continue
        if mode is None:
            errors.append(f"{row_label}: invalid mode '{rule.get('mode')}'")
            continue
        if mode not in MATRIX_ALLOWED_MODES:
            errors.append(
                f"{row_label}: mode '{mode.value}' is not allowed in matrix rules (use content-type defaults instead)"
            )
            continue

        if source != "*" and source not in normalized_capabilities:
            errors.append(f"{row_label}: unknown source '{source}'")
            continue

        if (
            source != "*"
            and content_type != "*"
            and source in normalized_capabilities
            and content_type not in normalized_capabilities[source]
        ):
            errors.append(
                f"{row_label}: source '{source}' does not support content_type '{content_type}'"
            )
            continue

        normalized_rules.append(
            {
                "source": source,
                "content_type": content_type,
                "mode": mode.value,
            }
        )

    return normalized_rules, errors


def _iter_rules(rules: Any) -> Iterable[tuple[str, str, PolicyMode]]:
    if not isinstance(rules, list):
        return []

    normalized: list[tuple[str, str, PolicyMode]] = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        source = _normalize_rule_source(rule.get("source"))
        content_type = _normalize_rule_content_type(rule.get("content_type"))
        mode = parse_policy_mode(rule.get("mode"))
        if (
            source is None
            or content_type is None
            or mode is None
            or mode not in MATRIX_ALLOWED_MODES
        ):
            continue
        normalized.append((source, content_type, mode))
    return normalized


def resolve_policy_mode(
    *,
    source: Any,
    content_type: Any,
    global_settings: Mapping[str, Any] | None,
    user_settings: Mapping[str, Any] | None = None,
) -> PolicyMode:
    """Resolve an effective policy mode for a request context.

    Resolution:
    1. Resolve the content-type default (ceiling).
    2. Match rules in specificity order.
    3. Cap the matched rule at the ceiling.
    4. If no rule matches, return the ceiling.

    The content-type default acts as a ceiling — matrix rules can only
    match or restrict further, never upgrade beyond the default.

    Concrete-release browse exception:
    - sources whose browse results are already concrete releases normalize
      request_book to request_release.
    """
    effective = merge_request_policy_settings(global_settings, user_settings)
    normalized_source = normalize_source(source)
    normalized_content_type = normalize_content_type(content_type)

    # Resolve the content-type default (ceiling)
    default_key = (
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK"
        if normalized_content_type == "audiobook"
        else "REQUEST_POLICY_DEFAULT_EBOOK"
    )
    default_mode = parse_policy_mode(effective.get(default_key))
    ceiling = default_mode if default_mode is not None else REQUEST_POLICY_DEFAULT_FALLBACK_MODE

    # Match rules in specificity order
    rules = tuple(_iter_rules(effective.get("REQUEST_POLICY_RULES", [])))
    candidates = (
        (normalized_source, normalized_content_type),
        (normalized_source, "*"),
        ("*", normalized_content_type),
        ("*", "*"),
    )
    for candidate_source, candidate_content_type in candidates:
        for rule_source, rule_content_type, rule_mode in rules:
            if rule_source == candidate_source and rule_content_type == candidate_content_type:
                return _normalize_release_result_mode(
                    normalized_source,
                    cap_mode(rule_mode, ceiling),
                )

    return _normalize_release_result_mode(normalized_source, ceiling)
