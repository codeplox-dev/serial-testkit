"""Integration tests for two-machine mode using a PTY pair created with socat.

Requires: socat, Linux

These tests use actual subprocess execution of serialtest.py, testing
the full integration of all components.
"""

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest


# Path to serialtest.py (parent directory of test/)
_SCRIPT_DIR = Path(__file__).parent.parent
_SERIAL = _SCRIPT_DIR / "serialtest.py"


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux")
class TestPeerOnly:
    """Test peering mode (now the default behavior)."""

    def test_peer_only_success(self, pty_pair: tuple[str, str, subprocess.Popen]) -> None:  # type: ignore[type-arg]
        """Test that peering completes successfully."""
        pty1, pty2, _socat = pty_pair

        # Start server (runs in loop, will be terminated via signal)
        # Use short timeout so server exits promptly after SIGTERM
        server_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty1,
                "-f", "none",
                "-r", "server",
                "-w", "3",  # short timeout for quick exit after signal
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        time.sleep(0.3)

        # Start client (exits after peering)
        client_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty2,
                "-f", "none",
                "-r", "client",
                "-w", "10",  # client needs enough time to connect
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait for client to complete peering
            client_stdout, client_stderr = client_proc.communicate(timeout=10)
            client_output = client_stdout + client_stderr

            assert client_proc.returncode == 0, f"client failed:\n{client_output}"
            assert "connection established" in client_output, (
                f"client: connection not established:\n{client_output}"
            )

            # Give server time to complete peering, then terminate
            time.sleep(0.5)
            server_proc.send_signal(signal.SIGTERM)
            server_stdout, server_stderr = server_proc.communicate(timeout=10)
            server_output = server_stdout + server_stderr

            assert server_proc.returncode == 0, f"server failed:\n{server_output}"
            assert "connection established" in server_output, (
                f"server: connection not established:\n{server_output}"
            )

        finally:
            for proc in [server_proc, client_proc]:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()
                if proc.stderr:
                    proc.stderr.close()
                if proc.stdout:
                    proc.stdout.close()

    @pytest.mark.parametrize("delay_s", [0.0, 0.5, 1.0, 2.0])
    def test_peer_only_with_varying_delays(
        self,
        pty_pair: tuple[str, str, subprocess.Popen],  # type: ignore[type-arg]
        delay_s: float,
    ) -> None:
        """Test peering works with varying startup delays between server and client."""
        pty1, pty2, _socat = pty_pair

        # Start server (runs in loop, will be terminated via signal)
        # Use short timeout so server exits promptly after SIGTERM
        server_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty1,
                "-f", "none",
                "-r", "server",
                "-w", "3",  # short timeout for quick exit after signal
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        # Wait specified delay before starting client
        time.sleep(delay_s)

        # Start client (exits after peering)
        client_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty2,
                "-f", "none",
                "-r", "client",
                "-w", "10",  # client needs enough time to connect
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait for client to complete
            client_stdout, client_stderr = client_proc.communicate(timeout=35)
            client_output = client_stdout + client_stderr

            assert client_proc.returncode == 0, (
                f"client failed with delay={delay_s}s:\n{client_output}"
            )
            assert "connection established" in client_output, (
                f"client: connection not established with delay={delay_s}s:\n{client_output}"
            )

            # Terminate server after client completes
            time.sleep(0.5)
            server_proc.send_signal(signal.SIGTERM)
            server_stdout, server_stderr = server_proc.communicate(timeout=10)
            server_output = server_stdout + server_stderr

            assert server_proc.returncode == 0, (
                f"server failed with delay={delay_s}s:\n{server_output}"
            )
            assert "connection established" in server_output, (
                f"server: connection not established with delay={delay_s}s:\n{server_output}"
            )

        finally:
            for proc in [server_proc, client_proc]:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()
                if proc.stderr:
                    proc.stderr.close()
                if proc.stdout:
                    proc.stdout.close()


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux")
class TestHandshakeTimeout:
    """Test handshake timeout behavior."""

    def test_server_graceful_shutdown_while_waiting(self, pty_pair: tuple[str, str, subprocess.Popen]) -> None:  # type: ignore[type-arg]
        """Test server shuts down gracefully when signaled while waiting for client."""
        pty1, _pty2, _socat = pty_pair

        server_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty1,
                "-f", "none",
                "-r", "server",
                "-w", "2",  # Short timeout so signal can be processed between attempts
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Let server start waiting for connections
            time.sleep(1)
            server_proc.send_signal(signal.SIGTERM)
            stdout, stderr = server_proc.communicate(timeout=10)
            output = stdout + stderr

            # Server should exit cleanly after signal (silently waits, no timeout logged)
            assert server_proc.returncode == 0, f"Server crashed:\n{output}"
            assert "shutdown" in output.lower(), f"Expected shutdown message:\n{output}"

        finally:
            if server_proc.poll() is None:
                server_proc.terminate()
                server_proc.wait()
            if server_proc.stderr:
                server_proc.stderr.close()
            if server_proc.stdout:
                server_proc.stdout.close()

    def test_client_timeout_no_server(self, pty_pair: tuple[str, str, subprocess.Popen]) -> None:  # type: ignore[type-arg]
        """Test client times out when no server responds."""
        _pty1, pty2, _socat = pty_pair

        client_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty2,
                "-f", "none",
                "-r", "client",
                "-w", "2",  # Short timeout
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            stdout, stderr = client_proc.communicate(timeout=10)
            output = stdout + stderr

            # Should fail with timeout (client exits with error code)
            assert client_proc.returncode != 0, f"Expected timeout failure:\n{output}"
            assert "timeout" in output.lower(), f"Expected timeout message:\n{output}"

        finally:
            if client_proc.poll() is None:
                client_proc.terminate()
                client_proc.wait()
            if client_proc.stderr:
                client_proc.stderr.close()
            if client_proc.stdout:
                client_proc.stdout.close()


@pytest.mark.integration
@pytest.mark.skipif(sys.platform != "linux", reason="Requires Linux")
class TestSessionExchange:
    """Test full session exchange (peering + data exchange + shutdown)."""

    def test_session_with_messages(self, pty_pair: tuple[str, str, subprocess.Popen]) -> None:  # type: ignore[type-arg]
        """Test full session with data exchange."""
        pty1, pty2, _socat = pty_pair

        # Start server
        server_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty1,
                "-f", "none",
                "-r", "server",
                "-w", "10",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        time.sleep(0.3)

        # Start client with explicit msg count
        client_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty2,
                "-f", "none",
                "-r", "client",
                "-n", "5",  # 5 messages
                "-w", "10",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait for client to complete session
            client_stdout, client_stderr = client_proc.communicate(timeout=30)
            client_output = client_stdout + client_stderr

            assert client_proc.returncode == 0, f"client failed:\n{client_output}"
            assert "Session: SUCCESS" in client_output, (
                f"client: session not successful:\n{client_output}"
            )
            assert "5 sent" in client_output, (
                f"client: expected 5 messages sent:\n{client_output}"
            )

            # Terminate server
            time.sleep(0.5)
            server_proc.send_signal(signal.SIGTERM)
            server_stdout, server_stderr = server_proc.communicate(timeout=10)
            server_output = server_stdout + server_stderr

            assert server_proc.returncode == 0, f"server failed:\n{server_output}"
            assert "Session: SUCCESS" in server_output, (
                f"server: session not successful:\n{server_output}"
            )

        finally:
            for proc in [server_proc, client_proc]:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()
                if proc.stderr:
                    proc.stderr.close()
                if proc.stdout:
                    proc.stdout.close()

    def test_session_zero_messages(self, pty_pair: tuple[str, str, subprocess.Popen]) -> None:  # type: ignore[type-arg]
        """Test session with zero messages (just peering + shutdown)."""
        pty1, pty2, _socat = pty_pair

        # Start server
        server_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty1,
                "-f", "none",
                "-r", "server",
                "-w", "10",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        time.sleep(0.3)

        # Start client with 0 messages
        client_proc = subprocess.Popen(
            [
                sys.executable,
                str(_SERIAL),
                "-d", pty2,
                "-f", "none",
                "-r", "client",
                "-n", "0",  # 0 messages - just peering
                "-w", "10",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        try:
            # Wait for client to complete
            client_stdout, client_stderr = client_proc.communicate(timeout=30)
            client_output = client_stdout + client_stderr

            # With 0 messages received, exit code should be NO_DATA (2)
            assert client_proc.returncode == 2, (
                f"client: expected exit code 2 (NO_DATA):\n{client_output}"
            )
            assert "0 sent" in client_output or "Session: SUCCESS" in client_output, (
                f"client: unexpected output:\n{client_output}"
            )

            # Terminate server
            time.sleep(0.5)
            server_proc.send_signal(signal.SIGTERM)
            server_stdout, server_stderr = server_proc.communicate(timeout=10)
            server_output = server_stdout + server_stderr

            assert server_proc.returncode == 0, f"server failed:\n{server_output}"

        finally:
            for proc in [server_proc, client_proc]:
                if proc.poll() is None:
                    proc.terminate()
                    proc.wait()
                if proc.stderr:
                    proc.stderr.close()
                if proc.stdout:
                    proc.stdout.close()
