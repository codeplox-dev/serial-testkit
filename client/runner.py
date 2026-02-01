"""Client runner for serial-testkit.

Contains run_client() which performs peering and session data exchange,
returning an exit code based on the result.
"""

import logging
from enum import IntEnum

from client.handshake import client_handshake
from common.connection import PeeringError, Role, SessionParams
from common.device import configure_ftdi_latency_timer, open_serial
from common.report import PeeringReport
from session.exchange import client_exchange
from session.report import SessionReport

logger = logging.getLogger(__name__)


class ExitCode(IntEnum):
    """Exit codes for client operations."""

    SUCCESS = 0  # Session complete, 100% CRC OK
    PEERING_FAILED = 1  # Handshake timeout or failure
    NO_DATA = 2  # Session ran but no messages exchanged
    CRC_ERRORS = 3  # Session complete but CRC failures occurred


def run_client(
    device: str,
    baudrate: int,
    rtscts: bool,
    handshake_timeout_s: int,
    msg_count: int,
    no_latency_fix: bool = False,
) -> int:
    """Run client: peering + session data exchange. Returns exit code.

    The client:
    - Initiates handshake with server
    - Sends session params (msg_count) in ACK
    - Performs request-response data exchange
    - Returns exit code based on session result
    """
    # Apply FTDI latency fix by default
    if not no_latency_fix:
        configure_ftdi_latency_timer(device)

    try:
        ser = open_serial(device, baudrate, rtscts)
    except Exception as e:
        logger.error(f"Failed to open serial port: {e}")
        return ExitCode.PEERING_FAILED

    try:
        session_params = SessionParams(msg_count=msg_count)

        logger.info(f"Client: connecting to server (msg_count={msg_count})...")

        # Attempt peering with session params
        try:
            conn = client_handshake(
                ser, timeout_s=handshake_timeout_s, session_params=session_params
            )
        except PeeringError as e:
            logger.warning(f"Peering failed: {e}")
            report = PeeringReport(
                connected=False,
                error=e,
            )
            report.print()
            return ExitCode.PEERING_FAILED

        logger.info(
            f"Peering successful (id={conn.connection_id.hex()}, msg_count={msg_count})"
        )

        # Print peering report
        peering_report = PeeringReport(
            connected=True,
            connection_id=conn.connection_id,
            role=Role.CLIENT,
        )
        peering_report.print()

        # Perform session data exchange
        session_result = client_exchange(ser, conn, msg_count=msg_count)

        # Print session report
        session_report = SessionReport(result=session_result)
        session_report.print()

        # Map session result to exit code
        if not session_result.success:
            return ExitCode.PEERING_FAILED  # Session failed (timeout, etc.)
        if session_result.received == 0:
            return ExitCode.NO_DATA
        if session_result.crc_pass_rate < 100.0:
            return ExitCode.CRC_ERRORS

        return ExitCode.SUCCESS

    finally:
        ser.close()
        logger.info(f"Closed {device}")
