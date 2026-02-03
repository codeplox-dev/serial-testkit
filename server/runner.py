"""Server runner for serial-testkit.

Contains run_server() which runs in a persistent loop,
waiting for client connections, performing session exchanges,
and handling SIGINT for graceful shutdown.
"""

import logging
import signal
from types import FrameType

from common.connection import PeeringError, Role
from common.device import configure_ftdi_latency_timer, open_serial
from common.report import PeeringReport
from server.handshake import server_handshake
from session.exchange import server_exchange
from session.report import SessionReport

logger = logging.getLogger(__name__)

# Short timeout for handshake polling - allows quick response to shutdown signals
HANDSHAKE_POLL_S = 1.0


def run_server(
    device: str,
    baudrate: int,
    rtscts: bool,
    no_latency_fix: bool = False,
) -> int:
    """Run server in persistent loop. Returns 0 unless crash.

    The server:
    - Waits for client connections
    - Handles SIGINT/SIGTERM for immediate graceful exit
    - Returns to peering mode after each session completes
    """
    running = True
    in_session = False

    def handle_signal(_sig: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False
        if in_session:
            logger.warning("Signal received during session - exiting early")
        else:
            logger.info("Signal received - shutting down")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Apply FTDI latency fix by default
    if not no_latency_fix:
        configure_ftdi_latency_timer(device)

    try:
        ser = open_serial(device, baudrate, rtscts)
    except Exception as e:
        logger.error(f"Failed to open serial port: {e}")
        return 1

    try:
        logger.info(f"Server started on {device}, waiting for connections...")

        while running:
            # Attempt peering with short timeout to allow quick response to signals
            try:
                conn = server_handshake(ser, client_timeout_s=HANDSHAKE_POLL_S)
            except PeeringError:
                # Timeout - loop back to check running flag
                continue

            if not running:  # Check if signal was received during handshake
                break

            logger.info(f"Connection established (id={conn.connection_id.hex()})")

            # Print peering report
            peering_report = PeeringReport(
                connected=True,
                connection_id=conn.connection_id,
                role=Role.SERVER,
                msg_count=conn.session_params.msg_count if conn.session_params else None,
            )
            peering_report.print()

            # Perform session data exchange
            in_session = True
            msg_count = conn.session_params.msg_count if conn.session_params else 0
            session_result = server_exchange(ser, conn, msg_count=msg_count)
            in_session = False

            if not running:  # Check if signal was received during session
                break

            # Print session report
            session_report = SessionReport(result=session_result)
            session_report.print()

            # Log summary and continue loop
            if session_result.success:
                logger.info("Session complete, returning to wait for next client")
            else:
                logger.warning(f"Session failed: {session_result.error}")

    finally:
        ser.close()
        logger.info(f"Closed {device}")

    logger.info("Server shutdown complete")
    return 0
