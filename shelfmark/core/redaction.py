"""Taking credentials and links out of text.

Three jobs, which used to live in three places with three sets of patterns:

* ``redact_secrets`` masks credentials where they stand and leaves the rest of a URL readable.
  The log formatter uses it, so nothing logged carries a key.
* ``safe_url`` and ``redact_urls_in_text`` keep a URL's scheme, host and path and drop its query,
  for an error message or a log line that names a link.
* ``strip_links`` removes links altogether, for text shown to someone who has no use for them.

This module imports nothing from the rest of the app, so the logger can depend on it.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse, urlunparse

# Query parameters that carry tracker/indexer credentials. Prowlarr guids are often
# the download URL itself (apikey included), and they end up in task ids that are
# logged all over the download pipeline, so mask them at the formatter instead.
_SECRET_QUERY_PARAM_PATTERN = re.compile(
    r"(?<=[?&;])"
    r"([a-z0-9_-]*api[_-]?key|api|apitoken|access_token|token|passkey|authkey|rsskey"
    r"|torrent_pass|auth|key|password|passwd|pwd|pass|secret|sid|uid)"
    r"=[^&\s#'\"<>;:,)\]]+",
    re.IGNORECASE,
)

# Credentials in a URL's userinfo, as in a proxy's socks5://user:pass@host:port. The authority
# ends at the first "/", "?" or "#" (RFC 3986), so an "@" after one of those is not userinfo.
_URL_USERINFO_PATTERN = re.compile(r"(://)[^/?#@\s'\"<>]+@")

# A credential carried as a path segment: a tracker passkey (/rss/<key>), or the token in the
# URL of a chat service's bot or webhook, which notification URLs end up logging. Only segments
# that name a secret are matched, so ids in our own routes (/api/download/<md5>) stay readable.
_SECRET_PATH_SEGMENT_PATTERN = re.compile(
    r"(?<=/)((?:passkey|apikey|api[_-]key|rsskey|authkey|token|secret|rss|torznab)/)"
    r"[A-Za-z0-9_.~-]{16,}",
    re.IGNORECASE,
)
_CHAT_SERVICE_TOKEN_PATTERNS = (
    # https://api.telegram.org/bot<id>:<token>/sendMessage
    re.compile(r"(/bot)\d+:[A-Za-z0-9_-]{20,}"),
    # https://discord.com/api/webhooks/<id>/<token>
    re.compile(r"(/webhooks/\d+/)[A-Za-z0-9_-]{20,}"),
    # https://hooks.slack.com/services/T000/B000/<token>
    re.compile(r"(/services/[A-Z0-9]+/[A-Z0-9]+/)[A-Za-z0-9]{16,}"),
)

# A link inside a message. A client error quotes the request: "401 Client Error: Unauthorized
# for url: <full url>", or for a refused connection "Max retries exceeded with url: <path and
# query>" with no scheme. Schemes are case-insensitive, and a closing quote or bracket is not
# part of the link.
_LINK_IN_TEXT_PATTERN = re.compile(
    r"https?://[^\s'\"<>)\]]+|(?<=url: )/[^\s'\"<>)\]]*",
    re.IGNORECASE,
)
_REMOVED_LINK = "(link removed)"


def redact_secrets(text: str) -> str:
    """Mask credentials in text, keeping the rest of a URL readable.

    Covers credential query parameters, userinfo (``user:pass@host``), path segments that
    name a secret and the tokens of Telegram, Discord and Slack URLs.
    """
    text = _SECRET_QUERY_PARAM_PATTERN.sub(r"\1=(removed)", text)
    text = _URL_USERINFO_PATTERN.sub(r"\1(removed)@", text)
    text = _SECRET_PATH_SEGMENT_PATTERN.sub(r"\1(removed)", text)
    for pattern in _CHAT_SERVICE_TOKEN_PATTERNS:
        text = pattern.sub(r"\1(removed)", text)
    return text


def safe_url(url: str, *, limit: int = 120) -> str:
    """Return a log-safe URL: scheme/host/path kept, query and fragment dropped.

    Torrent download URLs commonly carry credentials in their query string
    (Prowlarr's ``apikey=...`` proxy links among them), so raw URLs must never
    reach logs or exception messages.
    """
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return f"<unparsed url: {type(url).__name__}>"
    safe = urlunparse(parsed._replace(query="", fragment=""))
    return safe[:limit]


def _link_without_query(link: str) -> str:
    if link.startswith("/"):
        return link.split("?", 1)[0].split("#", 1)[0]
    return safe_url(link)


def redact_urls_in_text(text: str) -> str:
    """Scrub credential-bearing links out of free-form text such as exception messages.

    A full URL keeps its scheme, host and path, and the path a failed request prints after
    ``url: `` keeps everything before its query.
    """
    return _LINK_IN_TEXT_PATTERN.sub(lambda match: _link_without_query(match.group(0)), text)


def strip_links(text: str) -> str:
    """Replace every link in text with a marker, keeping the rest of the text.

    A link is a full http(s) URL, or the scheme-less path after ``url: `` that requests
    prints for a failed connection. The link can carry an indexer key or a tracker passkey in
    its query string or path, and a non-admin has no use for it.
    """
    return _LINK_IN_TEXT_PATTERN.sub(_REMOVED_LINK, text)
