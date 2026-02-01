#!/usr/bin/env python3
"""Serial communication test tool with client/server roles.

This is a thin entrypoint that delegates to client/server runners.
"""

import argparse
import logging
import sys

from client.runner import run_client
from server.runner import run_server

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 115200
DEFAULT_HANDSHAKE_TIMEOUT_S = 30
DEFAULT_MSG_COUNT = 100


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Serial communication test with client/server roles",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Server (RPi):     %(prog)s -d /dev/ttyAMA4 -r server
  Client (workstation): %(prog)s -d /dev/ttyUSB0 -r client -n 100

Server waits for client connections (send SIGTERM/SIGINT to stop).
Client initiates peering and exits.
""",
    )

    parser.add_argument(
        "-d",
        "--device",
        type=str,
        required=True,
        help="Serial device path (e.g., /dev/ttyAMA4)",
    )
    parser.add_argument(
        "-r",
        "--role",
        type=str,
        choices=["client", "server"],
        required=True,
        help="Role: 'client' (initiates connection) or 'server' (waits for client)",
    )
    parser.add_argument(
        "-f",
        "--flow-control",
        type=str,
        choices=["none", "rtscts"],
        default="none",
        help="Flow control (default: none)",
    )
    parser.add_argument(
        "-b",
        "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        help=f"Baud rate (default: {DEFAULT_BAUDRATE})",
    )
    parser.add_argument(
        "-n",
        "--msg-count",
        type=int,
        default=DEFAULT_MSG_COUNT,
        help=f"Message count for session test (client only, default: {DEFAULT_MSG_COUNT})",
    )
    parser.add_argument(
        "-w",
        "--handshake-timeout",
        type=int,
        default=DEFAULT_HANDSHAKE_TIMEOUT_S,
        help=f"Handshake timeout in seconds (default: {DEFAULT_HANDSHAKE_TIMEOUT_S})",
    )
    parser.add_argument(
        "--no-latency-fix",
        action="store_true",
        help="Disable automatic FTDI latency timer configuration",
    )

    args = parser.parse_args()

    logger.info(f"Serial device: {args.device}, role={args.role}")

    # Convert flow control string to boolean
    rtscts = args.flow_control == "rtscts"

    if args.role == "client":
        return run_client(
            device=args.device,
            baudrate=args.baudrate,
            rtscts=rtscts,
            handshake_timeout_s=args.handshake_timeout,
            msg_count=args.msg_count,
            no_latency_fix=args.no_latency_fix,
        )
    else:
        return run_server(
            device=args.device,
            baudrate=args.baudrate,
            rtscts=rtscts,
            no_latency_fix=args.no_latency_fix,
        )


if __name__ == "__main__":
    sys.exit(main())
