"""Common modules for serial-testkit.

This package contains shared code used by both client and server:
- protocol: MsgType enum, timing constants, SerialPort Protocol
- connection: SessionParams, Connection dataclasses
- message: Wire format encoding/decoding
- encoding: Peering message encoding/decoding
- io: Serial I/O helpers (drain_input, send_data, recv_data)
- device: Serial device setup and FTDI configuration
- report: Reporting abstractions
"""

from common.connection import (
    Connection,
    ConnectionMismatchError,
    PeeringError,
    SessionParams,
    UnexpectedMessageError,
)
from common.encoding import EncodingError, TransportError
from common.protocol import (
    CONN_ID_SIZE,
    DEFAULT_ACK_TIMEOUT_S,
    DEFAULT_CLIENT_TIMEOUT_S,
    DEFAULT_SYN_INTERVAL_S,
    FIN_WAIT_TIMEOUT_S,
    MsgType,
    SerialPort,
)

__all__ = [
    # Protocol
    "MsgType",
    "SerialPort",
    "CONN_ID_SIZE",
    "DEFAULT_CLIENT_TIMEOUT_S",
    "DEFAULT_SYN_INTERVAL_S",
    "DEFAULT_ACK_TIMEOUT_S",
    "FIN_WAIT_TIMEOUT_S",
    # Connection
    "SessionParams",
    "Connection",
    # Exceptions
    "ConnectionMismatchError",
    "EncodingError",
    "PeeringError",
    "TransportError",
    "UnexpectedMessageError",
]
