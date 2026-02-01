"""Serial device setup and FTDI configuration for serial-testkit.

Contains:
- configure_ftdi_latency_timer: Configure FTDI latency timer for reliable RTS/CTS
- log_device_info: Log information about a serial device
- open_serial: Open and configure a serial port
"""

import logging
import os

import serial
import serial.tools.list_ports

logger = logging.getLogger(__name__)

FTDI_LATENCY_TIMER_TARGET = 1


def configure_ftdi_latency_timer(device: str) -> bool:
    """Configure FTDI latency timer to 1ms for reliable RTS/CTS."""
    device_name = os.path.basename(device)

    if not device_name.startswith("ttyUSB"):
        logger.debug(f"Latency fix not applicable to {device_name}")
        return False

    sysfs_path = f"/sys/bus/usb-serial/devices/{device_name}/latency_timer"

    if not os.path.exists(sysfs_path):
        logger.warning(f"Cannot configure latency timer: {sysfs_path} not found")
        return False

    try:
        with open(sysfs_path, "r") as f:
            current_value = int(f.read().strip())

        if current_value == FTDI_LATENCY_TIMER_TARGET:
            logger.debug(f"Latency timer already set to {FTDI_LATENCY_TIMER_TARGET}ms")
            return True

        with open(sysfs_path, "w") as f:
            f.write(str(FTDI_LATENCY_TIMER_TARGET))

        with open(sysfs_path, "r") as f:
            new_value = int(f.read().strip())

        if new_value == FTDI_LATENCY_TIMER_TARGET:
            logger.info(
                f"Set FTDI latency timer from {current_value}ms to "
                f"{FTDI_LATENCY_TIMER_TARGET}ms for improved RTS/CTS reliability"
            )
            return True
        else:
            logger.warning(
                f"Failed to set latency timer: wrote {FTDI_LATENCY_TIMER_TARGET}, read {new_value}"
            )
            return False

    except PermissionError:
        logger.warning("Cannot configure latency timer: permission denied (run with sudo)")
        return False
    except Exception as e:
        logger.warning(f"Failed to configure latency timer: {e}")
        return False


def log_device_info(device: str) -> None:
    """Log information about a serial device."""
    real_path = os.path.realpath(device)
    if real_path.startswith("/dev/pts/"):
        logger.info(f"Device: {device} -> {real_path} (pty)")
        return

    ports = [p for p in serial.tools.list_ports.comports() if p.device == device]
    if len(ports) == 0:
        logger.info(f"Device: {device} (not in port list)")
        return
    if len(ports) > 1:
        raise RuntimeError(f"Multiple ports found for device {device}")

    info = ports[0]
    logger.info(f"Device: {info.device}")
    logger.info(f"Description: {info.description}")
    if info.vid is not None:
        logger.info(f"VID:PID: {info.vid:04x}:{info.pid:04x}")


def open_serial(
    device: str,
    baudrate: int,
    rtscts: bool = False,
) -> serial.Serial:
    """Open and configure a serial port."""
    log_device_info(device)
    ser = serial.Serial(
        port=device,
        baudrate=baudrate,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        xonxoff=False,
        rtscts=rtscts,
        timeout=0.1,
        write_timeout=1.0,
    )
    ser.reset_output_buffer()
    logger.debug(f"Serial port: baudrate={ser.baudrate}, rtscts={ser.rtscts}")
    return ser
