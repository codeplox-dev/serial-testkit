#!/usr/bin/env python3
"""Serial communication test tool."""

import argparse
import logging
import os
import pty
import sys
import threading
import time
from dataclasses import dataclass
from typing import Protocol

import serial
import serial.tools.list_ports

import message
import peering

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

DEFAULT_BAUDRATE = 115200
DEFAULT_DURATION_S = 15
DEFAULT_WARMUP_S = 20
THROUGHPUT_MIN_DURATION_S = 30

# -----------------------------------------------------------------------------
# Experimental: FTDI Latency Timer Configuration
# -----------------------------------------------------------------------------
#
# FTDI USB-serial adapters (FT232R, FT232RL, etc.) have a known issue with
# hardware flow control (RTS/CTS). The chip has an internal 256-byte buffer
# and a latency timer that controls how often data is flushed to the host.
#
# Default latency timer is 16ms, which can cause flow control timing issues:
# - The CTS signal may not be processed quickly enough
# - Buffer overflows can occur before CTS is asserted
# - Write timeouts happen even with correct wiring
#
# Setting the latency timer to 1ms significantly improves RTS/CTS reliability
# by reducing the response time to flow control signals.
#
# This is controlled via sysfs:
#   /sys/bus/usb-serial/devices/ttyUSB0/latency_timer
#
# References:
# - Linux Kernel Bug #197109: ftdi_sio RTS/CTS issues
# - FTDI Application Note AN232B-04: Data Throughput, Latency and Handshaking
#
# This feature is EXPERIMENTAL because:
# - Requires root privileges to modify sysfs
# - May not work on all systems or kernel versions
# - Only applies to FTDI devices (not native UARTs like ttyAMA*)
# -----------------------------------------------------------------------------

FTDI_LATENCY_TIMER_TARGET = 1  # Target latency timer value in milliseconds


def configure_ftdi_latency_timer(device: str) -> bool:
    """
    Attempt to configure FTDI latency timer to improve RTS/CTS reliability.

    This is an EXPERIMENTAL feature that reduces the FTDI chip's internal
    buffer latency from 16ms to 1ms, improving hardware flow control timing.

    Args:
        device: Serial device path (e.g., /dev/ttyUSB0)

    Returns:
        True if configuration was successful, False otherwise.

    Note:
        - Requires root privileges
        - Only works for FTDI USB-serial devices
        - Setting is volatile (resets on device disconnect)
    """
    # Extract device name (e.g., "ttyUSB0" from "/dev/ttyUSB0")
    device_name = os.path.basename(device)

    # Only applicable to USB serial devices
    if not device_name.startswith("ttyUSB"):
        logger.debug(f"Latency fix not applicable to {device_name} (not a USB serial device)")
        return False

    sysfs_path = f"/sys/bus/usb-serial/devices/{device_name}/latency_timer"

    # Check if sysfs path exists
    if not os.path.exists(sysfs_path):
        logger.warning(f"Cannot configure latency timer: {sysfs_path} not found")
        return False

    try:
        # Read current value
        with open(sysfs_path, "r") as f:
            current_value = int(f.read().strip())

        if current_value == FTDI_LATENCY_TIMER_TARGET:
            logger.debug(f"Latency timer already set to {FTDI_LATENCY_TIMER_TARGET}ms")
            return True

        # Attempt to write new value
        with open(sysfs_path, "w") as f:
            f.write(str(FTDI_LATENCY_TIMER_TARGET))

        # Verify the change
        with open(sysfs_path, "r") as f:
            new_value = int(f.read().strip())

        if new_value == FTDI_LATENCY_TIMER_TARGET:
            logger.info(
                f"EXPERIMENTAL: Set FTDI latency timer from {current_value}ms to "
                f"{FTDI_LATENCY_TIMER_TARGET}ms for improved RTS/CTS reliability"
            )
            return True
        else:
            logger.warning(
                f"Failed to set latency timer: wrote {FTDI_LATENCY_TIMER_TARGET}, "
                f"read back {new_value}"
            )
            return False

    except PermissionError:
        logger.warning(
            f"Cannot configure latency timer: permission denied. "
            f"Run with sudo to enable this experimental feature."
        )
        return False
    except Exception as e:
        logger.warning(f"Failed to configure latency timer: {e}")
        return False


# -----------------------------------------------------------------------------
# Data classes
# -----------------------------------------------------------------------------


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


@dataclass
class TestResult:
    """Results from a test run."""

    sent: int
    received: int
    crc_ok: int
    bytes_transferred: int
    elapsed_s: float
    rtt_samples: list[float]
    peer_complete_received: bool  # True if we received PEER_COMPLETE from peer
    peer_complete_sent: bool  # True if we sent PEER_COMPLETE to peer
    is_leader: bool  # True if this node controlled the test duration
    test_id: int  # Hash of initiator's nanosecond start time

    @property
    def clean_exit(self) -> bool:
        """True if test ended cleanly (peer complete exchanged)."""
        return self.peer_complete_received or self.peer_complete_sent

    @property
    def crc_pass_rate(self) -> float:
        """CRC pass rate as percentage (0-100)."""
        return (self.crc_ok / self.received * 100) if self.received else 0.0

    @property
    def success(self) -> bool:
        """True if test was successful (clean exit + 100% CRC)."""
        return self.clean_exit and self.received > 0 and self.crc_ok == self.received


@dataclass
class TestStats:
    """Statistics collected during test execution."""

    sent: int
    received: int
    crc_ok: int
    bytes_transferred: int
    rtt_samples: list[float]
    peer_complete_received: bool
    peer_complete_sent: bool


# -----------------------------------------------------------------------------
# Utility functions
# -----------------------------------------------------------------------------


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


# -----------------------------------------------------------------------------
# Serial device abstraction
# -----------------------------------------------------------------------------


class SerialDevice(Protocol):
    """Protocol for serial device implementations."""

    @property
    def cts_state(self) -> bool | None:
        """CTS line state (None if not using hardware flow control)."""
        ...

    @property
    def out_waiting(self) -> int:
        """Bytes waiting in output buffer."""
        ...

    def close(self) -> None: ...
    def write_msg(self, payload: bytes) -> int: ...
    def read_msg(self) -> tuple[bytes | None, bool]: ...
    def flush_buffers(self) -> None: ...


def _write_msg(ser: serial.Serial, payload: bytes) -> int:
    return ser.write(message.encode(payload)) or 0


def _read_msg(ser: serial.Serial) -> tuple[bytes | None, bool]:
    return message.decode(ser)


class LoopbackDevice:
    """Virtual loopback device using a pty pair."""

    def __init__(self, baudrate: int, flush: bool = True) -> None:
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
            timeout=0.1,  # Short timeout to prevent buffer overflow
            write_timeout=1.0,
            xonxoff=False,
            rtscts=False,
        )
        if flush:
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            logger.debug("Flushed serial buffers")
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

    @property
    def cts_state(self) -> bool | None:
        """CTS not applicable for loopback device."""
        return None

    @property
    def out_waiting(self) -> int:
        """Return bytes waiting in output buffer."""
        return self._serial.out_waiting

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

    def flush_buffers(self) -> None:
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()


class HardwareDevice:
    """Hardware serial device."""

    def __init__(
        self, device: str, flow_control: str, baudrate: int, flush: bool = True
    ) -> None:
        self._log_device_info(device)
        self._serial = serial.Serial(
            port=device,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=False,  # Software flow control incompatible with binary data
            rtscts=flow_control == "crtscts",
            timeout=0.1,  # Short timeout to prevent buffer overflow
            write_timeout=1.0,
        )
        if flush:
            self._serial.reset_input_buffer()
            self._serial.reset_output_buffer()
            logger.debug("Flushed serial buffers")
        logger.debug(
            "Serial port settings: baudrate=%s, bytesize=%s, parity=%s, stopbits=%s, rtscts=%s",
            self._serial.baudrate,
            self._serial.bytesize,
            self._serial.parity,
            self._serial.stopbits,
            self._serial.rtscts,
        )
        # Check initial CTS state when using hardware flow control
        if self._serial.rtscts:
            if self._serial.cts:
                logger.debug("CTS asserted at startup")
            else:
                logger.warning(
                    "CTS not asserted at startup - peer may not be connected "
                    "or not configured for hardware flow control"
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

    @property
    def cts_state(self) -> bool | None:
        """Query CTS line state. Returns None if not using hardware flow control."""
        if not self._serial.rtscts:
            return None
        return self._serial.cts

    @property
    def out_waiting(self) -> int:
        """Return bytes waiting in output buffer."""
        return self._serial.out_waiting

    def close(self) -> None:
        if self._serial.is_open:
            name = self._serial.name
            self._serial.close()
            logger.info(f"Closed {name}")

    def write_msg(self, payload: bytes) -> int:
        return _write_msg(self._serial, payload)

    def read_msg(self) -> tuple[bytes | None, bool]:
        return _read_msg(self._serial)

    def flush_buffers(self) -> None:
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()


# -----------------------------------------------------------------------------
# Test execution
# -----------------------------------------------------------------------------


PEER_COMPLETE_SEND_COUNT = 3  # Send PEER_COMPLETE multiple times for reliability


def _run_test_loop(
    dev: SerialDevice,
    peer_info: peering.PeerInfo,
) -> TestStats:
    """Execute the test loop, sending and receiving data messages.

    Initiator: runs for duration_s, then sends PEER_COMPLETE (multiple times).
    Responder: runs until PEER_COMPLETE received or safety timeout.

    All messages include test_id; messages with wrong test_id are ignored.
    If responder receives PEER_INIT during test, it re-sends PEER_ACK (lost ACK recovery).
    """
    sent, received, crc_ok = 0, 0, 0
    bytes_transferred = 0
    rtt_samples: list[float] = []
    peer_complete_received = False
    peer_complete_sent = False
    write_timeout_count = 0
    cts_false_count = 0
    test_start = time.monotonic()

    # Responder safety timeout: prevents infinite loop if PEER_COMPLETE is lost
    responder_max_duration = (
        peer_info.duration_s * peering.RESPONDER_TIMEOUT_MULTIPLIER
    )

    while True:
        test_elapsed = time.monotonic() - test_start

        # Initiator: check if duration expired
        if peer_info.is_initiator and test_elapsed >= peer_info.duration_s:
            logger.info("Test duration complete, signaling peer...")
            # Send PEER_COMPLETE multiple times for reliability
            for i in range(PEER_COMPLETE_SEND_COUNT):
                try:
                    dev.write_msg(peering.make_peer_complete(peer_info.test_id))
                    peer_complete_sent = True
                except serial.SerialTimeoutException:
                    logger.warning(f"Timeout sending peer complete signal (attempt {i+1})")
            break

        # Responder: check safety timeout
        if not peer_info.is_initiator and test_elapsed >= responder_max_duration:
            logger.warning(
                f"Responder safety timeout after {test_elapsed:.1f}s "
                f"(expected PEER_COMPLETE within {responder_max_duration}s)"
            )
            break

        # Send a data message
        try:
            msg_start = time.monotonic()
            payload_out = peering.make_data_msg(peer_info.test_id)
            dev.write_msg(payload_out)
            sent += 1
            bytes_transferred += len(payload_out) + 8  # +8 for length+CRC
        except serial.SerialTimeoutException:
            write_timeout_count += 1
            cts = dev.cts_state
            out_waiting = dev.out_waiting
            if cts is None:
                # Not using hardware flow control
                logger.warning("Write timeout (no flow control)")
            elif not cts:
                cts_false_count += 1
                logger.warning(
                    f"Write timeout - CTS not asserted (out_waiting={out_waiting}). "
                    "Check RTS/CTS wiring or try -f none"
                )
            else:
                logger.warning(
                    f"Write timeout despite CTS asserted (out_waiting={out_waiting}). "
                    "Possible kernel/driver issue"
                )
            continue

        # Try to read a message
        payload, crc_ok_flag = dev.read_msg()
        if payload is None:
            continue

        # Handle received message
        match peering.classify_test_message(payload, peer_info.test_id):
            case peering.MessageResult.COMPLETE:
                logger.info("Received peer complete signal")
                peer_complete_received = True
                break
            case peering.MessageResult.PEER_INIT:
                # Initiator may not have received our ACK - re-send it
                if not peer_info.is_initiator:
                    logger.debug("Re-sending PEER_ACK (initiator may have missed it)")
                    try:
                        dev.write_msg(peering.make_peer_ack(peer_info.test_id))
                    except serial.SerialTimeoutException:
                        pass
            case peering.MessageResult.DATA:
                rtt_samples.append(time.monotonic() - msg_start)
                received += 1
                bytes_transferred += len(payload) + 8
                if crc_ok_flag:
                    crc_ok += 1
            case peering.MessageResult.IGNORE:
                pass

    # Report flow control diagnostics if issues occurred
    if write_timeout_count > 0:
        logger.info(
            f"Flow control stats: {write_timeout_count} write timeouts, "
            f"{cts_false_count} with CTS not asserted"
        )

    return TestStats(
        sent=sent,
        received=received,
        crc_ok=crc_ok,
        bytes_transferred=bytes_transferred,
        rtt_samples=rtt_samples,
        peer_complete_received=peer_complete_received,
        peer_complete_sent=peer_complete_sent,
    )


def run_loop(
    dev: SerialDevice,
    duration_s: int,
    warmup_s: int = DEFAULT_WARMUP_S,
    is_loopback: bool = False,
) -> TestResult:
    """Run the complete test: peer establishment then data exchange.

    Protocol:
    1. Peer establishment: exchange PEER_INIT, determine roles via timestamp
    2. Test phase: exchange data messages with test_id prefix
    3. Completion: initiator sends PEER_COMPLETE after duration expires

    Loopback mode skips peer establishment (always initiator).
    Exit code 0 requires: clean completion + 100% CRC pass rate.
    """
    our_timestamp_ns = time.time_ns()

    # Determine peer info (establish peer or loopback)
    peer_info: peering.PeerInfo
    if is_loopback:
        test_id = peering.make_test_id(our_timestamp_ns)
        peer_info = peering.PeerInfo(
            is_initiator=True, test_id=test_id, duration_s=duration_s
        )
        logger.info(f"Loopback mode (test_id={test_id:016x}, duration: {duration_s}s)")
    else:
        maybe_peer_info = peering.establish_peer(
            dev, our_timestamp_ns, duration_s, warmup_s
        )
        if maybe_peer_info is None:
            return TestResult(
                sent=0,
                received=0,
                crc_ok=0,
                bytes_transferred=0,
                elapsed_s=0.0,
                rtt_samples=[],
                peer_complete_received=False,
                peer_complete_sent=False,
                is_leader=False,
                test_id=0,
            )
        peer_info = maybe_peer_info

    # Run the test loop
    test_start = time.monotonic()
    stats = _run_test_loop(dev, peer_info)
    elapsed_s = time.monotonic() - test_start

    return TestResult(
        sent=stats.sent,
        received=stats.received,
        crc_ok=stats.crc_ok,
        bytes_transferred=stats.bytes_transferred,
        elapsed_s=elapsed_s,
        rtt_samples=stats.rtt_samples,
        peer_complete_received=stats.peer_complete_received,
        peer_complete_sent=stats.peer_complete_sent,
        is_leader=peer_info.is_initiator,
        test_id=peer_info.test_id,
    )


# -----------------------------------------------------------------------------
# Reporting
# -----------------------------------------------------------------------------


def report(result: TestResult) -> None:
    """Print statistics from run_loop."""
    print(
        f"sent={result.sent} recv={result.received} ok={result.crc_ok} "
        f"({result.crc_pass_rate:.1f}%)"
    )
    if result.sent != result.received:
        print(
            "(Note: sent/recv counts differ due to in-flight message "
            "when one side stopped first)"
        )
    if result.elapsed_s > 0 and result.bytes_transferred > 0:
        # For 8N1 UART: each byte needs 10 bits on wire (1 start + 8 data + 1 stop)
        bytes_per_sec = result.bytes_transferred / result.elapsed_s
        baud = bytes_per_sec * 10
        kbps = (result.bytes_transferred * 8 / result.elapsed_s) / 1000
        print(
            f"throughput: {baud:,.0f} baud ({kbps:.2f} Kbps) "
            f"over {result.elapsed_s:.1f}s"
        )
        if result.elapsed_s < THROUGHPUT_MIN_DURATION_S:
            print(
                "(Note: throughput from short test may not reflect "
                "sustained performance)"
            )
    latency = compute_latency_stats(result.rtt_samples)
    if latency:
        print(
            f"latency: avg={latency.avg_ms:.2f}ms min={latency.min_ms:.2f}ms "
            f"max={latency.max_ms:.2f}ms"
        )
        print(
            f"         p50={latency.p50_ms:.2f}ms p95={latency.p95_ms:.2f}ms "
            f"p99={latency.p99_ms:.2f}ms (n={latency.count})"
        )
    # Report role and completion status
    role = "initiator" if result.is_leader else "responder"
    if result.peer_complete_received:
        print(f"(Role: {role}, peer signaled test complete)")
    elif result.peer_complete_sent:
        print(f"(Role: {role}, signaled peer test complete)")
    elif not result.clean_exit:
        print(f"(Role: {role}, warning: test did not complete cleanly)")


def run_test(
    dev: SerialDevice, duration: int, warmup: int, is_loopback: bool = False
) -> int:
    """Run the test loop and report results.

    Returns:
        0 if test completed successfully (clean exit + 100% CRC)
        1 if test failed (errors, no peer, or CRC failures)
    """
    try:
        logger.info("Serial device ready")
        result = run_loop(dev, duration, warmup, is_loopback)
        report(result)

        if result.success:
            logger.info("Test completed successfully")
            return 0
        else:
            if not result.clean_exit:
                logger.error("Test did not complete cleanly")
            elif result.received == 0:
                logger.error("No messages received")
            else:
                logger.error(
                    f"CRC failures: {result.received - result.crc_ok}/{result.received}"
                )
            return 1
    except serial.SerialException as e:
        logger.error(f"Serial error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Error: {e}")
        return 1
    finally:
        dev.close()


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------


def _positive_int(value: str) -> int:
    """Validate that a value is a positive integer."""
    try:
        ivalue = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: '{value}'")
    if ivalue <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {ivalue}")
    return ivalue


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add baudrate, duration, warmup, and flush arguments to a parser."""
    parser.add_argument(
        "-b",
        "--baudrate",
        type=_positive_int,
        default=DEFAULT_BAUDRATE,
        help=f"Baud rate (default: {DEFAULT_BAUDRATE})",
    )
    parser.add_argument(
        "-t",
        "--duration",
        type=_positive_int,
        default=DEFAULT_DURATION_S,
        help=f"Test duration in seconds (default: {DEFAULT_DURATION_S})",
    )
    parser.add_argument(
        "-w",
        "--warmup",
        type=_positive_int,
        default=DEFAULT_WARMUP_S,
        help=f"Warmup period in seconds to wait for peer (default: {DEFAULT_WARMUP_S})",
    )
    parser.add_argument(
        "--flush",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Flush serial buffers on start (default: flush)",
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
        choices=["none", "crtscts"],
        default="crtscts",
        help="Flow control (default: crtscts)",
    )
    parser.add_argument(
        "--no-latency-fix",
        action="store_true",
        help="Disable automatic FTDI latency timer configuration (1ms for reliable RTS/CTS)",
    )
    _add_common_args(parser)

    args = parser.parse_args()

    if args.mode == "loopback":
        dev: SerialDevice = LoopbackDevice(args.baudrate, args.flush)
        return run_test(dev, args.duration, args.warmup, is_loopback=True)

    if args.device:
        # Apply FTDI latency fix by default (disable with --no-latency-fix)
        if not getattr(args, "no_latency_fix", False):
            configure_ftdi_latency_timer(args.device)

        dev = HardwareDevice(args.device, args.flow_control, args.baudrate, args.flush)
        return run_test(dev, args.duration, args.warmup)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
