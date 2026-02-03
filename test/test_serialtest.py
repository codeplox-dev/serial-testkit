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

    def test_rtscts_flow_control_accepted(self) -> None:
        """Verify 'rtscts' flow control is accepted."""
        proc = subprocess.run(
            [sys.executable, str(_SERIAL), "-f", "rtscts", "--help"],
            capture_output=True,
            text=True,
        )
        # --help should succeed
        self.assertEqual(proc.returncode, 0)

    def test_help_shows_only_valid_flow_options(self) -> None:
        """Verify help text only shows none and rtscts."""
        proc = subprocess.run(
            [sys.executable, str(_SERIAL), "--help"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        # Should show valid options
        self.assertIn("none", proc.stdout)
        self.assertIn("rtscts", proc.stdout)
        # Should NOT show software option
        # Use word boundary check to avoid false positives
        self.assertNotIn("software", proc.stdout.lower())


if __name__ == "__main__":
    unittest.main()
