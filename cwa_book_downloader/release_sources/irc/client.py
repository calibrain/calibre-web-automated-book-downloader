"""IRC client implementation using raw sockets.

Minimal IRC client for IRC Highway ebook searches.
"""

import random
import re
import socket
import ssl
import string
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Iterator, Optional

from cwa_book_downloader.core.logger import setup_logger

from .dcc import DCCOffer, parse_dcc_send

logger = setup_logger(__name__)


# IRC Highway server details
DEFAULT_SERVER = "irc.irchighway.net"
DEFAULT_PORT_TLS = 6697
DEFAULT_PORT_PLAIN = 6667
DEFAULT_CHANNEL = "ebooks"

# Timing
POST_CONNECT_DELAY = 2.0  # Seconds to wait after connect before joining
SOCKET_TIMEOUT = 300.0    # 5 minutes - long because we wait for DCC offers
RECV_BUFFER = 4096

# IRC channel user prefixes that indicate elevated status (ops, voice, etc.)
# These are the download bots/servers
ELEVATED_PREFIXES = frozenset({'~', '&', '@', '%', '+'})


class IRCEvent(Enum):
    """Events detected from IRC messages."""
    MESSAGE = auto()           # Generic message
    SEARCH_RESULT = auto()     # DCC SEND with "_results_for"
    BOOK_RESULT = auto()       # DCC SEND for actual book
    NO_RESULTS = auto()        # "Sorry" notice
    BAD_SERVER = auto()        # "try another server" notice
    SEARCH_ACCEPTED = auto()   # "has been accepted" notice
    MATCHES_FOUND = auto()     # "X matches" notice
    SERVER_LIST = auto()       # User list (353/366)
    PING = auto()              # Server PING
    VERSION = auto()           # CTCP VERSION request


@dataclass
class IRCMessage:
    """Parsed IRC message."""
    raw: str
    prefix: Optional[str] = None
    command: str = ""
    params: list[str] = field(default_factory=list)
    trailing: Optional[str] = None
    event: IRCEvent = IRCEvent.MESSAGE


class IRCError(Exception):
    """Base IRC error."""
    pass


class IRCConnectionError(IRCError):
    """Connection failed."""
    pass


class IRCClient:
    """Minimal IRC client for IRC Highway ebook searches.

    Designed for per-request connections - connect, do operation, disconnect.
    Not intended for long-lived connections.

    Usage:
        client = IRCClient(nick="mybot")
        client.connect()
        client.join_channel("ebooks")
        client.send_message("#ebooks", "@search harry potter")

        for msg in client.read_messages():
            if msg.event == IRCEvent.SEARCH_RESULT:
                offer = parse_dcc_send(msg.raw)
                break

        client.disconnect()
    """

    def __init__(
        self,
        nick: Optional[str] = None,
        server: str = DEFAULT_SERVER,
        port: Optional[int] = None,
        use_tls: bool = True,
        version: str = "CWA-BD 1.0",
    ):
        self.nick = nick or self._generate_nick()
        self.server = server
        self.port = port or (DEFAULT_PORT_TLS if use_tls else DEFAULT_PORT_PLAIN)
        self.use_tls = use_tls
        self.version = version

        self._socket: Optional[socket.socket] = None
        self._buffer = ""
        self._connected = False

        # Track online servers (elevated users in channel)
        self.online_servers: set[str] = set()

    @staticmethod
    def _generate_nick() -> str:
        """Generate random nickname like 'cwa_abc123'."""
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"cwa_{suffix}"

    def connect(self) -> None:
        """Connect to IRC server and authenticate.

        Connection sequence:
        1. TCP/TLS connect
        2. Send USER and NICK
        3. Wait for server welcome messages

        Raises:
            IRCConnectionError: If connection fails
        """
        logger.info(f"Connecting to {self.server}:{self.port} (TLS={self.use_tls})")

        try:
            # Create socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(SOCKET_TIMEOUT)

            # Wrap with TLS if needed
            if self.use_tls:
                context = ssl.create_default_context()
                # Skip verification for self-signed certs common on IRC servers
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                sock = context.wrap_socket(sock, server_hostname=self.server)

            sock.connect((self.server, self.port))
            self._socket = sock

        except socket.error as e:
            raise IRCConnectionError(f"Failed to connect: {e}")

        # Send authentication (USER before NICK per IRC protocol)
        self._send(f"USER {self.nick} 0 * :{self.nick}")
        self._send(f"NICK {self.nick}")

        # Wait for server to process welcome messages
        logger.debug(f"Waiting {POST_CONNECT_DELAY}s for server welcome")
        time.sleep(POST_CONNECT_DELAY)

        self._connected = True
        logger.info(f"Connected as {self.nick}")

    def disconnect(self) -> None:
        """Gracefully disconnect from server."""
        if self._socket:
            try:
                self._send("QUIT :Goodbye")
            except Exception:
                pass  # Best effort

            try:
                self._socket.close()
            except Exception:
                pass

            self._socket = None
            self._connected = False
            logger.info("Disconnected from IRC")

    def join_channel(self, channel: str, wait_for_join: bool = True) -> None:
        """Join an IRC channel.

        Args:
            channel: Channel name without # prefix
            wait_for_join: If True, wait for server confirmation (366 message)

        Also captures the channel's user list to track online servers.
        """
        self._send(f"JOIN #{channel}")
        logger.debug(f"Sent JOIN #{channel}")

        # Clear any existing server list before joining
        self.online_servers.clear()

        if wait_for_join:
            # Wait for end of NAMES list (366) which confirms we're in the channel
            start = time.time()
            timeout = 10.0  # 10 seconds should be plenty

            for line in self._recv_lines():
                if time.time() - start > timeout:
                    logger.warning(f"Timeout waiting for JOIN confirmation on #{channel}")
                    break

                msg = self._parse_message(line)

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
                    logger.info(f"Joined #{channel} - {len(self.online_servers)} servers online")
                    return

                # Check for errors (e.g., banned, channel doesn't exist)
                if msg.command in ("473", "474", "475", "403"):
                    logger.error(f"Cannot join #{channel}: {msg.trailing}")
                    return

            logger.warning(f"Joined #{channel} (no confirmation received)")

    def send_message(self, target: str, message: str) -> None:
        """Send a PRIVMSG to a channel or user.

        Args:
            target: Channel (with #) or user nick
            message: Message content
        """
        self._send(f"PRIVMSG {target} :{message}")
        logger.debug(f"Sent to {target}: {message[:50]}...")

    def send_notice(self, target: str, message: str) -> None:
        """Send a NOTICE to a user.

        Args:
            target: User nick
            message: Notice content
        """
        self._send(f"NOTICE {target} :{message}")

    def request_names(self, channel: str) -> None:
        """Request user list for a channel.

        Args:
            channel: Channel name without # prefix
        """
        self._send(f"NAMES #{channel}")

    def _parse_names_list(self, names_data: str) -> None:
        """Parse NAMES list and extract elevated users (download servers).

        IRC NAMES reply format (353):
        :server 353 nick = #channel :@user1 +user2 user3 ...

        Users with prefixes (~, &, @, %, +) are elevated (ops/voice).
        These are the download bots/servers.
        """
        # Extract the trailing part after the last colon (the actual names)
        if ' :' in names_data:
            names_part = names_data.split(' :')[-1]
        else:
            names_part = names_data

        for name in names_part.split():
            if not name:
                continue

            # Check if user has an elevated prefix
            if name[0] in ELEVATED_PREFIXES:
                # Strip the prefix to get the actual nick
                self.online_servers.add(name[1:])
            # Note: we only care about elevated users for server status

    def _send(self, message: str) -> None:
        """Send raw IRC message."""
        if not self._socket:
            raise IRCError("Not connected")

        data = f"{message}\r\n".encode('utf-8')
        self._socket.sendall(data)

    def _recv_lines(self) -> Iterator[str]:
        """Receive and yield complete IRC lines.

        IRC messages are delimited by \\r\\n. We buffer partial
        reads and yield complete lines as they arrive.
        """
        while True:
            # Check if we have a complete line in buffer
            while '\r\n' in self._buffer:
                line, self._buffer = self._buffer.split('\r\n', 1)
                if line:
                    yield line

            # Read more data
            try:
                data = self._socket.recv(RECV_BUFFER)
                if not data:
                    return  # Connection closed
                self._buffer += data.decode('utf-8', errors='replace')
            except socket.timeout:
                continue  # Keep waiting
            except socket.error as e:
                logger.warning(f"Socket error: {e}")
                return  # Connection error

    def _parse_message(self, line: str) -> IRCMessage:
        """Parse an IRC message line into components.

        Format: [:prefix] COMMAND [params] [:trailing]
        """
        msg = IRCMessage(raw=line)

        # Extract prefix if present
        if line.startswith(':'):
            space_idx = line.find(' ')
            if space_idx != -1:
                msg.prefix = line[1:space_idx]
                line = line[space_idx + 1:]

        # Extract trailing if present
        if ' :' in line:
            idx = line.find(' :')
            msg.trailing = line[idx + 2:]
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
        """Classify message into event type.

        Uses simple string containment checks for robustness
        rather than strict IRC protocol parsing.
        """
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
        if msg.command == "353":
            return IRCEvent.SERVER_LIST
        if msg.command == "366":
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
        logger.debug(f"PONG {server}")

    def _handle_version(self, msg: IRCMessage) -> None:
        """Respond to CTCP VERSION request."""
        if msg.prefix:
            # Extract nick from prefix (nick!user@host)
            sender = msg.prefix.split('!')[0]
            self.send_notice(sender, f"\x01VERSION {self.version}\x01")
            logger.debug(f"Sent VERSION to {sender}")

    def read_messages(self, auto_handle: bool = True) -> Iterator[IRCMessage]:
        """Read and yield IRC messages.

        Args:
            auto_handle: If True, automatically handle PING and VERSION

        Yields:
            IRCMessage objects for each received message
        """
        for line in self._recv_lines():
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
        result_type: bool = False,
    ) -> Optional[DCCOffer]:
        """Wait for a DCC SEND offer.

        Args:
            timeout: Maximum time to wait in seconds
            result_type: If True, wait for SEARCH_RESULT; else BOOK_RESULT

        Returns:
            DCCOffer if received, None if timeout
        """
        target_event = IRCEvent.SEARCH_RESULT if result_type else IRCEvent.BOOK_RESULT
        start = time.time()

        for msg in self.read_messages():
            if time.time() - start > timeout:
                logger.warning("Timeout waiting for DCC offer")
                return None

            if msg.event == target_event:
                try:
                    offer = parse_dcc_send(msg.raw)
                    logger.info(f"Received DCC offer: {offer.filename}")
                    return offer
                except Exception as e:
                    logger.error(f"Failed to parse DCC: {e}")
                    return None

            # Log other events for debugging
            if msg.event == IRCEvent.NO_RESULTS:
                logger.info("Server reports no results")
                return None
            if msg.event == IRCEvent.BAD_SERVER:
                logger.warning("Server unavailable")
                return None
            if msg.event == IRCEvent.SEARCH_ACCEPTED:
                logger.info("Search accepted, waiting for results...")
            if msg.event == IRCEvent.MATCHES_FOUND:
                # Extract count from "returned X matches"
                if msg.trailing and "returned" in msg.trailing:
                    try:
                        match = re.search(r'returned\s+(\d+)\s+matches', msg.trailing)
                        if match:
                            count = match.group(1)
                            logger.info(f"Found {count} matches")
                    except Exception:
                        pass

        return None

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._connected and self._socket is not None

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()
