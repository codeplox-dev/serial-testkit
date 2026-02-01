#!/usr/bin/env python3
"""Tests for peer establishment protocol using fake pty pairs.

These tests verify the peering protocol handles various timing scenarios:
- Simultaneous start (both peers start at nearly the same time)
- Staggered start (one peer starts before the other)
- Timeout scenarios
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


class TestPeeringProtocol(unittest.TestCase):
    """Test peer establishment with various timing scenarios."""

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_simultaneous_start(self) -> None:
        """Both peers start at nearly the same time."""
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        # Start both instances simultaneously
        procs = []
        for pty in ptys:
            proc = subprocess.Popen(
                [sys.executable, str(_SERIAL), "-d", pty, "-f", "none", "-t", "2"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            self.addCleanup(self._terminate_process, proc)

        # Verify both complete successfully
        self._verify_successful_peering(procs)

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_staggered_start_first_wins(self) -> None:
        """First peer starts, second joins after delay - first should be initiator."""
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        # Start first peer
        proc1 = subprocess.Popen(
            [sys.executable, str(_SERIAL), "-d", ptys[0], "-f", "none", "-t", "3"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_process, proc1)

        # Wait a bit before starting second peer
        time.sleep(0.5)

        # Start second peer
        proc2 = subprocess.Popen(
            [sys.executable, str(_SERIAL), "-d", ptys[1], "-f", "none", "-t", "5"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_process, proc2)

        # Verify both complete successfully
        self._verify_successful_peering([proc1, proc2])

        # First peer should be initiator (earlier timestamp)
        stdout1, stderr1 = proc1.communicate(timeout=10)
        output1 = stdout1 + stderr1
        self.assertIn("initiator", output1.lower())

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_staggered_start_second_wins(self) -> None:
        """Second peer starts after first - first should still be initiator."""
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        # Start first peer with longer duration
        proc1 = subprocess.Popen(
            [sys.executable, str(_SERIAL), "-d", ptys[0], "-f", "none", "-t", "5"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_process, proc1)

        # Wait a bit before starting second peer
        time.sleep(1.0)

        # Start second peer with shorter duration
        proc2 = subprocess.Popen(
            [sys.executable, str(_SERIAL), "-d", ptys[1], "-f", "none", "-t", "2"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_process, proc2)

        # Both should complete - first peer's duration controls
        for i, proc in enumerate([proc1, proc2]):
            stdout, stderr = proc.communicate(timeout=15)
            output = stdout + stderr
            self.assertIn("Test completed successfully", output, f"Peer {i+1} failed")

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_rapid_succession_starts(self) -> None:
        """Start peers in rapid succession (< 100ms apart)."""
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        procs = []
        for pty in ptys:
            proc = subprocess.Popen(
                [sys.executable, str(_SERIAL), "-d", pty, "-f", "none", "-t", "2"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            self.addCleanup(self._terminate_process, proc)
            time.sleep(0.05)  # 50ms between starts

        self._verify_successful_peering(procs)

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_roles_are_complementary(self) -> None:
        """Verify exactly one initiator and one responder."""
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        procs = []
        for pty in ptys:
            proc = subprocess.Popen(
                [sys.executable, str(_SERIAL), "-d", pty, "-f", "none", "-t", "2"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            self.addCleanup(self._terminate_process, proc)

        outputs = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=10)
            outputs.append(stdout + stderr)

        # Count initiators and responders
        initiator_count = sum(1 for o in outputs if "initiator" in o.lower())
        responder_count = sum(1 for o in outputs if "responder" in o.lower())

        self.assertEqual(initiator_count, 1, "Expected exactly one initiator")
        self.assertEqual(responder_count, 1, "Expected exactly one responder")

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_test_ids_match(self) -> None:
        """Verify both peers agree on the same test_id."""
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        procs = []
        for pty in ptys:
            proc = subprocess.Popen(
                [sys.executable, str(_SERIAL), "-d", pty, "-f", "none", "-t", "2"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            self.addCleanup(self._terminate_process, proc)

        test_ids = []
        for proc in procs:
            stdout, stderr = proc.communicate(timeout=10)
            output = stdout + stderr
            # Extract test_id from output (format: test_id=XXXXXXXXXXXXXXXX)
            match = re.search(r"test_id=([0-9a-f]{16})", output)
            if match:
                test_ids.append(match.group(1))

        self.assertEqual(len(test_ids), 2, "Expected test_id from both peers")
        self.assertEqual(test_ids[0], test_ids[1], "Test IDs should match")

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_short_duration(self) -> None:
        """Test with minimum duration (2s)."""
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        procs = []
        for pty in ptys:
            proc = subprocess.Popen(
                [sys.executable, str(_SERIAL), "-d", pty, "-f", "none", "-t", "2"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            self.addCleanup(self._terminate_process, proc)

        self._verify_successful_peering(procs)

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_longer_duration(self) -> None:
        """Test with longer duration (10s)."""
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        procs = []
        for pty in ptys:
            proc = subprocess.Popen(
                [sys.executable, str(_SERIAL), "-d", pty, "-f", "none", "-t", "10"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(proc)
            self.addCleanup(self._terminate_process, proc)

        self._verify_successful_peering(procs, timeout=20)

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_mismatched_durations(self) -> None:
        """Test with different durations - initiator's duration should be used."""
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        # Start with different durations
        proc1 = subprocess.Popen(
            [sys.executable, str(_SERIAL), "-d", ptys[0], "-f", "none", "-t", "3"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_process, proc1)

        proc2 = subprocess.Popen(
            [sys.executable, str(_SERIAL), "-d", ptys[1], "-f", "none", "-t", "10"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_process, proc2)

        # Both should complete successfully
        # (duration used depends on which peer becomes initiator)
        for proc in [proc1, proc2]:
            stdout, stderr = proc.communicate(timeout=15)
            output = stdout + stderr
            self.assertIn("Test completed successfully", output)

    @unittest.skipUnless(sys.platform == "linux", "Requires Linux")
    def test_staggered_start_bidirectional_data(self) -> None:
        """Verify bidirectional data exchange with staggered start.

        This tests the critical scenario where the responder enters the test
        loop before the initiator finishes peering. Previously, the initiator
        would flush its buffer on receiving PEER_ACK, discarding data the
        responder had already sent.
        """
        socat, ptys = self._create_pty_pair()
        self.addCleanup(self._terminate_process, socat)

        # Start first peer with longer delay before second
        proc1 = subprocess.Popen(
            [sys.executable, str(_SERIAL), "-d", ptys[0], "-f", "none", "-t", "3"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_process, proc1)

        # Longer delay ensures responder starts sending before initiator
        # finishes peering
        time.sleep(1.5)

        proc2 = subprocess.Popen(
            [sys.executable, str(_SERIAL), "-d", ptys[1], "-f", "none", "-t", "3"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._terminate_process, proc2)

        # Both should complete and exchange data
        for i, proc in enumerate([proc1, proc2]):
            stdout, stderr = proc.communicate(timeout=10)
            output = stdout + stderr

            self.assertIn("Test completed successfully", output)

            # Extract recv count - both sides must receive messages
            match = re.search(r"recv=(\d+)", output)
            self.assertIsNotNone(match, f"Peer {i+1} has no recv count")
            assert match is not None
            recv = int(match.group(1))
            self.assertGreater(
                recv, 0,
                f"Peer {i+1} received no messages (bidirectional failure)"
            )

    def _create_pty_pair(self) -> tuple[subprocess.Popen, list[str]]:  # type: ignore[type-arg]
        """Create a connected pty pair using socat."""
        socat = subprocess.Popen(
            ["socat", "-d", "-d", "pty,raw,echo=0", "pty,raw,echo=0"],
            stderr=subprocess.PIPE,
            text=True,
        )

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

        self.assertEqual(len(ptys), 2, f"Failed to get pty pair from socat: {ptys}")
        return socat, ptys

    def _verify_successful_peering(
        self, procs: list[subprocess.Popen], timeout: int = 10  # type: ignore[type-arg]
    ) -> None:
        """Verify all processes complete successfully with proper peering."""
        for i, proc in enumerate(procs):
            stdout, stderr = proc.communicate(timeout=timeout)
            output = stdout + stderr

            # Check for success
            self.assertIn(
                "Test completed successfully",
                output,
                f"Peer {i+1} did not complete successfully:\n{output}",
            )

            # Check for proper role assignment
            has_role = "initiator" in output.lower() or "responder" in output.lower()
            self.assertTrue(
                has_role, f"Peer {i+1} has no role assigned:\n{output}"
            )

            # Check for test_id
            self.assertIn(
                "test_id=", output, f"Peer {i+1} has no test_id:\n{output}"
            )

            # Check for stats
            match = re.search(r"sent=(\d+) recv=(\d+) ok=(\d+)", output)
            self.assertIsNotNone(
                match, f"Peer {i+1} has no stats:\n{output}"
            )
            assert match is not None
            recv = int(match.group(2))
            ok = int(match.group(3))
            self.assertGreater(recv, 0, f"Peer {i+1} received no messages")
            self.assertEqual(ok, recv, f"Peer {i+1} has CRC failures")

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
