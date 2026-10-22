"""Webshare.cz XML API client: login, search and download-link resolution.

Every endpoint is ``POST https://webshare.cz/api/<name>/`` with a form-encoded body and
an XML ``<response>`` whose ``<status>`` is ``OK`` or ``FATAL``. Authenticated calls carry
the session token as the ``wst`` form field. Login is a two-step salted exchange: fetch
the user's salt, then send ``SHA1(MD5_CRYPT(password, salt))`` plus an MD5 digest.

All shelfmark-stateful behaviour (config, task naming) lives in source.py/handler.py.
"""

import hashlib
import hmac
import threading
from dataclasses import dataclass
from typing import Any

import requests
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

from shelfmark.core.logger import setup_logger
from shelfmark.download import http as downloader
from shelfmark.download import network

logger = setup_logger(__name__)

API_BASE = "https://webshare.cz/api"
FILE_PAGE_BASE = "https://webshare.cz/#/file"
DEFAULT_TIMEOUT = (5, 15)

# Webshare's API answers with an empty/HTML body unless the request looks like the
# site's own XHR calls.
_API_HEADERS = {
    **downloader.DOWNLOAD_HEADERS,
    "Accept": "text/xml; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
}


class WebshareError(Exception):
    """A Webshare API call failed (network error, bad XML or a FATAL status)."""

    def __init__(self, message: str, code: str | None = None) -> None:
        """Keep the Webshare error code (e.g. ``LOGIN_FATAL_1``) next to the message."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WebshareFile:
    """One file from a Webshare search result."""

    ident: str
    name: str
    type: str | None
    size_bytes: int | None
    positive_votes: int
    negative_votes: int
    password_protected: bool

    @property
    def info_url(self) -> str:
        """Public Webshare page for this file."""
        return f"{FILE_PAGE_BASE}/{self.ident}"


@dataclass(frozen=True)
class WebshareAccount:
    """The logged-in account's identity and VIP state."""

    username: str
    vip: bool
    vip_days: int | None


# --- md5-crypt ---

_ITOA64 = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def _to64(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ITOA64[value & 0x3F])
        value >>= 6
    return "".join(chars)


def md5_crypt(password: str, salt: str, magic: str = "$1$") -> str:
    """FreeBSD md5-crypt (``$1$salt$hash``), the scheme Webshare salts passwords with.

    Python dropped the ``crypt`` module in 3.13, so this is a straight port of the
    reference algorithm (the same one ``openssl passwd -1`` implements). MD5 here is
    dictated by Webshare's login protocol, not chosen to protect anything at rest, hence
    ``usedforsecurity=False`` throughout this module.
    """
    salt = salt.removeprefix(magic)
    salt = salt.split("$", 1)[0][:8]

    pw = password.encode()
    salt_b = salt.encode()
    magic_b = magic.encode()

    alternate = hashlib.md5(pw + salt_b + pw, usedforsecurity=False).digest()
    ctx = pw + magic_b + salt_b
    for remaining in range(len(pw), 0, -16):
        ctx += alternate[: min(16, remaining)]
    length = len(pw)
    while length:
        ctx += b"\x00" if length & 1 else pw[:1]
        length >>= 1
    final = hashlib.md5(ctx, usedforsecurity=False).digest()

    for i in range(1000):
        round_ctx = pw if i & 1 else final
        if i % 3:
            round_ctx += salt_b
        if i % 7:
            round_ctx += pw
        round_ctx += final if i & 1 else pw
        final = hashlib.md5(round_ctx, usedforsecurity=False).digest()

    encoded = "".join(
        _to64((final[a] << 16) | (final[b] << 8) | final[c], 4)
        for a, b, c in ((0, 6, 12), (1, 7, 13), (2, 8, 14), (3, 9, 15), (4, 10, 5))
    )
    encoded += _to64(final[11], 2)
    return f"{magic}{salt}${encoded}"


def hash_password(username: str, password: str, salt: str) -> tuple[str, str]:
    """Return the ``(password, digest)`` pair Webshare's login endpoint expects."""
    crypted = md5_crypt(password, salt).encode()
    password_hash = hashlib.sha1(crypted, usedforsecurity=False).hexdigest()
    digest_input = f"{username}:Webshare:{password_hash}".encode()
    digest = hashlib.md5(digest_input, usedforsecurity=False).hexdigest()
    return password_hash, digest


# --- transport ---


def _text(node: Any, tag: str) -> str | None:
    child = node.find(tag)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _int(node: Any, tag: str) -> int | None:
    value = _text(node, tag)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _post(
    endpoint: str,
    data: dict[str, Any],
    timeout: tuple[int, int] = DEFAULT_TIMEOUT,
) -> Any:
    """POST to an API endpoint and return the parsed ``<response>`` root on OK."""
    url = f"{API_BASE}/{endpoint}/"
    try:
        response = requests.post(
            url,
            data=data,
            headers=_API_HEADERS,
            timeout=timeout,
            proxies=network.get_proxies(url),
            verify=network.get_ssl_verify(url),
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as exc:
        msg = f"Webshare request failed: {exc}"
        raise WebshareError(msg) from exc

    try:
        root = DefusedElementTree.fromstring(response.content)
    except DefusedElementTree.ParseError, DefusedXmlException:
        msg = f"Webshare returned an unreadable response for {endpoint}"
        raise WebshareError(msg) from None

    if _text(root, "status") != "OK":
        code = _text(root, "code")
        message = _text(root, "message") or f"Webshare {endpoint} failed"
        raise WebshareError(message, code)
    return root


# --- client ---


class WebshareClient:
    """Session-aware Webshare client.

    The login token is cached per username at module level so searches and
    downloads share one session instead of logging in for every request. A call that
    fails while authenticated drops the token and retries once with a fresh login, which
    covers tokens Webshare expired server-side. A cached token is only reused while the
    password it was obtained with is unchanged, so editing the password forces a login.
    """

    _token_lock = threading.Lock()
    _tokens: dict[str, tuple[str, str]] = {}  # noqa: RUF012 - username -> (password, token)

    def __init__(self, username: str, password: str) -> None:
        """Bind the client to one Webshare account."""
        self.username = username
        self.password = password

    def login(self) -> str:
        """Log in and return a fresh session token (WST)."""
        if not self.username or not self.password:
            msg = "Webshare username and password are required"
            raise WebshareError(msg)

        salt_root = _post("salt", {"username_or_email": self.username})
        salt = _text(salt_root, "salt")
        if not salt:
            msg = "Webshare did not return a password salt"
            raise WebshareError(msg)

        password_hash, digest = hash_password(self.username, self.password, salt)
        login_root = _post(
            "login",
            {
                "username_or_email": self.username,
                "password": password_hash,
                "digest": digest,
                "keep_logged_in": 1,
            },
        )
        token = _text(login_root, "token")
        if not token:
            msg = "Webshare login did not return a session token"
            raise WebshareError(msg)

        with self._token_lock:
            self._tokens[self.username] = (self.password, token)
        return token

    def _token(self) -> str:
        with self._token_lock:
            cached = self._tokens.get(self.username)
        if cached and hmac.compare_digest(cached[0].encode(), self.password.encode()):
            return cached[1]
        return self.login()

    def _invalidate_token(self) -> None:
        with self._token_lock:
            self._tokens.pop(self.username, None)

    def _authed_post(
        self,
        endpoint: str,
        data: dict[str, Any],
        timeout: tuple[int, int] = DEFAULT_TIMEOUT,
    ) -> Any:
        try:
            return _post(endpoint, {**data, "wst": self._token()}, timeout)
        except WebshareError as exc:
            # Errors that are about the file itself won't change after re-login.
            if exc.code and exc.code.startswith(("FILE_LINK_FATAL", "FILE_INFO_FATAL")):
                raise
            logger.debug("Webshare %s failed (%s), retrying with a fresh login", endpoint, exc)
            self._invalidate_token()
            return _post(endpoint, {**data, "wst": self.login()}, timeout)

    def account(self) -> WebshareAccount:
        """Return the logged-in account's username and VIP state."""
        root = self._authed_post("user_data", {})
        return WebshareAccount(
            username=_text(root, "username") or self.username,
            vip=_text(root, "vip") == "1",
            vip_days=_int(root, "vip_days"),
        )

    def search(
        self,
        query: str,
        *,
        category: str = "",
        limit: int = 50,
        sort: str = "",
        timeout: tuple[int, int] = DEFAULT_TIMEOUT,
    ) -> list[WebshareFile]:
        """Search Webshare by file name."""
        root = self._authed_post(
            "search",
            {
                "what": query,
                "category": category,
                "sort": sort,
                "limit": limit,
                "offset": 0,
            },
            timeout,
        )
        files: list[WebshareFile] = []
        for node in root.findall("file"):
            ident = _text(node, "ident")
            name = _text(node, "name")
            if not ident or not name:
                continue
            files.append(
                WebshareFile(
                    ident=ident,
                    name=name,
                    type=(_text(node, "type") or "").lower() or None,
                    size_bytes=_int(node, "size"),
                    positive_votes=_int(node, "positive_votes") or 0,
                    negative_votes=_int(node, "negative_votes") or 0,
                    password_protected=_text(node, "password") == "1",
                )
            )
        return files

    def file_link(self, ident: str) -> str:
        """Resolve a file ident to a time-limited direct download URL."""
        root = self._authed_post(
            "file_link",
            {"ident": ident, "download_type": "file_download", "force_https": 1},
        )
        link = _text(root, "link")
        if not link:
            msg = "Webshare did not return a download link"
            raise WebshareError(msg)
        return link
