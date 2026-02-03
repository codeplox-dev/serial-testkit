# serial-testkit

A tool for testing serial communication links using a client/server protocol. Implements a TCP-like handshake for peering and then tests message transfer with CRC32 checksums.

Supports macOS and Linux. Requires Python 3.9 or newer.

## Setup

```bash
./pop setup
```

This creates a Python virtualenv and installs dependencies from `requirements.txt`.

## Use

### Two-machine serial test

Connect two machines via serial cable. One runs as **server** (waits for connections), the other as **client** (initiates connection and controls the test).

**Machine A - Server (e.g., Raspberry Pi):**
```bash
sudo ./pop run -d /dev/ttyAMA4 -r server
```

**Machine B - Client (e.g., Linux workstation):**
```bash
sudo ./pop run -d /dev/ttyUSB0 -r client -n 100
```

The client sends 100 request messages, the server echoes each back, and the client measures round-trip time (RTT) for latency statistics.

### Example output

**Client output:**
```
Peering: SUCCESS (id=b7d61c40, role=client)
Session: SUCCESS (100 sent, 100 received, 100 ok, 0 errors)
Throughput: 104,867 baud (83.89 Kbps) over 2.8s
Latency: avg=27.90ms min=12.93ms max=47.95ms
         p50=27.00ms p95=43.94ms p99=43.94ms (n=100)
```

**Server output:**
```
Peering: SUCCESS (id=b7d61c40, role=server, msg_count=100)
Session: SUCCESS (100 sent, 100 received, 100 ok, 0 errors)
```

- **Peering**: TCP-like 3-way handshake (SYN → SYN_ACK → ACK with session params)
- **Session**: Request-response data exchange with CRC verification
- **Throughput**: Baud (line rate for 8N1: 10 bits/byte) and Kbps (data rate)
- **Latency**: RTT statistics from client-measured round trips

### CLI options

| Option | Default | Description |
|--------|---------|-------------|
| `-d`, `--device` | (required) | Serial device path |
| `-r`, `--role` | (required) | Role: `client` or `server` |
| `-n`, `--msg-count` | 100 | Number of messages to exchange (client only) |
| `-b`, `--baudrate` | 115200 | Serial baud rate |
| `-f`, `--flow-control` | none | Flow control: `none` or `rtscts` |
| `-w`, `--handshake-timeout` | 30 | Handshake timeout in seconds |
| `--no-latency-fix` | (off) | Disable FTDI latency timer optimization |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SERIAL_LOG_INTERVAL` | 100 | Progress logging interval (every Nth message) |

By default, per-message logging is suppressed to reduce noise. Progress is logged every 100 messages at DEBUG level. Set `SERIAL_LOG_INTERVAL=1` to log every message, or use TRACE level logging for full verbosity.

### Protocol

1. **Peering phase**: Client sends SYN with connection ID, server responds with SYN_ACK, client sends ACK with session parameters (msg_count)
2. **Session phase**: Client sends DATA messages, server echoes each back. Client measures RTT for each round trip
3. **Shutdown phase**: Client sends FIN, server responds with FIN_ACK

All messages use wire format: `[4-byte length][payload][4-byte CRC32]`

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | Success - all messages exchanged with 100% CRC pass |
| 1 | Peering failed - handshake timeout or error |
| 2 | No data - session completed but no messages received |
| 3 | CRC errors - some messages had CRC failures |

## Operations

| Command | Description |
|---------|-------------|
| `./pop setup` | Create virtualenv and install dependencies |
| `./pop run [args]` | Run serial test (see CLI options) |
| `./pop test` | Run all tests (unit + integration) |
| `./pop test-unit` | Run unit tests only (fast, no socat required) |
| `./pop test-integration` | Run integration tests (requires Linux + socat) |
| `./pop clean` | Remove cache directories |
| `./pop distclean` | Remove virtualenv and all caches |

## Development

### Running tests

Integration tests require `socat` to create connected PTY pairs.

```bash
# All tests
./pop test

# Unit tests only (fast)
./pop test-unit

# Integration tests only
./pop test-integration
```

### Architecture

- `serialtest.py` - Thin CLI entrypoint
- `common/` - Shared protocol, encoding, and utilities
- `client/` - Client handshake and runner
- `server/` - Server handshake and runner (persistent loop)
- `session/` - Session data exchange, result tracking, and reporting$

## Tools

Claude Code w/ the Opus 4.5 model was used to refactor my original code into a client-server model with peering protocol and to then implement the in-session testing.