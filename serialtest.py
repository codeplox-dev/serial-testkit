#!/usr/bin/env python3
"""Serial communication test tool."""

import argparse
import logging
import os
import pty
import signal
import sys
import threading
import time
from types import FrameType
from typing import Protocol

import serial
import serial.tools.list_ports

import message

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

DEFAULT_BAUDRATE = 115200
DEFAULT_DURATION_S = 15
DEFAULT_WARMUP_S = 5


class SerialDevice(Protocol):
    """Protocol for serial device implementations."""

    def close(self) -> None: ...
    def write_msg(self, payload: bytes) -> int: ...
    def read_msg(self) -> tuple[bytes | None, bool]: ...


def _write_msg(ser: serial.Serial, payload: bytes) -> int:
    return ser.write(message.encode(payload)) or 0


def _read_msg(ser: serial.Serial) -> tuple[bytes | None, bool]:
    return message.decode(ser)


class LoopbackDevice:
    """Virtual loopback device using a pty pair."""

    def __init__(self, baudrate: int) -> None:
        if sys.platform not in ("linux", "darwin"):
            raise RuntimeError(
                f"Loopback mode only supported on Linux/macOS, not {sys.platform}"
            )
        self._master_fd, slave_fd = pty.openpty()
        slave_name = os.ttyname(slave_fd)
        os.close(slave_fd)
        self._serial = serial.Serial(
            slave_name,
            baudrate=baudrate,
            timeout=1.0,
            write_timeout=1.0,
            xonxoff=False,
            rtscts=False,
        )
        self._running = True
        self._echo_thread = threading.Thread(target=self._echo_loop, daemon=True)
        self._echo_thread.start()
        logger.info(f"Loopback pty: {slave_name}")

    def _echo_loop(self) -> None:
        while self._running:
            try:
                data = os.read(self._master_fd, 4096)
                if data:
                    os.write(self._master_fd, data)
            except OSError:
                break

    def close(self) -> None:
        self._running = False
        if self._serial.is_open:
            self._serial.close()
        os.close(self._master_fd)
        logger.info("Closed loopback device")

    def write_msg(self, payload: bytes) -> int:
        return _write_msg(self._serial, payload)

    def read_msg(self) -> tuple[bytes | None, bool]:
        return _read_msg(self._serial)


class HardwareDevice:
    """Hardware serial device."""

    def __init__(self, device: str, flow_control: str, baudrate: int) -> None:
        self._log_device_info(device)
        self._serial = serial.Serial(
            port=device,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=flow_control == "software",
            rtscts=flow_control == "ctsrts",
            timeout=1.0,
            write_timeout=1.0,
        )
        logger.debug(
            "Serial port settings: baudrate=%s, bytesize=%s, parity=%s, stopbits=%s, rtscts=%s",
            self._serial.baudrate,
            self._serial.bytesize,
            self._serial.parity,
            self._serial.stopbits,
            self._serial.rtscts,
        )

    @staticmethod
    def _log_device_info(device: str) -> None:
        if device.startswith("/dev/pts/"):
            logger.info(f"Device: {device} (pty)")
            return

        ports = [p for p in serial.tools.list_ports.comports() if p.device == device]
        if len(ports) == 0:
            raise RuntimeError(f"Device {device} not found in port list")
        if len(ports) > 1:
            raise RuntimeError(f"Multiple ports found for device {device}")

        info = ports[0]
        logger.info(f"Device: {info.device}")
        logger.info(f"Description: {info.description}")
        logger.info(f"Hardware ID: {info.hwid}")
        if info.vid is not None:
            logger.info(f"VID:PID: {info.vid:04x}:{info.pid:04x}")
        if info.manufacturer:
            logger.info(f"Manufacturer: {info.manufacturer}")
        if info.product:
            logger.info(f"Product: {info.product}")
        if info.serial_number:
            logger.info(f"Serial Number: {info.serial_number}")

    def close(self) -> None:
        if self._serial.is_open:
            name = self._serial.name
            self._serial.close()
            logger.info(f"Closed {name}")

    def write_msg(self, payload: bytes) -> int:
        return _write_msg(self._serial, payload)

    def read_msg(self) -> tuple[bytes | None, bool]:
        return _read_msg(self._serial)


def run_loop(
    dev: SerialDevice, duration_s: int, warmup_s: int = DEFAULT_WARMUP_S
) -> tuple[int, int, int]:
    """Run send/receive loop until duration expires or SIGINT. Returns (sent, received, crc_ok).

    During warmup_s, write timeouts are tolerated (retried) while waiting for peer.
    After warmup, write timeouts raise SerialTimeoutException.
    """
    sent, received, crc_ok = 0, 0, 0
    running = True
    start_time = time.monotonic()
    peer_detected = False
    warmup_logged = False

    def handler(_sig: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handler)

    while running and (duration_s == 0 or (time.monotonic() - start_time) < duration_s):
        elapsed = time.monotonic() - start_time
        in_warmup = elapsed < warmup_s

        try:
            dev.write_msg(message.random_payload())
        except serial.SerialTimeoutException:
            if in_warmup:
                if not warmup_logged:
                    logger.info("Waiting for peer...")
                    warmup_logged = True
                continue
            raise

        sent += 1

        if not peer_detected:
            logger.info("Peer detected")
            peer_detected = True

        payload, ok = dev.read_msg()
        if payload is not None:
            received += 1
            if ok:
                crc_ok += 1

    return sent, received, crc_ok


def report(stats: tuple[int, int, int]) -> None:
    """Print statistics from run_loop."""
    sent, received, crc_ok = stats
    pct = (crc_ok / received * 100) if received else 0
    print(f"sent={sent} recv={received} ok={crc_ok} ({pct:.1f}%)")


def run_test(dev: SerialDevice, duration: int, warmup: int) -> int:
    """Run the test loop and report results."""
    try:
        logger.info("Serial device ready")
        duration_msg = "indefinitely" if duration == 0 else f"for {duration}s"
        logger.info(f"Running test loop {duration_msg} (Ctrl-C to stop)")
        report(run_loop(dev, duration, warmup))
        return 0
    except serial.SerialException as e:
        logger.error(f"Serial error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    finally:
        dev.close()


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add baudrate, duration, and warmup arguments to a parser."""
    parser.add_argument(
        "-b",
        "--baudrate",
        type=int,
        default=DEFAULT_BAUDRATE,
        help=f"Baud rate (default: {DEFAULT_BAUDRATE})",
    )
    parser.add_argument(
        "-t",
        "--duration",
        type=int,
        default=DEFAULT_DURATION_S,
        help=f"Duration in seconds, 0 = indefinite (default: {DEFAULT_DURATION_S})",
    )
    parser.add_argument(
        "-w",
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_S,
        help=f"Warmup period in seconds to wait for peer (default: {DEFAULT_WARMUP_S})",
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Test serial device communication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s loopback                    Run loopback test with pty
  %(prog)s -d /dev/ttyAMA4             Run on serial device (hw flow control)
  %(prog)s -d /dev/ttyUSB0 -b 9600     Run at 9600 baud
""",
    )

    subparsers = parser.add_subparsers(dest="mode")

    # Loopback subcommand
    loopback_parser = subparsers.add_parser(
        "loopback", help="Run loopback test using pty"
    )
    _add_common_args(loopback_parser)

    # Device mode args (top-level)
    parser.add_argument(
        "-d", "--device", type=str, help="Serial device path (e.g., /dev/ttyAMA4)"
    )
    parser.add_argument(
        "-f",
        "--flow-control",
        type=str,
        choices=["none", "ctsrts", "software"],
        default="ctsrts",
        help="Flow control (default: ctsrts)",
    )
    _add_common_args(parser)

    args = parser.parse_args()

    if args.mode == "loopback":
        dev: SerialDevice = LoopbackDevice(args.baudrate)
        return run_test(dev, args.duration, args.warmup)

    if args.device:
        dev = HardwareDevice(args.device, args.flow_control, args.baudrate)
        return run_test(dev, args.duration, args.warmup)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
