"""Client shutdown functions for serial-testkit."""

import logging
import time

from common.connection import Connection
from common.encoding import (
    EncodingError,
    TransportError,
    decode_message,
    encode_control,
)
from common.protocol import FIN_WAIT_TIMEOUT_S, MsgType, SerialPort

logger = logging.getLogger(__name__)


def client_shutdown(
    port: SerialPort, conn: Connection, timeout_s: float = FIN_WAIT_TIMEOUT_S
) -> bool:
    """Client initiates clean shutdown.

    Sends FIN and waits for FIN_ACK.
    Returns True if FIN_ACK received, False on timeout.
    """
    logger.info("Client: initiating shutdown")
    fin_msg = encode_control(MsgType.FIN, conn.connection_id)
    start = time.monotonic()
    last_fin = 0.0
    fin_interval = 0.5  # Retry FIN more frequently than SYN

    while time.monotonic() - start < timeout_s:
        # Retransmit FIN periodically
        if time.monotonic() - last_fin > fin_interval:
            port.write(fin_msg)
            last_fin = time.monotonic()
            logger.debug("Client: sent FIN")

        try:
            msg_type, recv_id, _, crc_ok = decode_message(port)
        except (TransportError, EncodingError):
            continue

        if (
            msg_type == MsgType.FIN_ACK
            and recv_id == conn.connection_id
            and crc_ok
        ):
            logger.info("Client: received FIN_ACK, shutdown complete")
            return True

    logger.warning("Client: FIN_ACK timeout, closing anyway")
    return False
