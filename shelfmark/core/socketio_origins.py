"""Origin policy for the Socket.IO handshake."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


def _first(value: str) -> str:
    return value.split(",")[0].strip()


def _own_origins(environ: dict[str, Any]) -> set[str]:
    """Origins python-engineio treats as same-origin when no list is configured."""
    origins: set[str] = set()
    scheme = environ.get("wsgi.url_scheme")
    host = environ.get("HTTP_HOST")
    if scheme and host:
        origins.add(f"{scheme}://{host}")
        if "HTTP_X_FORWARDED_PROTO" in environ or "HTTP_X_FORWARDED_HOST" in environ:
            fwd_scheme = _first(environ.get("HTTP_X_FORWARDED_PROTO", scheme))
            fwd_host = _first(environ.get("HTTP_X_FORWARDED_HOST", host))
            origins.add(f"{fwd_scheme}://{fwd_host}")
    return origins


def build_cors_allowed_origins(
    extra_origins: list[str],
) -> None | str | Callable[[str | None, dict[str, Any]], bool]:
    """Value for Flask-SocketIO's ``cors_allowed_origins``.

    With no extras this is ``None``, the library's own same-origin check. With
    extras the same check applies and the listed origins are accepted as well.
    """
    if not extra_origins:
        return None
    if "*" in extra_origins:
        return "*"
    extras = {origin.lower() for origin in extra_origins}

    def is_allowed(origin: str | None, environ: dict[str, Any]) -> bool:
        if not origin:
            return True
        return origin in _own_origins(environ) or origin.lower() in extras

    return is_allowed
