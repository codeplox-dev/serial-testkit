# serial-testkit

A tool for testing serial communication links. Sends length-prefixed messages with CRC32 checksums and reports success rates.

## Setup

```bash
./pop setup
```

This creates a Python virtualenv and installs dependencies from `requirements.txt`.

## Use

### Loopback mode (single machine)

Test the software without hardware using a virtual pty (Linux/macOS only):

```bash
./pop run-loop
```

Output shows statistics when complete:
```
sent=110776 recv=110776 ok=110776 (100.0%)
```

### Two-machine serial test

Run on both machines connected via serial cable:

**Machine A:**
```bash
SERIAL_DEVICE=/dev/ttyAMA4 ./pop run
```

**Machine B:**
```bash
SERIAL_DEVICE=/dev/ttyUSB0 ./pop run
```

Both sides simultaneously send and receive messages.

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SERIAL_DEVICE` | (required for `run`) | Serial device path |
| `BAUDRATE` | 115200 | Serial baud rate |
| `RUN_DURATION_S` | 15 | Test duration in seconds (0 = until Ctrl-C) |
| `MSG_COUNT` | 50 | (unused, reserved) |

### Operations

| Command | Description |
|---------|-------------|
| `./pop setup` | Create virtualenv and install dependencies |
| `./pop run-loop` | Run loopback test |
| `./pop run` | Run two-machine test (requires `SERIAL_DEVICE`) |
| `./pop test` | Run the test suite |
| `./pop distclean` | Remove virtualenv and cache directories |
| `./pop help` | Show usage and environment variables |

## Development

### Running tests

```bash
./pop test
```

The test creates two pseudo-terminals connected via socat, runs an instance on each, and verifies messages are exchanged with 100% CRC pass rate.

Requires `socat`:
```bash
# Debian/Ubuntu
sudo apt install socat

# Arch
sudo pacman -S socat
```
