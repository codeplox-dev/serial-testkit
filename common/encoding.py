"""Peering message encoding/decoding for serial-testkit.

Contains functions for encoding and decoding peering protocol messages:
- Control messages (SYN, SYN_ACK, ACK, FIN, FIN_ACK)
- Data messages with connection ID
- ACK messages with session parameters
"""

import os

from common import message
from common.connection import SessionParams
from common.protocol import CONN_ID_SIZE, MsgType, SerialPort


class EncodingError(Exception):
    """Raised when message decoding fails due to invalid message format."""

    pass


class TransportError(Exception):
    """Raised when message decoding fails due to transport issues (timeout, truncation)."""

    pass


def generate_connection_id() -> bytes:
    """Generate random 4-byte connection ID."""
    return os.urandom(CONN_ID_SIZE)


def encode_control(msg_type: MsgType, conn_id: bytes) -> bytes:
    """Encode a control message (SYN/SYN_ACK/ACK/FIN/FIN_ACK)."""
    payload = bytes([msg_type]) + conn_id
    return message.encode(payload)


def encode_ack_with_params(conn_id: bytes, session_params: SessionParams) -> bytes:
    """Encode ACK message with session parameters.

    ACK payload: [type=0x03][4-byte conn_id][4-byte msg_count]
    """
    payload = (
        bytes([MsgType.ACK])
        + conn_id
        + message.uint32_to_bytes(session_params.msg_count)
    )
    return message.encode(payload)


def decode_ack_with_params(payload: bytes) -> tuple[bytes, SessionParams]:
    """Decode ACK message, extracting required session params.

    Returns (conn_id, session_params) on success.
    Raises EncodingError if payload is invalid or missing session params.
    """
    # Minimum valid ACK: type (1) + conn_id (4) + msg_count (4) = 9 bytes
    if len(payload) < 1 + CONN_ID_SIZE + 4:
        raise EncodingError(f"ACK payload too short: {len(payload)} bytes, need at least {1 + CONN_ID_SIZE + 4}")

    conn_id = payload[1 : 1 + CONN_ID_SIZE]
    msg_count = message.uint32_from_bytes(
        payload[1 + CONN_ID_SIZE : 1 + CONN_ID_SIZE + 4]
    )
    return conn_id, SessionParams(msg_count=msg_count)


def encode_data(conn_id: bytes, data: bytes) -> bytes:
    """Encode a DATA message with connection ID and payload."""
    payload = bytes([MsgType.DATA]) + conn_id + data
    return message.encode(payload)


def decode_message(
    reader: SerialPort,
) -> tuple[MsgType, bytes, bytes, bool]:
    """Decode message from reader.

    Returns (msg_type, conn_id, data, crc_ok).

    Raises:
        TransportError: On timeout or truncated message.
        EncodingError: On invalid message format (bad MsgType, too short).
    """
    payload, crc_ok = message.decode(reader)
    if payload is None:
        raise TransportError("Timeout or truncated message")

    if len(payload) < 1 + CONN_ID_SIZE:
        raise EncodingError(f"Payload too short: {len(payload)} bytes, need at least {1 + CONN_ID_SIZE}")

    try:
        msg_type = MsgType(payload[0])
    except ValueError:
        raise EncodingError(f"Invalid message type: {payload[0]}")

    conn_id = payload[1 : 1 + CONN_ID_SIZE]
    data = payload[1 + CONN_ID_SIZE :] if len(payload) > 1 + CONN_ID_SIZE else b""
    return msg_type, conn_id, data, crc_ok
