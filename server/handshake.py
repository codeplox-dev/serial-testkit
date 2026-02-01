"""Server-side handshake functions for serial-testkit.

Implements server side of TCP-like 3-way handshake:
  1. Wait for SYN from client
  2. Send SYN_ACK
  3. Wait for ACK (with session params)
"""

import logging
import time

from common.connection import Connection, PeeringError, Role, SessionParams
from common.encoding import (
    EncodingError,
    TransportError,
    decode_ack_with_params,
    decode_message,
    encode_control,
)
from common.io import drain_input
from common.protocol import (
    DEFAULT_ACK_TIMEOUT_S,
    DEFAULT_CLIENT_TIMEOUT_S,
    DEFAULT_SYN_INTERVAL_S,
    MsgType,
    SerialPort,
)

logger = logging.getLogger(__name__)


def server_wait_for_syn(
    port: SerialPort,
    timeout_s: float = DEFAULT_CLIENT_TIMEOUT_S,
) -> bytes:
    """Wait for SYN from client.

    Returns conn_id on success.
    Raises PeeringError on timeout.
    Note: Caller should drain_input() before calling if needed.
    """
    start = time.monotonic()

    while time.monotonic() - start < timeout_s:
        try:
            msg_type, recv_id, _, crc_ok = decode_message(port)
        except (TransportError, EncodingError):
            continue

        if msg_type == MsgType.SYN and crc_ok:
            logger.info(f"Server: received SYN (id={recv_id.hex()})")
            return recv_id

    raise PeeringError(f"Server: timeout ({timeout_s}s) waiting for client SYN")


def server_send_syn_ack_wait_ack(
    port: SerialPort,
    conn_id: bytes,
    timeout_s: float = DEFAULT_ACK_TIMEOUT_S,
    syn_ack_interval_s: float = DEFAULT_SYN_INTERVAL_S,
) -> SessionParams:
    """Send SYN_ACK and wait for ACK with session params.

    Returns session_params on success.
    Raises PeeringError on timeout or missing session params.
    """
    syn_ack_msg = encode_control(MsgType.SYN_ACK, conn_id)
    start = time.monotonic()
    last_syn_ack = 0.0

    while time.monotonic() - start < timeout_s:
        # Retransmit SYN_ACK periodically (client may have missed it)
        if time.monotonic() - last_syn_ack > syn_ack_interval_s:
            port.write(syn_ack_msg)
            last_syn_ack = time.monotonic()
            logger.debug("Server: sent SYN_ACK")

        try:
            msg_type, recv_id, data, crc_ok = decode_message(port)
        except (TransportError, EncodingError):
            continue

        if msg_type == MsgType.ACK and recv_id == conn_id and crc_ok:
            # Decode session params from ACK payload (required)
            # The full payload is: [type][conn_id][session_params...]
            # We need to reconstruct it for decode_ack_with_params
            full_payload = bytes([MsgType.ACK]) + recv_id
            if data:
                full_payload += data

            try:
                _, session_params = decode_ack_with_params(full_payload)
            except EncodingError:
                logger.warning("Server: received ACK without session params, ignoring")
                continue

            logger.info(
                f"Server: received ACK, connection established (id={conn_id.hex()})"
            )
            logger.info(f"Server: session params: msg_count={session_params.msg_count}")
            return session_params

        # Handle duplicate SYN (client may be retransmitting)
        if msg_type == MsgType.SYN and recv_id == conn_id:
            logger.debug("Server: received duplicate SYN, will retransmit SYN_ACK")
            continue

    raise PeeringError(f"Server: timeout ({timeout_s}s) waiting for ACK")


def server_handshake(
    port: SerialPort,
    client_timeout_s: float = DEFAULT_CLIENT_TIMEOUT_S,
    ack_timeout_s: float = DEFAULT_ACK_TIMEOUT_S,
) -> Connection:
    """Perform server-side 3-way handshake.

    1. Wait for SYN from client (up to client_timeout_s)
    2. Send SYN_ACK with client's connection_id
    3. Wait for ACK with session params (up to ack_timeout_s)

    Returns Connection on success.
    Raises PeeringError on failure.
    """
    # Clear any stale data from previous runs
    drain_input(port)

    # Phase 1: Wait for SYN (raises PeeringError on timeout)
    conn_id = server_wait_for_syn(port, timeout_s=client_timeout_s)

    # Phase 2: Send SYN_ACK, wait for ACK with session params (raises PeeringError on timeout)
    session_params = server_send_syn_ack_wait_ack(port, conn_id, timeout_s=ack_timeout_s)

    return Connection(
        connection_id=conn_id,
        role=Role.SERVER,
        session_params=session_params,
    )
