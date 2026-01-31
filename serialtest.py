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
from dataclasses import dataclass
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


@dataclass
class LatencyStats:
    """Computed latency statistics in milliseconds."""

    count: int
    min_ms: float
    max_ms: float
    avg_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


def compute_latency_stats(rtt_samples: list[float]) -> LatencyStats | None:
    """Compute latency statistics from RTT samples (in seconds).

    Returns None if no samples available.
    """
    if not rtt_samples:
        return None

    count = len(rtt_samples)
    samples_ms = sorted(s * 1000 for s in rtt_samples)

    def percentile(sorted_data: list[float], p: float) -> float:
        idx = int(p / 100 * (len(sorted_data) - 1))
        return sorted_data[idx]

    return LatencyStats(
        count=count,
        min_ms=samples_ms[0],
        max_ms=samples_ms[-1],
        avg_ms=sum(samples_ms) / count,
        p50_ms=percentile(samples_ms, 50),
        p95_ms=percentile(samples_ms, 95),
        p99_ms=percentile(samples_ms, 99),
    )


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
) -> tuple[int, int, int, int, float, list[float]]:
    """Run send/receive loop until duration expires or SIGINT.

    Returns (sent, received, crc_ok, bytes_transferred, elapsed_s, rtt_samples).

    rtt_samples contains the round-trip time in seconds for each successful
    message exchange. Timeouts are not included.

    During warmup_s, write timeouts are tolerated (retried) while waiting for peer.
    After warmup, write timeouts raise SerialTimeoutException.

    Stats counting begins only after peer detection (first successful read), so messages
    sent during warmup before the peer is ready are not counted.
    """
    sent, received, crc_ok = 0, 0, 0
    bytes_transferred = 0
    rtt_samples: list[float] = []
    running = True
    start_time = time.monotonic()
    stats_start_time: float | None = None
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
            payload_out = message.random_payload()
            msg_start = time.monotonic()
            dev.write_msg(payload_out)
        except serial.SerialTimeoutException:
            if in_warmup:
                if not warmup_logged:
                    logger.info("Waiting for peer...")
                    warmup_logged = True
                continue
            raise

        # Only count sent after peer is detected (first successful read)
        if peer_detected:
            sent += 1
            # Count bytes: payload + 8 bytes protocol overhead (4-byte length + 4-byte CRC)
            bytes_transferred += len(payload_out) + 8

        payload, ok = dev.read_msg()
        if payload is not None:
            if not peer_detected:
                logger.info("Peer detected")
                peer_detected = True
                stats_start_time = time.monotonic()
            else:
                # Only count received after peer was already detected
                rtt_samples.append(time.monotonic() - msg_start)
                received += 1
                bytes_transferred += len(payload) + 8
                if ok:
                    crc_ok += 1

    elapsed_s = (time.monotonic() - stats_start_time) if stats_start_time else 0.0
    return sent, received, crc_ok, bytes_transferred, elapsed_s, rtt_samples


THROUGHPUT_MIN_DURATION_S = 30


def report(stats: tuple[int, int, int, int, float, list[float]]) -> None:
    """Print statistics from run_loop."""
    sent, received, crc_ok, bytes_transferred, elapsed_s, rtt_samples = stats
    pct = (crc_ok / received * 100) if received else 0
    print(f"sent={sent} recv={received} ok={crc_ok} ({pct:.1f}%)")
    if sent != received:
        print("(Note: sent/recv counts differ due to in-flight message when one side stopped first)")
    if elapsed_s > 0 and bytes_transferred > 0:
        # For 8N1 UART: each byte needs 10 bits on wire (1 start + 8 data + 1 stop)
        bytes_per_sec = bytes_transferred / elapsed_s
        baud = bytes_per_sec * 10
        kbps = (bytes_transferred * 8 / elapsed_s) / 1000
        print(f"throughput: {baud:,.0f} baud ({kbps:.2f} Kbps) over {elapsed_s:.1f}s")
        if elapsed_s < THROUGHPUT_MIN_DURATION_S:
            print("(Note: throughput from short test may not reflect sustained performance)")
    latency = compute_latency_stats(rtt_samples)
    if latency:
        print(
            f"latency: avg={latency.avg_ms:.2f}ms min={latency.min_ms:.2f}ms "
            f"max={latency.max_ms:.2f}ms"
        )
        print(
            f"         p50={latency.p50_ms:.2f}ms p95={latency.p95_ms:.2f}ms "
            f"p99={latency.p99_ms:.2f}ms (n={latency.count})"
        )


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
