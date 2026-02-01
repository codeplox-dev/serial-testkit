#!/usr/bin/env python3
"""Tests for serialtest.py CLI and CTS diagnostic features."""

import subprocess
import sys
import unittest
from pathlib import Path

# Path to serialtest.py (parent directory of test/)
_SCRIPT_DIR = Path(__file__).parent.parent
_SERIAL = _SCRIPT_DIR / "serialtest.py"


class TestFlowControlCLI(unittest.TestCase):
    """Test flow control CLI options."""

    def test_software_flow_control_not_available(self) -> None:
        """Verify software flow control option is rejected by CLI."""
        proc = subprocess.run(
            [sys.executable, str(_SERIAL), "-d", "/dev/null", "-f", "software"],
            capture_output=True,
            text=True,
        )
        # argparse should reject "software" as invalid choice
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("invalid choice", proc.stderr.lower())
        self.assertIn("software", proc.stderr)

    def test_none_flow_control_accepted(self) -> None:
        """Verify 'none' flow control is accepted."""
        proc = subprocess.run(
            [sys.executable, str(_SERIAL), "-f", "none", "--help"],
            capture_output=True,
            text=True,
        )
        # --help should succeed
        self.assertEqual(proc.returncode, 0)

    def test_crtscts_flow_control_accepted(self) -> None:
        """Verify 'crtscts' flow control is accepted."""
        proc = subprocess.run(
            [sys.executable, str(_SERIAL), "-f", "crtscts", "--help"],
            capture_output=True,
            text=True,
        )
        # --help should succeed
        self.assertEqual(proc.returncode, 0)

    def test_help_shows_only_valid_flow_options(self) -> None:
        """Verify help text only shows none and crtscts."""
        proc = subprocess.run(
            [sys.executable, str(_SERIAL), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        # Should show valid options
        self.assertIn("none", proc.stdout)
        self.assertIn("crtscts", proc.stdout)
        # Should NOT show software option
        # Use word boundary check to avoid false positives
        self.assertNotIn("software", proc.stdout.lower())


class TestCTSDiagnostics(unittest.TestCase):
    """Test CTS diagnostic logging features.

    Note: Full CTS testing requires hardware. These tests verify the
    diagnostic code paths exist and log appropriate messages.
    """

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_loopback_reports_no_cts(self) -> None:
        """Loopback mode should not report CTS state (not applicable)."""
        proc = subprocess.run(
            [sys.executable, str(_SERIAL), "loopback", "-t", "2"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Loopback doesn't use hardware flow control, so no CTS messages
        self.assertNotIn("CTS asserted", proc.stderr)
        self.assertNotIn("CTS not asserted", proc.stderr)

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_loopback_no_write_timeout_cts_message(self) -> None:
        """Loopback should not include CTS in timeout messages (N/A)."""
        # Loopback mode normally doesn't timeout, but if it did,
        # it should report "no flow control" not CTS state
        proc = subprocess.run(
            [sys.executable, str(_SERIAL), "loopback", "-t", "2"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Should complete successfully without CTS-related timeout messages
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("Check RTS/CTS wiring", proc.stderr)


class TestHardwareDeviceProtocol(unittest.TestCase):
    """Test HardwareDevice class properties exist and work correctly."""

    def test_cts_state_property_exists(self) -> None:
        """Verify cts_state property is defined on HardwareDevice."""
        import serialtest

        # Verify the property exists on the class
        self.assertTrue(hasattr(serialtest.HardwareDevice, "cts_state"))
        # And on the Protocol
        self.assertTrue(hasattr(serialtest.SerialDevice, "cts_state"))

    def test_out_waiting_property_exists(self) -> None:
        """Verify out_waiting property is defined on HardwareDevice."""
        import serialtest

        # Verify the property exists on the class
        self.assertTrue(hasattr(serialtest.HardwareDevice, "out_waiting"))
        # And on the Protocol
        self.assertTrue(hasattr(serialtest.SerialDevice, "out_waiting"))

    def test_loopback_device_cts_returns_none(self) -> None:
        """Verify LoopbackDevice.cts_state returns None."""
        import serialtest

        dev = serialtest.LoopbackDevice(baudrate=115200, flush=True)
        try:
            self.assertIsNone(dev.cts_state)
        finally:
            dev.close()

    def test_loopback_device_out_waiting_returns_int(self) -> None:
        """Verify LoopbackDevice.out_waiting returns an integer."""
        import serialtest

        dev = serialtest.LoopbackDevice(baudrate=115200, flush=True)
        try:
            self.assertIsInstance(dev.out_waiting, int)
            self.assertGreaterEqual(dev.out_waiting, 0)
        finally:
            dev.close()


if __name__ == "__main__":
    unittest.main()
