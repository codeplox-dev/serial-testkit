#!/usr/bin/env python3
"""
Test two-machine mode using a fake tty pair created with socat.
Requires: socat, Linux
"""

import re
import subprocess
import sys
import time
import unittest
from pathlib import Path

# Path to serialtest.py (parent directory of test/)
_SCRIPT_DIR = Path(__file__).parent.parent
_SERIAL = _SCRIPT_DIR / "serialtest.py"


class TestTwoMachine(unittest.TestCase):
    """Test serial communication between two instances via socat pty pair."""

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_two_machine_communication(self) -> None:
        # Start socat to create connected pty pair
        socat = subprocess.Popen(
            ["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"],
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_process, socat)

        # Parse pty names from socat stderr output
        ptys: list[str] = []
        for _ in range(10):
            assert socat.stderr is not None
            line = socat.stderr.readline()
            if "PTY is" in line:
                match = re.search(r"/dev/pts/\d+", line)
                if match:
                    ptys.append(match.group())
            if len(ptys) == 2:
                break
            time.sleep(0.1)

        self.assertEqual(
            len(ptys), 2, f"Failed to get pty pair from socat, got: {ptys}"
        )

        # Start two instances of serial.py with no flow control and short duration
        procs: list[subprocess.Popen[str]] = []
        for pty in ptys:
            proc = subprocess.Popen(
                [sys.executable, str(_SERIAL), "-d", pty, "-f", "none", "-t", "3"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            self.addCleanup(self._terminate_process, proc)

        # Collect and verify results
        for i, proc in enumerate(procs):
            stdout, stderr = proc.communicate(timeout=5)
            output = stdout + stderr

            match = re.search(r"sent=(\d+) recv=(\d+) ok=(\d+)", output)
            self.assertIsNotNone(
                match, f"Instance {i + 1}: No stats found in output:\n{output}"
            )
            assert match is not None

            _sent, recv, ok = (
                int(match.group(1)),
                int(match.group(2)),
                int(match.group(3)),
            )
            self.assertGreater(recv, 0, f"Instance {i + 1}: No messages received")
            self.assertEqual(ok, recv, f"Instance {i + 1}: CRC failures ({ok}/{recv})")

            # Verify latency output is present
            latency_match = re.search(r"latency: avg=[\d.]+ms", output)
            self.assertIsNotNone(
                latency_match,
                f"Instance {i + 1}: No latency stats found in output:\n{output}",
            )

    @staticmethod
    def _terminate_process(proc: subprocess.Popen) -> None:  # type: ignore[type-arg]
        if proc.poll() is None:
            proc.terminate()
            proc.wait()
        if proc.stderr:
            proc.stderr.close()
        if proc.stdout:
            proc.stdout.close()


if __name__ == "__main__":
    unittest.main()
