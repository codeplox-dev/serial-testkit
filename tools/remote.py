#!/usr/bin/env python3
"""Remote test helper for serial-testkit.

Provides utilities for:
- Uploading code to remote RPi via SSH
- Executing and managing remote/local test processes
- Fetching and merging test data
- Managing test cleanup

Environment variables:
    SERIAL_RPI_HOST: SSH host for RPi
    SERIAL_RPI_USER: SSH user for RPi
    SERIAL_RPI_PASSWORD: SSH password for RPi
    SERIAL_RPI_DEVICE: Serial device on RPi (default: /dev/ttyAMA4)
    SERIAL_RPI_PATH: Path to serial-testkit on RPi (default: ~/serial-testkit)
    SERIAL_LOCAL_DEVICE: Serial device on local machine (default: /dev/ttyUSB0)
"""

import os
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


# Default configuration from environment variables
def get_env(name: str, default: str = "") -> str:
    """Get environment variable with default."""
    return os.environ.get(name, default)


RPI_HOST = get_env("SERIAL_RPI_HOST")
RPI_USER = get_env("SERIAL_RPI_USER")
RPI_PASSWORD = get_env("SERIAL_RPI_PASSWORD")
RPI_DEVICE = get_env("SERIAL_RPI_DEVICE", "/dev/ttyAMA4")
RPI_PATH = get_env("SERIAL_RPI_PATH", "~/serial-testkit")
LOCAL_DEVICE = get_env("SERIAL_LOCAL_DEVICE", "/dev/ttyUSB0")


@dataclass
class RemoteConfig:
    """Configuration for remote connection."""

    host: str = RPI_HOST
    user: str = RPI_USER
    password: str = RPI_PASSWORD
    device: str = RPI_DEVICE
    path: str = RPI_PATH


@dataclass
class LocalConfig:
    """Configuration for local connection."""

    device: str = LOCAL_DEVICE
    script_dir: Path = Path(__file__).parent.parent


@dataclass
class TestResult:
    """Result from a test execution."""

    output: str
    returncode: int
    sent: int = 0
    recv: int = 0
    ok: int = 0
    role: str = "unknown"
    success: bool = False


class RemoteHelper:
    """Helper for managing remote RPi operations."""

    def __init__(
        self,
        remote: RemoteConfig | None = None,
        local: LocalConfig | None = None,
    ) -> None:
        self.remote = remote or RemoteConfig()
        self.local = local or LocalConfig()

    def ssh_cmd(self, command: str, timeout: int = 120) -> tuple[str, int]:
        """Execute command on remote host via SSH."""
        ssh_args = [
            "sshpass",
            "-p",
            self.remote.password,
            "ssh",
            "-oPubKeyAuthentication=no",
            "-oStrictHostKeyChecking=no",
            f"{self.remote.user}@{self.remote.host}",
            command,
        ]
        try:
            result = subprocess.run(
                ssh_args,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "SSH command timed out", -1
        except Exception as e:
            return f"SSH error: {e}", -1

    def scp_upload(self, local_path: Path | str, remote_path: str) -> tuple[str, int]:
        """Upload file to remote host via SCP."""
        scp_args = [
            "sshpass",
            "-p",
            self.remote.password,
            "scp",
            "-oPubKeyAuthentication=no",
            "-oStrictHostKeyChecking=no",
            str(local_path),
            f"{self.remote.user}@{self.remote.host}:{remote_path}",
        ]
        try:
            result = subprocess.run(
                scp_args,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "SCP command timed out", -1
        except Exception as e:
            return f"SCP error: {e}", -1

    def scp_download(self, remote_path: str, local_path: Path | str) -> tuple[str, int]:
        """Download file from remote host via SCP."""
        scp_args = [
            "sshpass",
            "-p",
            self.remote.password,
            "scp",
            "-oPubKeyAuthentication=no",
            "-oStrictHostKeyChecking=no",
            f"{self.remote.user}@{self.remote.host}:{remote_path}",
            str(local_path),
        ]
        try:
            result = subprocess.run(
                scp_args,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result.stdout + result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            return "SCP command timed out", -1
        except Exception as e:
            return f"SCP error: {e}", -1

    def upload_code(self) -> bool:
        """Upload current code to remote RPi."""
        script_dir = self.local.script_dir

        # Ensure remote directory exists
        output, returncode = self.ssh_cmd(f"mkdir -p {self.remote.path}")
        if returncode != 0:
            print(f"Failed to create remote directory: {output}")
            return False

        # Upload Python files
        for py_file in script_dir.glob("*.py"):
            output, returncode = self.scp_upload(py_file, f"{self.remote.path}/")
            if returncode != 0:
                print(f"Failed to upload {py_file.name}: {output}")
                return False

        # Upload pop script
        pop_path = script_dir / "pop"
        if pop_path.exists():
            output, returncode = self.scp_upload(pop_path, f"{self.remote.path}/")
            if returncode != 0:
                print(f"Failed to upload pop: {output}")
                return False

        # Upload requirements.txt
        req_path = script_dir / "requirements.txt"
        if req_path.exists():
            output, returncode = self.scp_upload(req_path, f"{self.remote.path}/")
            if returncode != 0:
                print(f"Failed to upload requirements.txt: {output}")
                return False

        # Clean Python bytecode cache to ensure fresh code is used
        # This is important when message formats change (e.g., sync magic)
        output, returncode = self.ssh_cmd(f"cd {self.remote.path} && ./pop clean")
        if returncode != 0:
            print(f"Warning: pop clean failed: {output}")
            # Continue anyway - cache may not exist yet

        return True

    def verify_connectivity(self) -> bool:
        """Verify SSH connectivity to remote host."""
        output, returncode = self.ssh_cmd("echo OK")
        return "OK" in output and returncode == 0

    def check_remote_serial_ports(self) -> list[str]:
        """Check for serial ports on remote host."""
        output, _ = self.ssh_cmd("ls /dev/tty* 2>/dev/null | grep -E '(USB|AMA|ACM)'")
        return [line.strip() for line in output.strip().split("\n") if line.strip()]

    def check_local_serial_ports(self) -> list[str]:
        """Check for serial ports on local machine."""
        result = subprocess.run(
            ["ls", "-1", "/dev/"],
            capture_output=True,
            text=True,
        )
        ports = []
        for line in result.stdout.strip().split("\n"):
            if re.match(r"tty(USB|AMA|ACM)", line):
                ports.append(f"/dev/{line}")
        return ports

    def kill_remote_tests(self) -> tuple[str, int]:
        """Kill any running test processes on remote host."""
        cmd = "sudo pkill -f 'python.*serialtest' || true"
        return self.ssh_cmd(cmd)

    def kill_local_tests(self) -> None:
        """Kill any running test processes locally."""
        subprocess.run(
            ["sudo", "pkill", "-f", "python.*serialtest"],
            capture_output=True,
        )

    def cleanup_all(self) -> None:
        """Clean up all test processes on both sides."""
        self.kill_remote_tests()
        self.kill_local_tests()

    def run_remote_test(
        self,
        duration_s: int,
        flow_control: Literal["none", "crtscts"],
        timeout: int | None = None,
    ) -> TestResult:
        """Run test on remote RPi."""
        if timeout is None:
            timeout = duration_s * 3 + 60

        cmd = (
            f"cd {self.remote.path} && "
            f"sudo ./pop run --device {self.remote.device} "
            f"-f {flow_control} -t {duration_s}"
        )
        output, returncode = self.ssh_cmd(cmd, timeout=timeout)
        return self._parse_result(output, returncode)

    def run_local_test(
        self,
        duration_s: int,
        flow_control: Literal["none", "crtscts"],
        timeout: int | None = None,
    ) -> TestResult:
        """Run test on local machine."""
        if timeout is None:
            timeout = duration_s * 3 + 60

        cmd = [
            "sudo",
            str(self.local.script_dir / "pop"),
            "run",
            "--device",
            self.local.device,
            "-f",
            flow_control,
            "-t",
            str(duration_s),
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout + result.stderr
            return self._parse_result(output, result.returncode)
        except subprocess.TimeoutExpired:
            return TestResult(output="TIMEOUT", returncode=-1)
        except Exception as e:
            return TestResult(output=f"Error: {e}", returncode=-1)

    def run_test_pair(
        self,
        duration_s: int,
        flow_control: Literal["none", "crtscts"],
        start_delay_s: float = 0,
    ) -> tuple[TestResult, TestResult]:
        """Run coordinated test on both remote and local machines.

        Returns (local_result, remote_result).
        """
        timeout = duration_s * 3 + 60
        remote_result_holder: dict[str, TestResult] = {}

        def run_remote():
            remote_result_holder["result"] = self.run_remote_test(
                duration_s, flow_control, timeout
            )

        # Start remote test in background
        remote_thread = threading.Thread(target=run_remote)
        remote_thread.start()

        # Wait for start delay
        if start_delay_s > 0:
            time.sleep(start_delay_s)

        # Run local test
        local_result = self.run_local_test(duration_s, flow_control, timeout)

        # Wait for remote test to complete
        remote_thread.join(timeout=timeout)
        remote_result = remote_result_holder.get(
            "result",
            TestResult(output="Remote thread did not complete", returncode=-1),
        )

        return local_result, remote_result

    def fetch_remote_results(
        self,
        remote_dir: str,
        local_dir: Path,
    ) -> bool:
        """Fetch test results from remote host."""
        local_dir.mkdir(parents=True, exist_ok=True)

        # Download using rsync via SSH
        rsync_args = [
            "sshpass",
            "-p",
            self.remote.password,
            "rsync",
            "-avz",
            "-e",
            "ssh -oPubKeyAuthentication=no -oStrictHostKeyChecking=no",
            f"{self.remote.user}@{self.remote.host}:{remote_dir}/",
            str(local_dir) + "/",
        ]
        try:
            result = subprocess.run(
                rsync_args,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _parse_result(self, output: str, returncode: int) -> TestResult:
        """Parse test output into TestResult."""
        sent, recv, ok = 0, 0, 0
        role = "unknown"

        stats_match = re.search(r"sent=(\d+)\s+recv=(\d+)\s+ok=(\d+)", output)
        if stats_match:
            sent = int(stats_match.group(1))
            recv = int(stats_match.group(2))
            ok = int(stats_match.group(3))

        if "initiator" in output.lower():
            role = "initiator"
        elif "responder" in output.lower():
            role = "responder"

        success = (
            "Test completed successfully" in output
            and ok == recv
            and recv > 0
        )

        return TestResult(
            output=output,
            returncode=returncode,
            sent=sent,
            recv=recv,
            ok=ok,
            role=role,
            success=success,
        )


def main() -> int:
    """CLI interface for remote helper operations."""
    import argparse

    parser = argparse.ArgumentParser(description="Remote test helper for serial-testkit")
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # upload command
    subparsers.add_parser("upload", help="Upload code to remote RPi")

    # verify command
    subparsers.add_parser("verify", help="Verify remote connectivity")

    # ports command
    subparsers.add_parser("ports", help="List serial ports")

    # cleanup command
    subparsers.add_parser("cleanup", help="Kill all test processes")

    # test command
    test_parser = subparsers.add_parser("test", help="Run a test pair")
    test_parser.add_argument("-t", "--duration", type=int, default=5, help="Test duration")
    test_parser.add_argument(
        "-f",
        "--flow",
        choices=["none", "crtscts"],
        default="none",
        help="Flow control mode",
    )
    test_parser.add_argument(
        "--delay",
        type=float,
        default=0,
        help="Start delay for local test",
    )

    args = parser.parse_args()

    helper = RemoteHelper()

    if args.command == "upload":
        if helper.upload_code():
            print("Code uploaded successfully")
            return 0
        else:
            print("Failed to upload code")
            return 1

    elif args.command == "verify":
        if helper.verify_connectivity():
            print("Remote connectivity OK")
            return 0
        else:
            print("Remote connectivity FAILED")
            return 1

    elif args.command == "ports":
        print("Local serial ports:")
        for port in helper.check_local_serial_ports():
            print(f"  {port}")
        print("Remote serial ports:")
        for port in helper.check_remote_serial_ports():
            print(f"  {port}")
        return 0

    elif args.command == "cleanup":
        helper.cleanup_all()
        print("Cleaned up all test processes")
        return 0

    elif args.command == "test":
        print(f"Running test: duration={args.duration}s, flow={args.flow}, delay={args.delay}s")
        local_result, remote_result = helper.run_test_pair(
            args.duration,
            args.flow,
            args.delay,
        )
        print(f"Local:  sent={local_result.sent} recv={local_result.recv} ok={local_result.ok} "
              f"role={local_result.role} success={local_result.success}")
        print(f"Remote: sent={remote_result.sent} recv={remote_result.recv} ok={remote_result.ok} "
              f"role={remote_result.role} success={remote_result.success}")
        return 0 if (local_result.success and remote_result.success) else 1

    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
