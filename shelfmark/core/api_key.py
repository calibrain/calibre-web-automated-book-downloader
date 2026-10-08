"""Static API-key authentication backed by the SHELFMARK_API_KEY environment variable.

When ``SHELFMARK_API_KEY`` is set, a request carrying that value as a Bearer token or in
``X-Api-Key`` is treated as an admin for that request only. Both headers are
checked, since a reverse proxy in front of Shelfmark may set its own
``Authorization`` header, which would otherwise shadow an operator-supplied
``X-Api-Key``. A candidate that matches neither is ignored so that bearer
tokens forwarded by reverse proxies keep working. The key is never logged.
"""

from __future__ import annotations

import hmac

from shelfmark.config.env import SHELFMARK_API_KEY, SHELFMARK_API_KEY_READONLY


def extract_api_key_candidates(
    authorization_header: str | None, api_key_header: str | None
) -> list[str]:
    """Return the non-empty credentials a client presented, Bearer token first."""
    candidates: list[str] = []
    if authorization_header:
        scheme, _, token = authorization_header.strip().partition(" ")
        token = token.strip()
        if scheme.lower() == "bearer" and token:
            candidates.append(token)
    if api_key_header:
        token = api_key_header.strip()
        if token:
            candidates.append(token)
    return candidates


def matches_api_key(candidate: str) -> bool:
    """Constant-time comparison against the configured key. False when unset."""
    if not SHELFMARK_API_KEY or not candidate:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), SHELFMARK_API_KEY.encode("utf-8"))


def key_scope(candidate: str) -> str | None:
    """``"admin"``, ``"readonly"`` or None for a presented credential.

    The read-only key (``SHELFMARK_API_KEY_READONLY``) is for dashboards: it reaches
    ``/api/stats`` and nothing else. Admin wins if both are set to the same value.
    """
    if not candidate:
        return None
    if matches_api_key(candidate):
        return "admin"
    if SHELFMARK_API_KEY_READONLY and hmac.compare_digest(
        candidate.encode("utf-8"), SHELFMARK_API_KEY_READONLY.encode("utf-8")
    ):
        return "readonly"
    return None
