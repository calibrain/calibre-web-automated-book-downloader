"""IRC client implementation using raw sockets.

Minimal IRC client for Shelfmark release searches.
"""

import re
import socket
import ssl
import time
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Self

from shelfmark.core.logger import setup_logger

from .dcc import DCCError, DCCOffer, parse_dcc_send, validate_dcc_endpoint

if TYPE_CHECKING:
    from collections.abc import Iterator

logger = setup_logger(__name__)


# Timing
SOCKET_TIMEOUT = 300.0  # 5 minutes - long because we wait for DCC offers
RECV_BUFFER = 4096
# How often a deadline-bound read wakes up to re-check the clock
POLL_INTERVAL = 2.0

# IRC channel user prefixes that indicate elevated status (ops, voice, etc.)
# These are the download bots/servers
ELEVATED_PREFIXES = frozenset({"~", "&", "@", "%", "+"})


class IRCEvent(Enum):
    """Events detected from IRC messages."""

    MESSAGE = auto()  # Generic message
    SEARCH_RESULT = auto()  # DCC SEND with "_results_for"
    BOOK_RESULT = auto()  # DCC SEND for actual book
    NO_RESULTS = auto()  # "Sorry" notice
    BAD_SERVER = auto()  # "try another server" notice
    SEARCH_ACCEPTED = auto()  # "has been accepted" notice
    MATCHES_FOUND = auto()  # "X matches" notice
    SERVER_LIST = auto()  # User list (353/366)
    PING = auto()  # Server PING
    VERSION = auto()  # CTCP VERSION request


@dataclass
class IRCMessage:
    """Parsed IRC message."""

    raw: str
    prefix: str | None = None
    command: str = ""
    params: list[str] = field(default_factory=list)
    trailing: str | None = None
    event: IRCEvent = IRCEvent.MESSAGE


class IRCError(Exception):
    """Base IRC error."""


def has_control_characters(value: str) -> bool:
    """True if value holds CR, LF, NUL or another control character.

    The CTCP delimiter (0x01) is allowed because CTCP replies are framed with it.
    """
    return any((ord(char) < 0x20 and char != "\x01") or ord(char) == 0x7F for char in value)


class IRCConnectionError(IRCError):
    """Connection failed."""


class IRCClient:
    """Minimal IRC client for per-request IRC release searches."""

    def __init__(
        self,
        nick: str,
        server: str,
        port: int,
        *,
        use_tls: bool = True,
        version: str = "Shelfmark 1.0",
    ) -> None:
        """Initialize the IRC client with connection settings and defaults."""
        if not nick:
            msg = "IRC nickname is required"
            raise IRCError(msg)
        if not server:
            msg = "IRC server is required"
            raise IRCError(msg)
        if not port:
            msg = "IRC port is required"
            raise IRCError(msg)
        self.nick = nick
        self.server = server
        self.port = port
        self.use_tls = use_tls
        self.version = version

        self._socket: socket.socket | None = None
        self._buffer = ""
        self._connected = False

        # Track online servers (elevated users in channel)
        self.online_servers: set[str] = set()

    def _require_socket(self) -> socket.socket:
        """Return the active socket or raise when the client is disconnected."""
        sock = self._socket
        if sock is None:
            msg = "Not connected"
            raise IRCError(msg)
        return sock

    def connect(self) -> None:
        """Connect to IRC server, send USER/NICK, and wait for welcome."""
        logger.info("Connecting to %s:%s (TLS=%s)", self.server, self.port, self.use_tls)

        try:
            # Create socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SOCKET_TIMEOUT)

            # Wrap with TLS if needed
            if self.use_tls:
                context = ssl.create_default_context()
                # Python already defaults to TLS 1.2; pin it so an OpenSSL config cannot lower it
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                # Skip verification for self-signed certs common on IRC servers
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                sock = context.wrap_socket(sock, server_hostname=self.server)

            sock.connect((self.server, self.port))
            self._socket = sock

        except OSError as e:
            msg = f"Failed to connect: {e}"
            raise IRCConnectionError(msg) from e

        # Send authentication (USER before NICK per IRC protocol)
        self._send(f"USER {self.nick} 0 * :{self.nick}")
        self._send(f"NICK {self.nick}")

        # Wait for 001 (RPL_WELCOME) which confirms registration is complete
        # Server may take time for hostname lookup, ident check, etc.
        logger.debug("Waiting for server welcome (001)...")
        sock.settimeout(2.0)  # Short timeout for polling

        start = time.time()
        timeout = 30.0  # Max wait for registration

        while time.time() - start < timeout:
            try:
                data = sock.recv(RECV_BUFFER)
                if not data:
                    msg = "Connection closed during registration"
                    raise IRCConnectionError(msg)
                self._buffer += data.decode("utf-8", errors="replace")
            except TimeoutError:
                continue

            # Process lines looking for 001 or errors
            while "\r\n" in self._buffer:
                line, self._buffer = self._buffer.split("\r\n", 1)
                if not line:
                    continue

                # Handle PING during registration
                if line.startswith("PING"):
                    pong = line.replace("PING", "PONG", 1)
                    self._send(pong)
                    logger.debug("PONG %s", pong.split(":")[-1] if ":" in pong else "")
                    continue

                # 001 = RPL_WELCOME - registration complete
                if " 001 " in line:
                    sock.settimeout(SOCKET_TIMEOUT)  # Restore timeout
                    self._connected = True
                    logger.info("Connected as %s", self.nick)
                    return

                # Check for fatal errors
                if " 433 " in line:  # Nickname in use
                    msg = "Nickname already in use"
                    raise IRCConnectionError(msg)
                if " 432 " in line:  # Erroneous nickname
                    msg = "Invalid nickname"
                    raise IRCConnectionError(msg)

        msg = "Timeout waiting for server welcome"
        raise IRCConnectionError(msg)

    def disconnect(self) -> None:
        """Gracefully disconnect from server."""
        if self._socket:
            with suppress(Exception):
                self._send("QUIT :Goodbye")

            with suppress(Exception):
                self._socket.close()

            self._socket = None
            self._connected = False
            logger.info("Disconnected from IRC")

    def join_channel(self, channel: str, *, wait_for_join: bool = True) -> None:
        """Join an IRC channel (without # prefix) and capture online servers."""
        self._send(f"JOIN #{channel}")
        logger.debug("Sent JOIN #%s", channel)

        # Clear any existing server list before joining
        self.online_servers.clear()

        if wait_for_join:
            sock = self._require_socket()
            # Use a short socket timeout during join so we can check elapsed time
            original_timeout = sock.gettimeout()
            sock.settimeout(2.0)  # 2 second recv timeout

            try:
                start = time.time()
                timeout = 15.0  # Total wait time for join

                while time.time() - start < timeout:
                    # Read data with short timeout
                    try:
                        data = sock.recv(RECV_BUFFER)
                        if not data:
                            break
                        self._buffer += data.decode("utf-8", errors="replace")
                    except TimeoutError:
                        continue  # No data yet, check time and retry

                    # Process any complete lines in buffer
                    while "\r\n" in self._buffer:
                        line, self._buffer = self._buffer.split("\r\n", 1)
                        if not line:
                            continue

                        msg = self._parse_message(line)
                        logger.debug("JOIN wait recv: %s - %s", msg.command, line[:80])

                        # Handle PING during join wait
                        if msg.event == IRCEvent.PING:
                            self._handle_ping(msg)
                            continue

                        # 353 = RPL_NAMREPLY - parse the names list
                        if msg.command == "353":
                            self._parse_names_list(msg.raw)
                            continue

                        # 366 = RPL_ENDOFNAMES - channel join is complete
                        if msg.command == "366":
                            if not self.online_servers:
                                # Joining a channel that doesn't exist on this network
                                # silently creates an empty one, so an empty name list is
                                # the only hint that the channel name is wrong.
                                logger.warning(
                                    "Joined #%s but no servers are online - the channel may "
                                    "be empty or not exist on %s",
                                    channel,
                                    self.server,
                                )
                            else:
                                logger.info(
                                    "Joined #%s - %s servers online",
                                    channel,
                                    len(self.online_servers),
                                )
                            return

                        # Check for errors (e.g., banned, channel doesn't exist)
                        if msg.command in ("473", "474", "475", "403"):
                            logger.error("Cannot join #%s: %s", channel, msg.trailing)
                            return

                logger.warning("Timeout waiting for JOIN confirmation on #%s", channel)

            finally:
                # Restore original socket timeout
                sock.settimeout(original_timeout)

    def send_message(self, target: str, message: str) -> None:
        """Send a PRIVMSG to a channel or user."""
        self._send(f"PRIVMSG {target} :{message}")
        logger.debug("Sent to %s: %s...", target, message[:50])

    def send_notice(self, target: str, message: str) -> None:
        """Send a NOTICE to a user."""
        self._send(f"NOTICE {target} :{message}")

    def _parse_names_list(self, names_data: str) -> None:
        """Parse 353 NAMES reply and extract elevated users (download servers)."""
        # Extract the trailing part after the last colon (the actual names)
        names_part = names_data.rsplit(" :", maxsplit=1)[-1] if " :" in names_data else names_data

        for name in names_part.split():
            # Check if user has an elevated prefix
            if name[0] in ELEVATED_PREFIXES:
                # Strip the prefix to get the actual nick
                self.online_servers.add(name[1:])
            # Note: we only care about elevated users for server status

    def _send(self, message: str) -> None:
        """Send raw IRC message."""
        if not self._socket:
            msg = "Not connected"
            raise IRCError(msg)

        # One write must be one command: a line break would smuggle in a second one
        if has_control_characters(message):
            msg = "Refusing to send IRC line containing control characters"
            raise IRCError(msg)

        data = f"{message}\r\n".encode()
        self._socket.sendall(data)

    def _recv_lines(self, deadline: float | None = None) -> Iterator[str]:
        """Receive and yield complete CRLF-delimited IRC lines.

        A deadline stops the read once it passes, even if nothing ever arrives.
        Callers time out by watching the messages they receive, so on a channel
        with no traffic at all there is nothing to watch: the recv would just
        keep blocking for SOCKET_TIMEOUT and retrying forever.
        """
        sock = self._require_socket()
        original_timeout = sock.gettimeout()

        try:
            while True:
                # Check if we have a complete line in buffer
                while "\r\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\r\n", 1)
                    if line:
                        yield line

                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        return
                    # Wake up often enough to notice the deadline pass
                    sock.settimeout(min(remaining, POLL_INTERVAL))

                # Read more data
                try:
                    data = sock.recv(RECV_BUFFER)
                    if not data:
                        return  # Connection closed
                    self._buffer += data.decode("utf-8", errors="replace")
                except TimeoutError:
                    continue  # Keep waiting (the deadline is re-checked above)
                except OSError as e:
                    logger.warning("Socket error: %s", e)
                    return  # Connection error
        finally:
            if deadline is not None:
                with suppress(OSError):
                    sock.settimeout(original_timeout)

    def _parse_message(self, line: str) -> IRCMessage:
        """Parse an IRC message line into components.

        Format: [:prefix] COMMAND [params] [:trailing]
        """
        msg = IRCMessage(raw=line)

        # Extract prefix if present
        if line.startswith(":"):
            space_idx = line.find(" ")
            if space_idx != -1:
                msg.prefix = line[1:space_idx]
                line = line[space_idx + 1 :]

        # Extract trailing if present
        if " :" in line:
            idx = line.find(" :")
            msg.trailing = line[idx + 2 :]
            line = line[:idx]

        # Split remaining into command and params
        parts = line.split()
        if parts:
            msg.command = parts[0]
            msg.params = parts[1:]

        # Classify event type based on message content
        msg.event = self._classify_event(msg)

        return msg

    def _classify_event(self, msg: IRCMessage) -> IRCEvent:
        """Classify message into event type using string containment checks."""
        raw = msg.raw
        trailing = msg.trailing or ""

        # DCC SEND detection
        if "DCC SEND" in raw:
            if "_results_for" in raw:
                return IRCEvent.SEARCH_RESULT
            return IRCEvent.BOOK_RESULT

        # NOTICE messages
        if msg.command == "NOTICE" or "NOTICE" in raw:
            if "Sorry" in trailing:
                return IRCEvent.NO_RESULTS
            if "try another server" in trailing:
                return IRCEvent.BAD_SERVER
            if "has been accepted" in trailing:
                return IRCEvent.SEARCH_ACCEPTED
            if "matches" in trailing:
                return IRCEvent.MATCHES_FOUND

        # User list (RPL_NAMREPLY and RPL_ENDOFNAMES)
        if msg.command in ("353", "366"):
            return IRCEvent.SERVER_LIST

        # Server PING
        if msg.command == "PING":
            return IRCEvent.PING

        # CTCP VERSION
        if "\x01VERSION\x01" in raw:
            return IRCEvent.VERSION

        return IRCEvent.MESSAGE

    def _handle_ping(self, msg: IRCMessage) -> None:
        """Respond to server PING with PONG."""
        # PING message format: PING :server
        server = msg.trailing or self.server
        self._send(f"PONG :{server}")
        logger.debug("PONG %s", server)

    def _handle_version(self, msg: IRCMessage) -> None:
        """Respond to CTCP VERSION request."""
        if msg.prefix:
            # Extract nick from prefix (nick!user@host)
            sender = msg.prefix.split("!")[0]
            self.send_notice(sender, f"\x01VERSION {self.version}\x01")
            logger.debug("Sent VERSION to %s", sender)

    @staticmethod
    def _sender_nick(msg: IRCMessage) -> str | None:
        """Extract the nick from a message prefix."""
        if not msg.prefix:
            return None
        return msg.prefix.split("!", maxsplit=1)[0]

    def _is_allowed_dcc_sender(
        self,
        msg: IRCMessage,
        expected_senders: set[str] | None,
    ) -> bool:
        allowed_senders = expected_senders or self.online_servers
        if not allowed_senders:
            return True

        sender = self._sender_nick(msg)
        if sender is None:
            logger.warning("Ignoring DCC offer without sender prefix")
            return False

        normalized_allowed = {nick.casefold() for nick in allowed_senders}
        if sender.casefold() not in normalized_allowed:
            logger.warning("Ignoring DCC offer from unexpected sender: %s", sender)
            return False
        return True

    def read_messages(
        self,
        *,
        auto_handle: bool = True,
        deadline: float | None = None,
    ) -> Iterator[IRCMessage]:
        """Read and yield IRC messages, optionally auto-handling PING/VERSION."""
        for line in self._recv_lines(deadline):
            msg = self._parse_message(line)

            # Auto-handle certain events
            if auto_handle:
                if msg.event == IRCEvent.PING:
                    self._handle_ping(msg)
                    continue  # Don't yield PING messages

                if msg.event == IRCEvent.VERSION:
                    self._handle_version(msg)
                    continue  # Don't yield VERSION messages

            yield msg

    def wait_for_dcc(
        self,
        timeout: float = 60.0,
        *,
        result_type: bool = False,
        expected_senders: set[str] | None = None,
    ) -> DCCOffer | None:
        """Wait for a DCC SEND offer. Returns None on timeout or no results."""
        target_event = IRCEvent.SEARCH_RESULT if result_type else IRCEvent.BOOK_RESULT
        deadline = time.time() + timeout

        for msg in self.read_messages(deadline=deadline):
            if msg.event == target_event:
                if not self._is_allowed_dcc_sender(msg, expected_senders):
                    continue
                try:
                    offer = parse_dcc_send(msg.raw)
                    validate_dcc_endpoint(offer)
                    logger.info("Received DCC offer: %s", offer.filename)
                except DCCError:
                    logger.exception("Rejected DCC offer")
                    continue
                else:
                    return offer

            # Log other events for debugging
            if msg.event == IRCEvent.NO_RESULTS:
                logger.info("Server reports no results")
                return None
            if msg.event == IRCEvent.BAD_SERVER:
                logger.warning("Server unavailable")
                return None
            if msg.event == IRCEvent.SEARCH_ACCEPTED:
                logger.info("Search accepted, waiting for results...")
            elif (
                msg.event == IRCEvent.MATCHES_FOUND and msg.trailing and "returned" in msg.trailing
            ):
                # Extract count from "returned X matches"
                match = re.search(r"returned\s+(\d+)\s+matches", msg.trailing)
                if match:
                    count = match.group(1)
                    logger.info("Found %s matches", count)

        if time.time() >= deadline:
            logger.warning("Timeout waiting for DCC offer")
        return None

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._connected and self._socket is not None

    def __enter__(self) -> Self:
        """Connect and return the IRC client for context-manager usage."""
        self.connect()
        return self

    def __exit__(self, *args: object) -> None:
        """Disconnect the IRC client when leaving a context manager."""
        self.disconnect()
