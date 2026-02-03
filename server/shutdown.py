"""Server shutdown functions for serial-testkit."""

import logging

from common.connection import Connection
from common.encoding import encode_control
from common.protocol import MsgType, SerialPort

logger = logging.getLogger(__name__)


def server_shutdown(port: SerialPort, conn: Connection) -> None:
    """Server responds to FIN with FIN_ACK."""
    logger.info("Server: responding to FIN")
    fin_ack_msg = encode_control(MsgType.FIN_ACK, conn.connection_id)
    port.write(fin_ack_msg)
    logger.info("Server: shutdown complete")
