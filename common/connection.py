"""Connection state dataclasses for serial-testkit.

Contains:
- Role: Enum for client/server role
- PeeringError: Exception for peering failures
- SessionParams: Parameters sent from client to server during peering
- Connection: Established connection state
"""

from dataclasses import dataclass
from enum import Enum


class Role(Enum):
    """Role in the client/server protocol."""

    CLIENT = "client"
    SERVER = "server"


class PeeringError(Exception):
    """Raised when peering handshake fails."""

    pass


class ConnectionMismatchError(Exception):
    """Raised when received message has wrong connection ID."""

    pass


class UnexpectedMessageError(Exception):
    """Raised when received message type is unexpected in current context."""

    pass


@dataclass
class SessionParams:
    """Parameters for a test session, sent from client to server during peering."""

    msg_count: int  # Total messages (client sends half, server sends half)


@dataclass
class Connection:
    """Established connection state."""

    connection_id: bytes  # 4 bytes
    role: Role
    session_params: SessionParams | None = None  # Server receives from client
