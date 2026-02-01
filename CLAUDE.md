# Claude Instructions for serial-testkit

This file provides instructions for AI assistants working on the serial-testkit project.

## Project Overview

serial-testkit is a serial communication testing toolkit that tests serial links between two machines (typically a workstation and a Raspberry Pi). It uses a peer establishment protocol and bidirectional data exchange to verify serial link integrity.

## Environment Setup

Before running any commands, ensure the direnv environment is loaded:

```bash
# Source the .envrc file directly
source .envrc

# Or use direnv (recommended)
direnv allow
```

All commands should be run within this environment to ensure proper configuration of environment variables and virtual environment paths.

## Tools Directory

The `tools/` directory contains helper scripts for testing. **Always use these tools instead of ad-hoc shell commands.**

### tools/remote.py

Remote test helper for managing RPi operations. Use this for:

1. **Uploading code to RPi**: `python tools/remote.py upload`
2. **Verifying connectivity**: `python tools/remote.py verify`
3. **Listing serial ports**: `python tools/remote.py ports`
4. **Cleaning up test processes**: `python tools/remote.py cleanup`
5. **Running a single test pair**: `python tools/remote.py test -t 5 -f none`

### tools/duration_test.py

Duration testing tool for comprehensive testing across flow control modes.

```bash
# Run 45-minute duration test (default)
python tools/duration_test.py

# Run with code upload
python tools/duration_test.py --upload

# Run for 10 minutes
python tools/duration_test.py --duration 10
```

## Environment Variables

Configure the remote connection using environment variables:

```bash
export SERIAL_RPI_HOST="192.168.0.22"           # RPi SSH host
export SERIAL_RPI_USER=""                       # RPi SSH user
export SERIAL_RPI_PASSWORD=""                   # RPi SSH password
export SERIAL_RPI_DEVICE="/dev/ttyAMA4"         # RPi serial device
export SERIAL_LOCAL_DEVICE="/dev/ttyUSB0"       # Local serial device
export SERIAL_TEST_DURATION="45"                # Test duration in minutes
```

## Test Results

Duration test results are saved in `duration-test-results/`:

- `results_YYYYMMDD_HHMMSS.csv`: Test results in CSV format
- `logs/`: Failed test logs with test IDs for correlation

## Key Files

- `serialtest.py`: Main serial test script
- `peering.py`: Peer establishment protocol
- `message.py`: Message encoding/decoding with CRC32
- `pop`: Shell wrapper for running tests

## Running Tests

### Unit Tests
```bash
python -m pytest test/ -v
```

### Lint and Type Checks
```bash
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy *.py
```

### Manual Single Test
```bash
# Use the remote helper instead of ad-hoc commands
python tools/remote.py test -t 5 -f none
```

## Flow Control

Two flow control modes are supported:

- **none**: No flow control - most reliable, works with any wiring
- **crtscts**: Hardware flow control using RTS/CTS signals (matches stty naming)

### Hardware Flow Control (RTS/CTS) Diagnostics

When using `crtscts` mode, the tool provides CTS line diagnostics:

- **CTS not asserted at startup**: Logged as warning - peer may not be connected or configured
- **Write timeout with CTS not asserted**: Hardware flow control issue - check RTS/CTS wiring
- **Write timeout despite CTS asserted**: Possible kernel/driver issue

If hardware flow control fails, try running with `-f none` to verify the serial link works.

**Note**: Software flow control (XON/XOFF) is not supported because XON (0x11) and XOFF (0x13) bytes appear in random test data, making it incompatible with binary data.

## Protocol Notes

1. The peer establishment protocol uses nanosecond timestamps - the earlier timestamp wins and becomes the initiator.
2. The initiator controls test duration and sends PEER_COMPLETE when done.
3. The responder runs until it receives PEER_COMPLETE or hits a safety timeout (2x duration).
4. All data messages include a test_id (derived from initiator's timestamp) to filter stale messages.
