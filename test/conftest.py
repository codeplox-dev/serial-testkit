"""pytest configuration and fixtures for serial-testkit tests.

Provides:
- MockSerialPort: Single-buffer mock for simple unit tests
- ConnectedMockPorts: Bidirectional mock pair for timeout/exchange tests
- socat PTY pair fixture for integration tests
- Markers for unit vs integration tests
"""

import io
import re
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest


class MockSerialPort:
    """Mock serial port for unit testing.

    Uses a single buffer shared between read and write operations.
    Data written to the port can be read back immediately.

    Use ConnectedMockPorts for testing scenarios that require
    separate send/receive channels (like timeout testing).
    """

    def __init__(self) -> None:
        self._buffer = io.BytesIO()
        self._read_pos = 0
        self._lock = threading.Lock()

    def write(self, data: bytes) -> int:
        with self._lock:
            pos = self._buffer.tell()
            self._buffer.seek(0, 2)  # Seek to end
            written = self._buffer.write(data)
            self._buffer.seek(pos)
            return written

    def read(self, size: int = 1, /) -> bytes:
        with self._lock:
            self._buffer.seek(self._read_pos)
            data = self._buffer.read(size)
            self._read_pos = self._buffer.tell()
            return data

    @property
    def in_waiting(self) -> int:
        with self._lock:
            end_pos = self._buffer.seek(0, 2)
            waiting = end_pos - self._read_pos
            return max(0, waiting)

    def inject(self, data: bytes) -> None:
        """Inject data into the buffer as if received from peer."""
        self.write(data)


class ConnectedMockPorts:
    """Bidirectional mock port pair for testing client-server communication.

    Data written to port_a appears in port_b's read buffer and vice versa.
    This properly simulates a serial connection where each side has
    independent send and receive channels.
    """

    def __init__(self) -> None:
        self._a_to_b = io.BytesIO()
        self._b_to_a = io.BytesIO()
        self._a_read_pos = 0
        self._b_read_pos = 0
        self._lock = threading.Lock()

    @property
    def port_a(self) -> "_ConnectedPort":
        """Port A: writes go to B's read buffer, reads come from B's writes."""
        return _ConnectedPort(self, is_port_a=True)

    @property
    def port_b(self) -> "_ConnectedPort":
        """Port B: writes go to A's read buffer, reads come from A's writes."""
        return _ConnectedPort(self, is_port_a=False)


class _ConnectedPort:
    """One end of a ConnectedMockPorts pair."""

    def __init__(self, parent: ConnectedMockPorts, is_port_a: bool) -> None:
        self._parent = parent
        self._is_port_a = is_port_a

    def write(self, data: bytes) -> int:
        with self._parent._lock:
            # Write to the OTHER port's read buffer
            buffer = self._parent._a_to_b if self._is_port_a else self._parent._b_to_a
            pos = buffer.tell()
            buffer.seek(0, 2)  # Seek to end
            written = buffer.write(data)
            buffer.seek(pos)
            return written

    def read(self, size: int = 1, /) -> bytes:
        with self._parent._lock:
            # Read from OUR read buffer (filled by other port's writes)
            if self._is_port_a:
                buffer = self._parent._b_to_a
                self._parent._b_to_a.seek(self._parent._a_read_pos)
                data = buffer.read(size)
                self._parent._a_read_pos = buffer.tell()
            else:
                buffer = self._parent._a_to_b
                self._parent._a_to_b.seek(self._parent._b_read_pos)
                data = buffer.read(size)
                self._parent._b_read_pos = buffer.tell()
            return data

    @property
    def in_waiting(self) -> int:
        with self._parent._lock:
            if self._is_port_a:
                buffer = self._parent._b_to_a
                read_pos = self._parent._a_read_pos
            else:
                buffer = self._parent._a_to_b
                read_pos = self._parent._b_read_pos
            end_pos = buffer.seek(0, 2)
            return max(0, end_pos - read_pos)

    def inject(self, data: bytes) -> None:
        """Inject data as if it came from the peer (for testing)."""
        with self._parent._lock:
            # Inject into OUR read buffer
            if self._is_port_a:
                buffer = self._parent._b_to_a
            else:
                buffer = self._parent._a_to_b
            pos = buffer.tell()
            buffer.seek(0, 2)
            buffer.write(data)
            buffer.seek(pos)


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line("markers", "unit: mark test as unit test")
    config.addinivalue_line("markers", "integration: mark test as integration test (requires socat)")


@pytest.fixture
def pty_pair() -> Generator[tuple[str, str, subprocess.Popen[str]], None, None]:
    """Create a connected PTY pair using socat.

    Yields (pty1, pty2, socat_process).

    The PTYs are connected: data written to pty1 appears on pty2 and vice versa.
    This enables testing serial communication without real hardware.

    Requires: socat installed and Linux platform.
    """
    if sys.platform != "linux":
        pytest.skip("socat PTY fixture requires Linux")

    # Check if socat is available
    try:
        subprocess.run(["which", "socat"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        pytest.skip("socat not installed")

    # Start socat to create connected PTY pair
    socat = subprocess.Popen(
        ["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"],
        stderr=subprocess.PIPE,
        text=True,
    )

    # Parse PTY names from socat stderr output
    ptys: list[str] = []
    try:
        for _ in range(20):  # Give socat time to start
            if socat.poll() is not None:
                raise RuntimeError(f"socat exited early with code {socat.returncode}")

            assert socat.stderr is not None
            line = socat.stderr.readline()
            if "PTY is" in line:
                match = re.search(r"/dev/pts/\d+", line)
                if match:
                    ptys.append(match.group())
            if len(ptys) == 2:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError(f"Failed to get PTY pair from socat, got: {ptys}")

        yield ptys[0], ptys[1], socat

    finally:
        # Cleanup
        if socat.poll() is None:
            socat.terminate()
            socat.wait(timeout=5)
        if socat.stderr:
            socat.stderr.close()


@pytest.fixture
def script_dir() -> Path:
    """Return path to the main script directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def serialtest_path(script_dir: Path) -> Path:
    """Return path to serialtest.py."""
    return script_dir / "serialtest.py"
