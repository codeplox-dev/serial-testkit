"""Serial I/O helpers for serial-testkit.

Contains:
- drain_input: Clear stale data from input buffer
- send_data: Send DATA message with connection ID
- recv_data: Receive DATA message, filtering by connection ID
"""

import logging

from common.connection import Connection, ConnectionMismatchError, UnexpectedMessageError
from common.encoding import decode_message, encode_data
from common.protocol import MsgType, SerialPort

logger = logging.getLogger(__name__)


def drain_input(port: SerialPort) -> int:
    """Drain stale data from input buffer. Returns bytes drained."""
    count = port.in_waiting
    if count > 0:
        port.read(count)
        logger.debug(f"Drained {count} stale bytes from input buffer")
    return count


def send_data(port: SerialPort, conn: Connection, payload: bytes) -> int | None:
    """Send a DATA message. Returns bytes written."""
    return port.write(encode_data(conn.connection_id, payload))


def recv_data(
    port: SerialPort, conn: Connection
) -> tuple[bytes, bool, MsgType]:
    """Receive a DATA message, filtering by connection ID.

    Returns (payload, crc_ok, msg_type):
    - (payload, True/False, DATA) for data messages with matching conn_id
    - (b"", False, FIN) if FIN received with matching conn_id

    Raises:
        TransportError: On timeout or truncated message.
        EncodingError: On invalid message format.
        ConnectionMismatchError: If message has wrong connection ID.
        UnexpectedMessageError: If message type is not DATA or FIN.
    """
    msg_type, recv_id, data, crc_ok = decode_message(port)

    if recv_id != conn.connection_id:
        raise ConnectionMismatchError(
            f"Expected conn_id={conn.connection_id.hex()}, got {recv_id.hex()}"
        )

    match msg_type:
        case MsgType.DATA:
            return data, crc_ok, MsgType.DATA
        case MsgType.FIN:
            return b"", False, MsgType.FIN
        case _:
            raise UnexpectedMessageError(f"Expected DATA or FIN, got {msg_type.name}")
