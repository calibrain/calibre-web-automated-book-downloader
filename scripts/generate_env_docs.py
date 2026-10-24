#!/usr/bin/env python3
"""Generate markdown documentation for environment variables from the settings registry.

This script extracts all settings that support environment variable configuration
and generates a comprehensive markdown file documenting each option.

Usage:
    python scripts/generate_env_docs.py [--output path/to/output.md]

The generated documentation includes:
- Environment variable name
- Description
- Type (string, number, boolean, etc.)
- Default value
- Organizational grouping by settings tab/group
"""

import argparse
import re
import sys
from pathlib import Path
from typing import Any

# Add project root to path
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))


def get_field_type_name(field: Any) -> str:
    """Get a human-readable type name for a field."""
    from shelfmark.core.settings_registry import (
        CheckboxField,
        MultiSelectField,
        NumberField,
        OrderableListField,
        PasswordField,
        SelectField,
        TagListField,
        TextField,
    )

    if isinstance(field, CheckboxField):
        return "boolean"
    if isinstance(field, NumberField):
        return "number"
    if isinstance(field, SelectField):
        return "string (choice)"
    if isinstance(field, MultiSelectField):
        return "string (comma-separated)"
    if isinstance(field, TagListField):
        return "string (comma-separated)"
    if isinstance(field, OrderableListField):
        return "JSON array"
    if isinstance(field, PasswordField):
        return "string (secret)"
    if isinstance(field, TextField):
        return "string"
    return "string"


def format_default_value(field: Any) -> str:
    """Format the default value for display."""
    default = field.default

    if default is None:
        return "_none_"
    if isinstance(default, bool):
        return f"`{str(default).lower()}`"
    if isinstance(default, (int, float)):
        return f"`{default}`"
    if isinstance(default, str):
        if default == "":
            return "_empty string_"
        return f"`{default}`"
    if isinstance(default, list):
        if not default:
            return "_empty list_"
        # For simple lists, show comma-separated values
        if all(isinstance(item, str) for item in default):
            return f"`{','.join(default)}`"
        # For complex lists (e.g., OrderableListField defaults), summarize
        return "_see UI for defaults_"
    return f"`{default}`"


def get_select_options(field: Any) -> list[str] | None:
    """Get the available options for a SelectField.

    Returns options formatted as 'value (label)' or just 'value' if they match,
    so users know the actual values to use in environment variables.
    """
    from shelfmark.core.settings_registry import SelectField

    if not isinstance(field, SelectField):
        return None

    options = field.options
    if callable(options):
        try:
            options = options()
        except Exception:
            return None

    if not options:
        return None

    result = []
    for opt in options:
        value = opt.get("value", "")
        label = opt.get("label", "")

        # Format as "value (label)" unless they're the same or value is empty
        if value == "":
            result.append(f'`""` ({label})')
        elif value == label or not label:
            result.append(f"`{value}`")
        else:
            result.append(f"`{value}` ({label})")

    return result


def _anchor(title: str) -> str:
    """Heading anchor the way GitHub builds it: lowercase, punctuation dropped, spaces to hyphens."""
    return re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")


def _generate_bootstrap_env_docs() -> list[str]:
    """Generate documentation for bootstrap environment variables from env.py."""
    # These are environment variables defined in env.py that are used before
    # the settings registry is available
    bootstrap_vars = [
        {
            "name": "CONFIG_DIR",
            "description": "Directory for storing configuration files and plugin settings.",
            "type": "string (path)",
            "default": "/config",
        },
        {
            "name": "LOG_ROOT",
            "description": "Root directory for log files.",
            "type": "string (path)",
            "default": "/var/log/",
        },
        {
            "name": "TMP_DIR",
            "description": "Staging directory for downloads before moving to destination.",
            "type": "string (path)",
            "default": "/tmp/shelfmark",
        },
        {
            "name": "ENABLE_LOGGING",
            "description": "Enable file logging under LOG_ROOT/shelfmark/ (including shelfmark.log and startup logs).",
            "type": "boolean",
            "default": "true",
        },
        {
            "name": "FLASK_HOST",
            "description": "Host address for the Flask web server.",
            "type": "string",
            "default": "0.0.0.0",
        },
        {
            "name": "FLASK_PORT",
            "description": "Port number for the Flask web server.",
            "type": "number",
            "default": "8084",
        },
        {
            "name": "SOCKETIO_ALLOWED_ORIGINS",
            "description": "Extra origins allowed to open the live-update websocket, comma separated (e.g. https://books.example.com). The app's own origin is always allowed, including behind a proxy that sends X-Forwarded-Host and X-Forwarded-Proto. Only needed if your proxy changes Host without sending those headers.",
            "type": "string",
            "default": "unset",
        },
        {
            "name": "SESSION_COOKIE_SECURE",
            "description": "Enable secure cookies (requires HTTPS).",
            "type": "boolean",
            "default": "false",
        },
        {
            "name": "CWA_DB_PATH",
            "description": "Path to the Calibre-Web database for authentication integration.",
            "type": "string (path)",
            "default": "/auth/app.db",
        },
        {
            "name": "HIDE_LOCAL_AUTH",
            "description": "Hide the username/password login form when OIDC is active.",
            "type": "boolean",
            "default": "false",
        },
        {
            "name": "DISABLE_LOCAL_AUTH",
            "description": "Disable username/password login and remove the local-admin prerequisite for OIDC. Implies HIDE_LOCAL_AUTH; with AUTH_METHOD=builtin, everyone is locked out until auth env vars are changed.",
            "type": "boolean",
            "default": "false",
        },
        {
            "name": "SHELFMARK_API_KEY",
            "description": "Optional static API key. When set, requests carrying it as 'Authorization: Bearer <key>' (or X-Api-Key) are authenticated as an admin; browser sessions keep working. Unset = off.",
            "type": "string",
            "default": "unset",
        },
        {
            "name": "SHELFMARK_API_KEY_READONLY",
            "description": "Optional second API key for dashboards and monitors. Send it like SHELFMARK_API_KEY. It can only GET /api/stats (counters only, no titles or user names). Every other request, and any write, is refused with 403, and it never gets a session. Unset = off.",
            "type": "string",
            "default": "unset",
        },
        {
            "name": "OIDC_AUTO_REDIRECT",
            "description": "Automatically redirect to the OIDC provider instead of showing the login page.",
            "type": "boolean",
            "default": "false",
        },
        {
            "name": "UMASK",
            "description": "File creation mask for the app process, read by entrypoint.sh. The default 0022 gives files 644 and directories 755.",
            "type": "string (octal)",
            "default": "0022",
        },
        {
            "name": "DOCKERMODE",
            "description": "Indicates the application is running inside a Docker container.",
            "type": "boolean",
            "default": "false",
        },
        {
            "name": "ONBOARDING",
            "description": "Show the onboarding wizard on first run. Set to false to skip (useful for ephemeral storage).",
            "type": "boolean",
            "default": "true",
        },
    ]

    lines = [
        "## Bootstrap Configuration",
        "",
        "These environment variables are used at startup before the settings system loads. They typically configure paths, server settings, and authentication startup behavior.",
        "",
        "| Variable | Description | Type | Default |",
        "|----------|-------------|------|---------|",
    ]

    lines.extend(
        f"| `{var['name']}` | {var['description']} | {var['type']} | `{var['default']}` |"
        for var in bootstrap_vars
    )

    lines.append("")
    lines.append("<details>")
    lines.append("<summary>Detailed descriptions</summary>")
    lines.append("")

    for var in bootstrap_vars:
        lines.append(f"#### `{var['name']}`")
        lines.append("")
        lines.append(var["description"])
        lines.append("")
        lines.append(f"- **Type:** {var['type']}")
        lines.append(f"- **Default:** `{var['default']}`")
        lines.append("")

    lines.append("</details>")
    lines.append("")

    return lines


def _generate_egress_env_docs() -> list[str]:
    """Generate documentation for VPN/Tor egress environment variables.

    These are startup-only variables consumed by entrypoint.sh / wireguard.sh
    (before and outside the settings registry) to select and configure the
    transparent-egress kill-switch. `USING_TOR` has a registry-backed entry
    under Network and is cross-referenced rather than repeated here so the two
    mutually exclusive egress modes are discoverable side by side without
    emitting a duplicate `#### USING_TOR` anchor.
    """
    egress_vars = [
        {
            "name": "USING_WIREGUARD",
            "description": "Route all traffic through a WireGuard VPN tunnel with a fail-closed iptables kill-switch (non-tunnel egress is dropped). Requires root startup and NET_ADMIN (plus NET_RAW). Mutually exclusive with USING_TOR.",
            "type": "boolean",
            "default": "false",
        },
        {
            "name": "WIREGUARD_CONFIG",
            "description": "Path to the mounted wg-quick configuration file.",
            "type": "string (path)",
            "default": "/config/wg0.conf",
        },
        {
            "name": "WIREGUARD_INTERFACE",
            "description": "WireGuard interface name brought up by wg-quick.",
            "type": "string",
            "default": "wg0",
        },
        {
            "name": "LAN_NETWORK",
            "description": "Comma-separated CIDRs kept off the tunnel so the WebUI and internal download clients (Prowlarr, qBittorrent) stay reachable.",
            "type": "string (comma-separated)",
            "default": "127.0.0.0/8,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16",
        },
        {
            "name": "WIREGUARD_ENFORCE_DNS",
            "description": "Pin the container's resolver so DNS cannot silently fall back to an off-tunnel path. The resolver used is WIREGUARD_DNS if set, else the tunnel config's DNS = line. This does NOT force queries through the tunnel: it is designed for a trusted LAN resolver kept reachable off-tunnel via LAN_NETWORK (the query leaves over the LAN; the resolver encrypts upstream while the download still egresses via the tunnel). Special case: when Docker's embedded resolver (nameserver 127.0.0.11) is present, it is PRESERVED so container-name resolution (e.g. prowlarr, qbittorrent) keeps working, and the embedded resolver's upstream must be pinned via the container's compose dns: list. Fails closed (refuses to start) only when no embedded resolver is present AND no resolver is defined, or /etc/resolv.conf is not writable.",
            "type": "boolean",
            "default": "true",
        },
        {
            "name": "WIREGUARD_DNS",
            "description": "Explicit resolver(s) (comma/space separated) to pin when WIREGUARD_ENFORCE_DNS is true and Docker's embedded resolver is NOT in use. Use when the VPN's pushed DNS filters domains you need; point it at a resolver reachable via the tunnel or an allowed LAN resolver. NOTE: when the embedded resolver (127.0.0.11) is present it is preserved and this value cannot repoint its upstream from inside the container — set the container's compose dns: list to the trusted resolver instead.",
            "type": "string (comma-separated)",
            "default": "unset (uses config DNS = line)",
        },
        {
            "name": "WIREGUARD_DISABLE_IPV6",
            "description": "Strip IPv6 Address/AllowedIPs/DNS from the tunnel config before wg-quick (many container kernels lack the ip6tables raw table wg-quick needs) and remove IPv6 as a leak surface.",
            "type": "boolean",
            "default": "true",
        },
        {
            "name": "WIREGUARD_ALLOW_IPV6_LEAK",
            "description": "Escape hatch: continue startup even when an IPv6 kill-switch cannot be installed AND IPv6 cannot be disabled. Only set when the container has no IPv6 connectivity, as IPv6 egress may otherwise bypass the tunnel.",
            "type": "boolean",
            "default": "false",
        },
        {
            "name": "WIREGUARD_ALLOW_WEBUI_OFFTUNNEL",
            "description": "When false (default) the kill-switch is strictly fail-closed: the only off-tunnel egress permitted is loopback, the tunnel device and the LAN allowlist. Set true only if a NON-LAN client (e.g. a public reverse proxy on a different segment) must reach the WebUI; it permits app-server REPLY packets (--sport FLASK_PORT, conntrack REPLY) to leave off-tunnel. Server replies only, never client-initiated egress, so it cannot leak outbound browsing/downloads or the real IP for outbound requests, but it is still an off-tunnel path while the tunnel is down, hence opt-in. LAN WebUI clients never need it (covered by LAN_NETWORK).",
            "type": "boolean",
            "default": "false",
        },
        {
            "name": "WIREGUARD_STALE_AFTER",
            "description": "Seconds since the last WireGuard handshake before the healthcheck bounces the tunnel.",
            "type": "number",
            "default": "180",
        },
        {
            "name": "TOR_CHECK_INTERVAL",
            "description": "Seconds between Tor watchdog probes. The watchdog resets the container when Tor stops being usable, so the restart policy rebuilds the Tor setup.",
            "type": "integer (seconds)",
            "default": "30",
        },
        {
            "name": "TOR_MAX_FAILURES",
            "description": "Consecutive failed Tor watchdog probes before the container is reset. 2 tolerates one failed probe.",
            "type": "integer",
            "default": "2",
        },
    ]

    lines = [
        "## Egress / VPN Routing",
        "",
        "These startup-only variables are consumed by `entrypoint.sh` / `wireguard.sh` to select and configure the WireGuard transparent-egress kill-switch. `USING_WIREGUARD` and [`USING_TOR`](#using_tor) (documented under Network) are mutually exclusive; both require root startup. `TOR_CHECK_INTERVAL` and `TOR_MAX_FAILURES` tune the Tor watchdog in `tor.sh`.",
        "",
        "| Variable | Description | Type | Default |",
        "|----------|-------------|------|---------|",
    ]

    lines.extend(
        f"| `{var['name']}` | {var['description']} | {var['type']} | `{var['default']}` |"
        for var in egress_vars
    )

    lines.append("")
    lines.append("<details>")
    lines.append("<summary>Detailed descriptions</summary>")
    lines.append("")

    for var in egress_vars:
        lines.append(f"#### `{var['name']}`")
        lines.append("")
        lines.append(var["description"])
        lines.append("")
        lines.append(f"- **Type:** {var['type']}")
        lines.append(f"- **Default:** `{var['default']}`")
        lines.append("")

    lines.append("</details>")
    lines.append("")

    return lines


def generate_env_docs() -> str:
    """Generate markdown documentation for all environment variables."""
    # Import settings modules to ensure all settings are registered
    import shelfmark.config.security
    import shelfmark.config.settings
    import shelfmark.metadata_providers.dnb
    import shelfmark.metadata_providers.googlebooks
    import shelfmark.metadata_providers.hardcover
    import shelfmark.metadata_providers.openlibrary
    import shelfmark.release_sources.irc.settings
    import shelfmark.release_sources.prowlarr.settings  # noqa: F401
    from shelfmark.core.settings_registry import (
        get_all_groups,
        get_all_settings_tabs,
    )

    tabs = get_all_settings_tabs()
    groups = {g.name: g for g in get_all_groups()}

    # Organize tabs by group
    grouped_tabs: dict[str | None, list] = {None: []}
    for group_name in groups:
        grouped_tabs[group_name] = []

    for tab in tabs:
        group_name = tab.group
        if group_name not in grouped_tabs:
            grouped_tabs[group_name] = []
        grouped_tabs[group_name].append(tab)

    # Build markdown output
    lines = [
        "# Environment Variables",
        "",
        "This document lists all configuration options that can be set via environment variables.",
        "",
        "> **Auto-generated** - Do not edit manually. Run `python scripts/generate_env_docs.py` to regenerate.",
        "",
        "## Table of Contents",
        "",
    ]

    # Generate TOC
    toc_entries = [
        "- [Bootstrap Configuration](#bootstrap-configuration)",
        "- [Egress / VPN Routing](#egress--vpn-routing)",
    ]

    # Ungrouped tabs first
    for tab in grouped_tabs.get(None, []):
        anchor = _anchor(tab.display_name)
        toc_entries.append(f"- [{tab.display_name}](#{anchor})")

    # Then grouped tabs
    for group_name, group in groups.items():
        group_tabs = grouped_tabs.get(group_name, [])
        if group_tabs:
            anchor = _anchor(group.display_name)
            toc_entries.append(f"- [{group.display_name}](#{anchor})")
            for tab in group_tabs:
                sub_anchor = _anchor(f"{group.display_name}-{tab.display_name}")
                toc_entries.append(f"  - [{tab.display_name}](#{sub_anchor})")

    lines.extend(toc_entries)
    lines.append("")
    lines.append("---")
    lines.append("")

    # Add bootstrap environment variables documentation
    lines.extend(_generate_bootstrap_env_docs())

    # Add egress / VPN routing (startup-only, shell-driven) documentation
    lines.extend(_generate_egress_env_docs())

    # Generate documentation for ungrouped tabs
    for tab in grouped_tabs.get(None, []):
        lines.extend(_generate_tab_docs(tab))

    # Generate documentation for grouped tabs
    for group_name, group in groups.items():
        group_tabs = grouped_tabs.get(group_name, [])
        if not group_tabs:
            continue

        lines.append(f"## {group.display_name}")
        lines.append("")

        for tab in group_tabs:
            lines.extend(_generate_tab_docs(tab, group_prefix=group.display_name))

    return "\n".join(lines)


def _generate_tab_docs(tab: Any, group_prefix: str | None = None) -> list[str]:
    """Generate documentation for a single settings tab."""
    from shelfmark.core.settings_registry import iter_value_fields

    lines = []

    # Section header
    if group_prefix:
        lines.append(f"### {group_prefix}: {tab.display_name}")
    else:
        lines.append(f"## {tab.display_name}")

    lines.append("")

    # Collect env-supported fields
    env_fields = [
        field for field in iter_value_fields(tab) if getattr(field, "env_supported", True)
    ]

    if not env_fields:
        lines.append("_No environment variables for this section._")
        lines.append("")
        return lines

    # Generate table
    lines.append("| Variable | Description | Type | Default |")
    lines.append("|----------|-------------|------|---------|")

    for field in env_fields:
        env_var = field.get_env_var_name()
        description = field.description or field.label
        # Clean up description for table (remove newlines, escape pipes)
        description = description.replace("\n", " ").replace("|", "\\|").strip()

        field_type = get_field_type_name(field)
        default = format_default_value(field)

        lines.append(f"| `{env_var}` | {description} | {field_type} | {default} |")

    lines.append("")

    # Add detailed documentation for each field
    lines.append("<details>")
    lines.append("<summary>Detailed descriptions</summary>")
    lines.append("")

    for field in env_fields:
        env_var = field.get_env_var_name()
        lines.append(f"#### `{env_var}`")
        lines.append("")
        lines.append(f"**{field.label}**")
        lines.append("")

        if field.description:
            lines.append(field.description)
            lines.append("")

        lines.append(f"- **Type:** {get_field_type_name(field)}")
        lines.append(f"- **Default:** {format_default_value(field)}")

        if getattr(field, "required", False):
            lines.append("- **Required:** Yes")

        if getattr(field, "requires_restart", False):
            lines.append("- **Requires restart:** Yes")

        # Show options for SelectField
        options = get_select_options(field)
        if options:
            lines.append(f"- **Options:** {', '.join(options)}")

        # Show constraints for NumberField
        from shelfmark.core.settings_registry import NumberField

        if isinstance(field, NumberField):
            constraints = []
            if field.min_value is not None:
                constraints.append(f"min: {field.min_value}")
            if field.max_value is not None:
                constraints.append(f"max: {field.max_value}")
            if constraints:
                lines.append(f"- **Constraints:** {', '.join(constraints)}")

        lines.append("")

    lines.append("</details>")
    lines.append("")

    return lines


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate markdown documentation for environment variables"
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=project_root / "docs" / "environment-variables.md",
        help="Output file path (default: docs/environment-variables.md)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print to stdout instead of file",
    )
    args = parser.parse_args()

    docs = generate_env_docs()

    if args.stdout:
        print(docs)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(docs)
        print(f"Generated: {args.output}")


if __name__ == "__main__":
    main()
