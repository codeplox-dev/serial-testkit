# serial-testkit

Serial communication testing toolkit with client/server peering protocol.

## Project Lifecycle Commands

All project lifecycle commands MUST be run through `pop`.
Never run `python -m ...`, `pytest`, or Python scripts directly.

### Available Commands

| Command | Description |
|---------|-------------|
| `./pop setup` | Create virtualenv and install dependencies |
| `./pop test` | Run all tests (unit + integration) |
| `./pop test-unit` | Run unit tests only (fast, no socat required) |
| `./pop test-integration` | Run integration tests only (requires Linux + socat) |
| `./pop clean` | Remove cache directories |
| `./pop distclean` | Remove virtualenv and all caches |
| `./pop run [args]` | Run serialtest.py with arguments |

### Why pop?

1. **Consistent environment**: Ensures correct virtualenv and Python path
2. **Reproducibility**: Same commands work across all developer machines
3. **Simplicity**: Single entry point for all operations

### Examples

```bash
# Run as server (waits for client connections)
./pop run -d /dev/ttyAMA4 -r server

# Run as client with message count
./pop run -d /dev/ttyUSB0 -r client -n 100

# Run all tests
./pop test

# Run only unit tests (fast)
./pop test-unit
```

## Architecture

- `serialtest.py` - Thin CLI entrypoint
- `common/` - Shared protocol, encoding, and utilities
- `client/` - Client handshake and runner
- `server/` - Server handshake and runner (persistent loop)
- `session/` - Session data exchange, result tracking, and reporting
