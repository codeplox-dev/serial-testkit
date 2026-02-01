"""Client-side handshake functions for serial-testkit.

Implements client side of TCP-like 3-way handshake:
  1. Send SYN periodically until SYN_ACK received
  2. Send ACK with session params to complete handshake
"""

import logging
import time

from common.connection import Connection, PeeringError, Role, SessionParams
from common.encoding import (
    EncodingError,
    TransportError,
    decode_message,
    encode_ack_with_params,
    encode_control,
    generate_connection_id,
)
from common.io import drain_input
from common.protocol import (
    DEFAULT_CLIENT_TIMEOUT_S,
    DEFAULT_SYN_INTERVAL_S,
    MsgType,
    SerialPort,
)

logger = logging.getLogger(__name__)


class HandshakeError(PeeringError):
    """Raised when client handshake fails."""

    pass


def client_send_syn_wait_syn_ack(
    port: SerialPort,
    conn_id: bytes,
    timeout_s: float = DEFAULT_CLIENT_TIMEOUT_S,
    syn_interval_s: float = DEFAULT_SYN_INTERVAL_S,
) -> bool:
    """Send SYN periodically and wait for SYN_ACK.

    Returns True on success, raises HandshakeError on timeout.
    Note: Caller should drain_input() before calling if needed.
    """
    syn_msg = encode_control(MsgType.SYN, conn_id)
    start = time.monotonic()
    last_syn = 0.0

    logger.info(f"Client: initiating connection (id={conn_id.hex()})")

    while time.monotonic() - start < timeout_s:
        # Retransmit SYN periodically
        if time.monotonic() - last_syn > syn_interval_s:
            port.write(syn_msg)
            last_syn = time.monotonic()
            logger.debug("Client: sent SYN")

        try:
            msg_type, recv_id, _, crc_ok = decode_message(port)
        except (TransportError, EncodingError):
            continue

        if msg_type == MsgType.SYN_ACK and recv_id == conn_id and crc_ok:
            logger.info("Client: received SYN_ACK")
            return True

    raise HandshakeError(f"Client: timeout ({timeout_s}s) waiting for SYN_ACK")


def client_send_ack_with_params(
    port: SerialPort, conn_id: bytes, session_params: SessionParams
) -> None:
    """Send ACK with session parameters to complete handshake."""
    ack_msg = encode_ack_with_params(conn_id, session_params)
    port.write(ack_msg)
    logger.info(
        f"Client: sent ACK with session params (msg_count={session_params.msg_count}), "
        f"connection established (id={conn_id.hex()})"
    )


def client_handshake(
    port: SerialPort,
    timeout_s: float = DEFAULT_CLIENT_TIMEOUT_S,
    syn_interval_s: float = DEFAULT_SYN_INTERVAL_S,
    session_params: SessionParams | None = None,
) -> Connection:
    """Perform client-side 3-way handshake.

    1. Send SYN with proposed connection_id (every syn_interval_s)
    2. Wait for SYN_ACK with matching connection_id (up to timeout_s)
    3. Send ACK with session params to complete handshake

    Returns Connection on success.
    Raises HandshakeError (subclass of PeeringError) on failure.
    """
    # Clear any stale data from previous runs
    drain_input(port)

    conn_id = generate_connection_id()

    # Phase 1: Send SYN, wait for SYN_ACK (raises HandshakeError on timeout)
    client_send_syn_wait_syn_ack(
        port, conn_id, timeout_s=timeout_s, syn_interval_s=syn_interval_s
    )

    # Phase 2: Send ACK with session params
    if session_params is None:
        session_params = SessionParams(msg_count=100)  # Default

    client_send_ack_with_params(port, conn_id, session_params)

    return Connection(
        connection_id=conn_id,
        role=Role.CLIENT,
        session_params=session_params,
    )
