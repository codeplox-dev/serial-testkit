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
throughput: 111,000,134 baud (88800.11 Kbps) over 15.0s
latency: avg=0.02ms min=0.02ms max=4.07ms
         p50=0.02ms p95=0.03ms p99=0.04ms (n=110776)
```

Throughput shows baud (line rate for 8N1 UART: 10 bits per byte including start/stop bits) and Kbps (actual data rate).

### Two-machine serial test

Run on both machines connected via serial cable with hardware flow control (`rtscts`):

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
| `-f`, `--flow-control` | rtscts | Flow control: `none`, `rtscts`, `software` |
| `-w`, `--warmup` | 20 | Warmup period in seconds to wait for peer |
| `--flush/--no-flush` | flush | Flush serial buffers on start |

During the warmup period, write timeouts are tolerated while waiting for the peer to start. This handles the case where the two machines don't start at exactly the same time. After warmup, write timeouts are treated as errors.

Example with options:
```bash
./pop run -d /dev/ttyAMA4 -b 9600 -t 60
```

Loopback mode also accepts `-b`, `-t`, `-w`, and `--flush/--no-flush` options.

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