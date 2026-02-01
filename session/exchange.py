"""Session data exchange for serial-testkit.

Contains:
- client_exchange: Client-side request-response loop with RTT measurement
- server_exchange: Server-side echo response loop
- wait_for_fin: Wait for FIN message from peer
"""

import logging
import time
from dataclasses import dataclass, field

from client.shutdown import client_shutdown
from common.encoding import EncodingError, TransportError, decode_message
from common.io import recv_data, send_data
from common.message import random_payload
from common.protocol import (
    FIN_WAIT_TIMEOUT_S,
    LOG_PROGRESS_INTERVAL,
    MsgType,
    SerialPort,
    TRACE,
)
from common.connection import (
    Connection,
    ConnectionMismatchError,
)
from server.shutdown import server_shutdown
from session.result import SessionError, SessionResult

logger = logging.getLogger(__name__)


@dataclass
class _SessionStats:
    """Internal stats accumulator during exchange."""

    sent: int = 0
    received: int = 0
    crc_ok: int = 0
    crc_errors: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    rtt_samples: list[float] = field(default_factory=list)
    elapsed_s: float = 0.0


def _stats_to_dict(stats: _SessionStats) -> dict:
    """Convert _SessionStats to dict for SessionResult unpacking."""
    return {
        "sent": stats.sent,
        "received": stats.received,
        "crc_ok": stats.crc_ok,
        "crc_errors": stats.crc_errors,
        "bytes_sent": stats.bytes_sent,
        "bytes_received": stats.bytes_received,
        "rtt_samples": stats.rtt_samples,
        "elapsed_s": stats.elapsed_s,
    }


def wait_for_fin(
    port: SerialPort,
    conn: Connection,
    timeout_s: float = FIN_WAIT_TIMEOUT_S,
) -> bool:
    """Wait for FIN message from peer.

    Args:
        port: Serial port to read from.
        conn: Connection with connection_id for filtering.
        timeout_s: Maximum time to wait for FIN.

    Returns:
        True if FIN received with matching conn_id, False on timeout.
    """
    start = time.monotonic()

    while time.monotonic() - start < timeout_s:
        try:
            msg_type, recv_id, _, crc_ok = decode_message(port)
        except (TransportError, EncodingError):
            continue

        if msg_type == MsgType.FIN and recv_id == conn.connection_id and crc_ok:
            logger.debug("Received FIN from peer")
            return True

        # Ignore other messages (DATA, etc.) while waiting for FIN

    logger.debug(f"Timeout ({timeout_s}s) waiting for FIN")
    return False


# -----------------------------------------------------------------------------
# Client exchange handlers
# -----------------------------------------------------------------------------


def _handle_client_data_response(
    stats: _SessionStats,
    response: bytes,
    crc_ok: bool,
    rtt_start: float,
    msg_index: int,
    msg_count: int,
) -> None:
    """Handle DATA response from server, updating stats and RTT."""
    stats.received += 1
    if response:
        stats.bytes_received += len(response)

    if crc_ok:
        stats.crc_ok += 1
        rtt = time.monotonic() - rtt_start
        stats.rtt_samples.append(rtt)
        logger.log(
            TRACE, f"Client: received response {msg_index + 1}/{msg_count} (RTT={rtt * 1000:.2f}ms)"
        )
        # Periodic progress logging
        if (msg_index + 1) % LOG_PROGRESS_INTERVAL == 0:
            logger.debug(f"Client: progress {msg_index + 1}/{msg_count} (RTT={rtt * 1000:.2f}ms)")
    else:
        stats.crc_errors += 1
        logger.warning(f"Client: CRC error on response {msg_index + 1}/{msg_count}")


def _handle_client_server_fin(
    stats: _SessionStats,
    start: float,
) -> SessionResult:
    """Handle unexpected FIN from server during exchange."""
    stats.elapsed_s = time.monotonic() - start
    logger.warning("Client: server sent FIN during exchange")
    return SessionResult(
        success=False,
        error=SessionError("Server sent FIN during exchange"),
        **_stats_to_dict(stats),
    )


def client_exchange(
    port: SerialPort,
    conn: Connection,
    msg_count: int,
) -> SessionResult:
    """Client-side data exchange.

    Sends msg_count DATA messages, waits for echo response to each,
    measures RTT for latency statistics.

    Args:
        port: Serial port for communication.
        conn: Established connection from peering.
        msg_count: Number of request-response rounds.

    Returns:
        SessionResult with exchange statistics.
    """
    stats = _SessionStats()
    start = time.monotonic()

    logger.info(f"Client: starting session exchange (msg_count={msg_count})")

    if msg_count == 0:
        # Skip exchange, go straight to shutdown
        logger.info("Client: msg_count=0, skipping exchange")
        stats.elapsed_s = time.monotonic() - start
        fin_ack = client_shutdown(port, conn)
        return SessionResult(
            success=True,
            fin_ack_received=fin_ack,
            **_stats_to_dict(stats),
        )

    for i in range(msg_count):
        # Generate random payload
        payload = random_payload()

        # Send DATA and start RTT timer
        rtt_start = time.monotonic()
        bytes_written = send_data(port, conn, payload)
        if bytes_written:
            stats.sent += 1
            stats.bytes_sent += bytes_written
            logger.log(TRACE, f"Client: sent message {i + 1}/{msg_count} ({len(payload)} bytes)")

        # Wait for echo response
        try:
            response, crc_ok, msg_type = recv_data(port, conn)
        except (TransportError, EncodingError, ConnectionMismatchError):
            stats.elapsed_s = time.monotonic() - start
            logger.error(f"Client: timeout waiting for response to message {i + 1}")
            return SessionResult(
                success=False,
                error=SessionError(f"Timeout waiting for response to message {i + 1}"),
                **_stats_to_dict(stats),
            )

        match msg_type:
            case MsgType.DATA:
                _handle_client_data_response(stats, response, crc_ok, rtt_start, i, msg_count)
            case MsgType.FIN:
                return _handle_client_server_fin(stats, start)

    stats.elapsed_s = time.monotonic() - start
    logger.info(
        f"Client: exchange complete ({stats.sent} sent, {stats.received} received, "
        f"{stats.crc_ok} ok, {stats.crc_errors} errors)"
    )

    # Initiate shutdown
    logger.info("Client: initiating shutdown")
    fin_ack = client_shutdown(port, conn)

    return SessionResult(
        success=True,
        fin_ack_received=fin_ack,
        **_stats_to_dict(stats),
    )


# -----------------------------------------------------------------------------
# Server exchange handlers
# -----------------------------------------------------------------------------


def _handle_server_data(
    port: SerialPort,
    conn: Connection,
    stats: _SessionStats,
    data: bytes,
    crc_ok: bool,
    msg_index: int,
    msg_count: int,
) -> None:
    """Handle DATA message from client, update stats and echo back."""
    stats.received += 1
    if data:
        stats.bytes_received += len(data)

    if crc_ok:
        stats.crc_ok += 1
        logger.log(TRACE, f"Server: received message {msg_index + 1}/{msg_count}")
    else:
        stats.crc_errors += 1
        logger.warning(f"Server: CRC error on message {msg_index + 1}/{msg_count}")

    # Echo back the payload (or send random if data was empty)
    payload = data if data else random_payload()
    bytes_written = send_data(port, conn, payload)
    if bytes_written:
        stats.sent += 1
        stats.bytes_sent += bytes_written
        logger.log(TRACE, f"Server: sent echo {msg_index + 1}/{msg_count}")
        # Periodic progress logging
        if (msg_index + 1) % LOG_PROGRESS_INTERVAL == 0:
            logger.debug(f"Server: progress {msg_index + 1}/{msg_count}")


def _handle_server_client_fin(
    port: SerialPort,
    conn: Connection,
    stats: _SessionStats,
    start: float,
) -> SessionResult:
    """Handle early FIN from client during exchange."""
    stats.elapsed_s = time.monotonic() - start
    logger.warning(f"Server: client sent FIN after {stats.received} messages")
    server_shutdown(port, conn)
    return SessionResult(
        success=False,
        error=SessionError(f"Client sent FIN after {stats.received} messages"),
        fin_received=True,
        **_stats_to_dict(stats),
    )


def server_exchange(
    port: SerialPort,
    conn: Connection,
    msg_count: int,
) -> SessionResult:
    """Server-side data exchange.

    Receives msg_count DATA messages from client, echoes each back.

    Args:
        port: Serial port for communication.
        conn: Established connection from peering.
        msg_count: Number of messages to expect from client.

    Returns:
        SessionResult with exchange statistics.
    """
    stats = _SessionStats()
    start = time.monotonic()

    logger.info(f"Server: starting session exchange (msg_count={msg_count})")

    if msg_count == 0:
        # Skip exchange, wait for FIN
        logger.info("Server: msg_count=0, waiting for FIN")
        stats.elapsed_s = time.monotonic() - start
        fin_received = wait_for_fin(port, conn, timeout_s=FIN_WAIT_TIMEOUT_S)
        if fin_received:
            server_shutdown(port, conn)
        return SessionResult(
            success=True,
            fin_received=fin_received,
            **_stats_to_dict(stats),
        )

    for i in range(msg_count):
        # Wait for client DATA
        try:
            data, crc_ok, msg_type = recv_data(port, conn)
        except (TransportError, EncodingError, ConnectionMismatchError):
            stats.elapsed_s = time.monotonic() - start
            logger.error(f"Server: timeout waiting for message {i + 1}")
            return SessionResult(
                success=False,
                error=SessionError(f"Timeout waiting for message {i + 1}"),
                **_stats_to_dict(stats),
            )

        match msg_type:
            case MsgType.DATA:
                _handle_server_data(port, conn, stats, data, crc_ok, i, msg_count)
            case MsgType.FIN:
                return _handle_server_client_fin(port, conn, stats, start)

    stats.elapsed_s = time.monotonic() - start
    logger.info(
        f"Server: exchange complete ({stats.sent} sent, {stats.received} received, "
        f"{stats.crc_ok} ok, {stats.crc_errors} errors)"
    )

    # Wait for client FIN and respond with FIN_ACK
    logger.info("Server: waiting for client FIN")
    fin_received = wait_for_fin(port, conn, timeout_s=FIN_WAIT_TIMEOUT_S)
    if fin_received:
        server_shutdown(port, conn)
    else:
        logger.warning("Server: FIN timeout, closing anyway")

    return SessionResult(
        success=True,
        fin_received=fin_received,
        **_stats_to_dict(stats),
    )
