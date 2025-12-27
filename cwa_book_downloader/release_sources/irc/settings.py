"""IRC settings registration.

Registers IRC Highway settings for the settings UI.
"""

from cwa_book_downloader.core.settings_registry import (
    CheckboxField,
    HeadingField,
    TextField,
    register_settings,
)


@register_settings(
    name="irc",
    display_name="IRC Highway",
    icon="download",
    order=56,
)
def irc_settings():
    """Define IRC source settings."""
    return [
        HeadingField(
            key="heading",
            title="IRC Highway",
            description=(
                "Search and download books from IRC Highway #ebooks channel. "
                "This source connects via IRC and uses DCC for file transfers. "
                "Note: DCC requires direct TCP connections to arbitrary ports, "
                "which may not work behind strict firewalls or NAT."
            ),
        ),

        CheckboxField(
            key="IRC_ENABLED",
            label="Enable IRC source",
            default=False,
            description="Enable searching and downloading from IRC Highway",
        ),

        TextField(
            key="IRC_NICK",
            label="Nickname",
            placeholder="Leave empty for random",
            description="Your IRC nickname. Leave empty to generate a random one.",
            env_supported=True,
            show_when={"field": "IRC_ENABLED", "value": True},
        ),

        TextField(
            key="IRC_SEARCH_BOT",
            label="Search bot",
            placeholder="search",
            default="search",
            description="The search bot to query (usually 'search' or 'searchook')",
            env_supported=True,
            show_when={"field": "IRC_ENABLED", "value": True},
        ),
    ]
