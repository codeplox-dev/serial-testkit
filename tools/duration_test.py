#!/usr/bin/env python3
"""Duration testing tool for serial-testkit.

Runs comprehensive tests with various flow control modes, durations, and timing delays.
Collects statistics in CSV format and saves logs from failed tests.
"""

import argparse
import csv
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

# Import the remote helper
from remote import RemoteConfig, LocalConfig, RemoteHelper, TestResult


# Default configuration
DEFAULT_TOTAL_DURATION_MINUTES = 45

# Test parameters
DURATIONS = [2, 5, 10, 15, 20, 30, 45, 61]
START_DELAYS = [0, 0.5, 1, 2, 3, 5]
FLOW_CONTROLS: list[Literal["none", "crtscts"]] = ["none", "crtscts"]


def save_failed_test_logs(
    test_id: str,
    timestamp: str,
    config_str: str,
    error_message: str,
    local_result: TestResult,
    remote_result: TestResult,
    results_dir: Path,
) -> None:
    """Save logs for failed tests."""
    log_dir = results_dir / "logs"
    log_dir.mkdir(exist_ok=True)

    with open(log_dir / f"{test_id}_local.log", "w") as f:
        f.write(f"Test ID: {test_id}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Config: {config_str}\n")
        f.write(f"Error: {error_message}\n")
        f.write("\n--- OUTPUT ---\n")
        f.write(local_result.output)

    with open(log_dir / f"{test_id}_remote.log", "w") as f:
        f.write(f"Test ID: {test_id}\n")
        f.write(f"Timestamp: {timestamp}\n")
        f.write(f"Config: {config_str}\n")
        f.write(f"Error: {error_message}\n")
        f.write("\n--- OUTPUT ---\n")
        f.write(remote_result.output)


def determine_error_message(local_result: TestResult, remote_result: TestResult) -> str:
    """Determine the error message for a failed test."""
    if not local_result.success:
        if "Test completed successfully" not in local_result.output:
            return "Local test did not complete successfully"
        if local_result.ok != local_result.recv:
            return f"Local CRC failures: {local_result.ok}/{local_result.recv}"
        if local_result.recv == 0:
            return "Local received no messages"
    if not remote_result.success:
        if "Test completed successfully" not in remote_result.output:
            return "Remote test did not complete successfully"
        if remote_result.ok != remote_result.recv:
            return f"Remote CRC failures: {remote_result.ok}/{remote_result.recv}"
        if remote_result.recv == 0:
            return "Remote received no messages"
    return "Unknown error"


def main() -> int:
    import os

    parser = argparse.ArgumentParser(
        description="Duration testing for serial-testkit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Environment variables:
  SERIAL_RPI_HOST        SSH host for RPi
  SERIAL_RPI_PASSWORD    SSH password
  SERIAL_RPI_DEVICE      Serial device on RPi
  SERIAL_LOCAL_DEVICE    Serial device on local machine
  SERIAL_TEST_DURATION   Total test duration in minutes
""",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=int(os.environ.get("SERIAL_TEST_DURATION", DEFAULT_TOTAL_DURATION_MINUTES)),
        help=f"Total test duration in minutes (default: {DEFAULT_TOTAL_DURATION_MINUTES})",
    )
    parser.add_argument(
        "--results-dir",
        default="duration-test-results",
        help="Directory for results (default: duration-test-results)",
    )
    parser.add_argument(
        "--upload",
        action="store_true",
        help="Upload code to remote before testing",
    )
    args = parser.parse_args()

    # Create configuration from environment variables
    remote_config = RemoteConfig()
    local_config = LocalConfig()

    helper = RemoteHelper(remote_config, local_config)
    results_dir = Path(args.results_dir)
    results_dir.mkdir(exist_ok=True)

    # Create CSV file with timestamp
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = results_dir / f"results_{run_id}.csv"

    # Verify connectivity
    print("Verifying remote connectivity...")
    if not helper.verify_connectivity():
        print("Failed to connect to remote RPi")
        return 1
    print("Remote connectivity OK")

    # Upload code if requested
    if args.upload:
        print("Uploading code to remote...")
        if not helper.upload_code():
            print("Failed to upload code")
            return 1
        print("Code uploaded successfully")

    # Clean up any existing test processes
    print("Cleaning up existing test processes...")
    helper.cleanup_all()

    print(f"Starting duration testing for {args.duration} minutes")
    print(f"Results will be saved to: {csv_path}")
    print(f"Testing flow control modes: {FLOW_CONTROLS}")
    print()

    start_time = time.monotonic()
    end_time = start_time + args.duration * 60
    tests_run = 0
    tests_passed = 0
    tests_failed = 0

    # Cycle indices
    duration_idx = 0
    delay_idx = 0
    flow_idx = 0

    # Open CSV file
    with open(csv_path, "w", newline="") as csvfile:
        fieldnames = [
            "test_id",
            "timestamp",
            "duration_s",
            "start_delay_s",
            "flow_control",
            "passed",
            "local_sent",
            "local_recv",
            "local_ok",
            "remote_sent",
            "remote_recv",
            "remote_ok",
            "local_role",
            "remote_role",
            "error_message",
        ]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        while time.monotonic() < end_time:
            elapsed_minutes = int((time.monotonic() - start_time) / 60)
            test_id = f"{run_id}_{tests_run:04d}_{uuid.uuid4().hex[:8]}"

            duration_s = DURATIONS[duration_idx]
            start_delay = START_DELAYS[delay_idx]
            flow_control = FLOW_CONTROLS[flow_idx]

            print(
                f"[{elapsed_minutes}/{args.duration}m] Test {tests_run + 1}: "
                f"duration={duration_s}s, delay={start_delay}s, flow={flow_control}"
            )

            timestamp = datetime.now().isoformat()
            config_str = f"duration={duration_s}s, delay={start_delay}s, flow={flow_control}"

            # Run coordinated test pair
            local_result, remote_result = helper.run_test_pair(
                duration_s,
                flow_control,
                start_delay,
            )

            tests_run += 1
            passed = local_result.success and remote_result.success
            error_message = ""

            if passed:
                tests_passed += 1
                print(
                    f"  PASSED: local={local_result.recv}/{local_result.sent}, "
                    f"remote={remote_result.recv}/{remote_result.sent}"
                )
            else:
                tests_failed += 1
                error_message = determine_error_message(local_result, remote_result)
                print(f"  FAILED: {error_message}")
                save_failed_test_logs(
                    test_id,
                    timestamp,
                    config_str,
                    error_message,
                    local_result,
                    remote_result,
                    results_dir,
                )

            # Write to CSV
            writer.writerow(
                {
                    "test_id": test_id,
                    "timestamp": timestamp,
                    "duration_s": duration_s,
                    "start_delay_s": start_delay,
                    "flow_control": flow_control,
                    "passed": passed,
                    "local_sent": local_result.sent,
                    "local_recv": local_result.recv,
                    "local_ok": local_result.ok,
                    "remote_sent": remote_result.sent,
                    "remote_recv": remote_result.recv,
                    "remote_ok": remote_result.ok,
                    "local_role": local_result.role,
                    "remote_role": remote_result.role,
                    "error_message": error_message,
                }
            )
            csvfile.flush()

            # Cycle through parameters
            flow_idx = (flow_idx + 1) % len(FLOW_CONTROLS)
            if flow_idx == 0:
                duration_idx = (duration_idx + 1) % len(DURATIONS)
                if duration_idx == 0:
                    delay_idx = (delay_idx + 1) % len(START_DELAYS)

            # Brief pause between tests
            time.sleep(2)

    # Summary
    print()
    print("=" * 60)
    print("Duration Testing Complete")
    print("=" * 60)
    print(f"Total tests: {tests_run}")
    print(f"Passed: {tests_passed}")
    print(f"Failed: {tests_failed}")
    if tests_run > 0:
        print(f"Pass rate: {tests_passed / tests_run * 100:.1f}%")
    print(f"Results saved to: {csv_path}")
    if tests_failed > 0:
        print(f"Failed test logs: {results_dir / 'logs'}")

    return 0 if tests_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
