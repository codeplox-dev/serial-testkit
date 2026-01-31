#!/usr/bin/env python3

import abc
import argparse
import sys
import os
import logging
import pty
import signal
import threading
import time
from types import FrameType
from typing import Literal
import zlib
import serial
import serial.tools.list_ports

logging.basicConfig(level=logging.DEBUG)

logger = logging.getLogger(__name__)

# Protocol constants
_UINT32_SIZE = 4  # Length header and CRC are 4 bytes each
_BYTE_ORDER: Literal['little', 'big'] = 'little'
_ENCODING = 'utf-8'

MSG_COUNT = 50
BAUDRATE = 115200
RUN_DURATION_S = 15

def _load_env() -> None:
    global MSG_COUNT, BAUDRATE, RUN_DURATION_S
    MSG_COUNT = int(os.environ.get("MSG_COUNT", str(MSG_COUNT)))
    BAUDRATE = int(os.environ.get("BAUDRATE", str(BAUDRATE)))
    RUN_DURATION_S = int(os.environ.get("RUN_DURATION_S", str(RUN_DURATION_S)))

def _uint32_to_bytes(value: int) -> bytes:
    """Encode unsigned 32-bit int as little-endian bytes."""
    return value.to_bytes(_UINT32_SIZE, _BYTE_ORDER, signed=False)

def _uint32_from_bytes(data: bytes) -> int:
    """Decode little-endian bytes to unsigned 32-bit int."""
    return int.from_bytes(data, _BYTE_ORDER, signed=False)

def _encode_msg(msg: str) -> bytes:
    payload = msg.encode(_ENCODING)
    length = _uint32_to_bytes(len(payload))
    crc = _uint32_to_bytes(zlib.crc32(payload))
    return length + payload + crc

def _decode_msg(ser: serial.Serial) -> tuple[str | None, bool]:
    length_bytes = ser.read(_UINT32_SIZE)
    if len(length_bytes) < _UINT32_SIZE:
        return None, False
    length = _uint32_from_bytes(length_bytes)
    payload = ser.read(length)
    crc_bytes = ser.read(_UINT32_SIZE)
    if len(payload) < length or len(crc_bytes) < _UINT32_SIZE:
        return None, False
    expected_crc = _uint32_from_bytes(crc_bytes)
    actual_crc = zlib.crc32(payload)
    return payload.decode(_ENCODING), expected_crc == actual_crc

def run_loop(dev: "SerialDevice") -> tuple[int, int, int]:
    """Run the send/receive loop until duration expires or SIGINT.

    Returns (sent, received, crc_ok).

    Note: Serial read/write operations have timeouts (1s each) ensuring the
    duration check is evaluated at least every ~2 seconds per loop iteration.
    """
    sent, received, crc_ok = 0, 0, 0
    running = True
    start_time = time.monotonic()

    def handler(_sig: int, _frame: FrameType | None) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, handler)

    while running and (RUN_DURATION_S == 0 or (time.monotonic() - start_time) < RUN_DURATION_S):
        dev.write_msg(f"ping {sent}")
        sent += 1
        msg, ok = dev.read_msg()
        if msg is not None:
            received += 1
            if ok:
                crc_ok += 1

    return sent, received, crc_ok

def report(stats: tuple[int, int, int]) -> None:
    """Print statistics from run_loop."""
    sent, received, crc_ok = stats
    pct = (crc_ok / received * 100) if received else 0
    print(f"sent={sent} recv={received} ok={crc_ok} ({pct:.1f}%)")

def main() -> int:
    _load_env()

    parser = argparse.ArgumentParser(description="Test serial device communication")
    parser.add_argument(
        "-d", "--device", type=str, required=True,
        help="Serial device path (e.g., /dev/ttyUSB0) or special value 'loopback'")
    parser.add_argument(
        "-f", "--flow-control", type=str, choices=["none", "ctsrts", "software"],
        default="ctsrts", help="Flow control: none, ctsrts (default), software")
    args = parser.parse_args()

    ser = None
    try:
        ser = SerialDevice.create(args.device, args.flow_control)
        logger.info("Serial device ready")
        duration_msg = "indefinitely" if RUN_DURATION_S == 0 else f"for {RUN_DURATION_S}s"
        logger.info(f"Running test loop {duration_msg} (Ctrl-C to stop)")
        report(run_loop(ser))
    except serial.SerialException as e:
        logger.error(f"Serial error: {e}")
        return 1
    except Exception as e:
        logger.error(f"Error checking serial device: {e}")
        return 1
    finally:
        if ser is not None:
            ser.close()

    return 0


class SerialDevice(abc.ABC):

    def __init__(self) -> None:
        self.serial: serial.Serial | None = None

    @abc.abstractmethod
    def close(self) -> None:
        pass

    @abc.abstractmethod
    def write_msg(self, msg: str) -> int:
        pass

    @abc.abstractmethod
    def read_msg(self) -> tuple[str | None, bool]:
        pass

    @classmethod
    def create(cls, device: str, flow_control: str) -> "SerialDevice":
        if device == "loopback":
            return LoopbackSerialDevice()
        return HardwareSerialDevice(device, flow_control)


class LoopbackSerialDevice(SerialDevice):

    def __init__(self) -> None:
        super().__init__()
        if sys.platform not in ('linux', 'darwin'):
            raise RuntimeError(f"Loopback mode only supported on Linux/macOS, not {sys.platform}")
        self._master_fd, slave_fd = pty.openpty()
        slave_name = os.ttyname(slave_fd)
        os.close(slave_fd)  # Close fd, we'll reopen via pyserial
        self.serial = serial.Serial(
            slave_name, baudrate=BAUDRATE, timeout=1.0, write_timeout=1.0,
            xonxoff=False, rtscts=False)
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
        if self.serial and self.serial.is_open:
            self.serial.close()
        os.close(self._master_fd)
        logger.info("Closed loopback device")

    def write_msg(self, msg: str) -> int:
        assert self.serial is not None
        return self.serial.write(_encode_msg(msg)) or 0

    def read_msg(self) -> tuple[str | None, bool]:
        assert self.serial is not None
        return _decode_msg(self.serial)


class HardwareSerialDevice(SerialDevice):

    def __init__(self, device: str, flow_control: str) -> None:
        super().__init__()
        self.flow_control = flow_control
        self._read_device_info(device)
        self.serial = self._open_device(device)

    @staticmethod
    def _read_device_info(device: str) -> None:
        # Skip info lookup for pseudo-terminals (used in testing)
        if device.startswith('/dev/pts/'):
            logger.info(f"Device: {device} (pty)")
            return

        # Get device info from USB metadata if available
        ports = list(filter(lambda p: p.device == device, serial.tools.list_ports.comports()))

        if len(ports) == 0:
            raise RuntimeError(f"Device {device} not found in port list")
        if len(ports) > 1:
            raise RuntimeError(f"Multiple ports found for device {device}")

        port_info = ports[0]
        logger.info(f"Device: {port_info.device}")
        logger.info(f"Description: {port_info.description}")
        logger.info(f"Hardware ID: {port_info.hwid}")
        if port_info.vid is not None:
            logger.info(f"VID:PID: {port_info.vid:04x}:{port_info.pid:04x}")
        if port_info.manufacturer:
            logger.info(f"Manufacturer: {port_info.manufacturer}")
        if port_info.product:
            logger.info(f"Product: {port_info.product}")
        if port_info.serial_number:
            logger.info(f"Serial Number: {port_info.serial_number}")

    def _open_device(self, device: str) -> serial.Serial:
        ser = serial.Serial(
            port=device,
            baudrate=BAUDRATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            xonxoff=self.flow_control == "software",
            rtscts=self.flow_control == "ctsrts",
            timeout=1.0,
            write_timeout=1.0
        )
        logger.debug(
            "Serial port settings: baudrate=%s, bytesize=%s, parity=%s, stopbits=%s, rtscts=%s",
            ser.baudrate, ser.bytesize, ser.parity, ser.stopbits, ser.rtscts)
        return ser

    def close(self) -> None:
        if self.serial is not None and self.serial.is_open:
            self.serial.close()
            logger.info(f"Closed {self.serial.name}")

    def write_msg(self, msg: str) -> int:
        assert self.serial is not None
        return self.serial.write(_encode_msg(msg)) or 0

    def read_msg(self) -> tuple[str | None, bool]:
        assert self.serial is not None
        return _decode_msg(self.serial)


if __name__ == "__main__":
    sys.exit(main())
