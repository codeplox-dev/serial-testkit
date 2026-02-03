"""Protocol definitions for serial-testkit.

Contains:
- MsgType enum for peering protocol message types
- SerialPort Protocol for type checking
- Timing constants for handshake and shutdown
- Logging configuration
"""

import logging
import os
from enum import IntEnum
from typing import Protocol

# TRACE logging level (below DEBUG)
TRACE = 5
logging.addLevelName(TRACE, "TRACE")

# Progress logging interval (configurable via envvar)
LOG_PROGRESS_INTERVAL = int(os.environ.get("SERIAL_LOG_INTERVAL", "100"))


class MsgType(IntEnum):
    """Message types for the peering protocol."""

    SYN = 0x01
    SYN_ACK = 0x02
    ACK = 0x03
    DATA = 0x10
    FIN = 0x20
    FIN_ACK = 0x21


class SerialPort(Protocol):
    """Protocol for serial port operations needed by peering."""

    def write(self, data: bytes, /) -> int | None: ...
    def read(self, size: int = ..., /) -> bytes: ...
    @property
    def in_waiting(self) -> int: ...


# Connection ID size in bytes
CONN_ID_SIZE = 4

# Default timing constants
DEFAULT_CLIENT_TIMEOUT_S = 60.0  # Server waits this long for client
DEFAULT_SYN_INTERVAL_S = 2.0  # Client sends SYN at this interval
DEFAULT_ACK_TIMEOUT_S = 10.0  # Wait for ACK after SYN_ACK sent
FIN_WAIT_TIMEOUT_S = 5.0  # Wait for FIN_ACK before force close
