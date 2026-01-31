# serial-testkit

A tool for testing serial communication links. Sends length-prefixed messages with CRC32 checksums and reports success rates.

Supports macOS and Linux with minimal tooling overhead. Requires Python 3.9 or newer.

## Setup

```bash
./pop setup
```

This creates a Python virtualenv and installs dependencies from `requirements.txt`.

## Use

### Loopback mode (single machine)

Run serial read / write software for a short time with a virtual pty:

```bash
./pop run loopback
```

Output shows statistics when complete:
```
sent=110776 recv=110776 ok=110776 (100.0%)
```

### Two-machine serial test

Run on both machines connected via serial cable with hardware flow control (`ctsrts`):

**Machine A (like a Linux workstation):**
```bash
sudo ./pop run -d /dev/ttyUSB0
```

**Machine B (like a Raspberry Pi):**
```bash
sudo ./pop run -d /dev/ttyAMA4
```

Both sides simultaneously send and receive messages.

### Run options

| Option | Default | Description |
|--------|---------|-------------|
| `-d`, `--device` | (required) | Serial device path |
| `-b`, `--baudrate` | 115200 | Serial baud rate |
| `-t`, `--duration` | 15 | Test duration in seconds (0 = until Ctrl-C) |
| `-f`, `--flow-control` | ctsrts | Flow control: `none`, `ctsrts`, `software` |

Example with options:
```bash
./pop run -d /dev/ttyAMA4 -b 9600 -t 60
```

Loopback mode also accepts `-b` and `-t` options.

### Operations

| Command | Description |
|---------|-------------|
| `./pop setup` | Create virtualenv and install dependencies |
| `./pop run loopback` | Run loopback test (uses pty) |
| `./pop run [args]` | Run serial test (see run options) |
| `./pop test` | Run the test suite |
| `./pop distclean` | Remove virtualenv and cache directories |
| `./pop help` | Show usage |

## Development

### Running tests

Some tests require `socat`.

```bash
./pop test
```

The two-machine test creates two pseudo-terminals connected via socat, runs an instance on each, and verifies messages are exchanged with 100% CRC pass rate.