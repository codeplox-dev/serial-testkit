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

Test with a virtual pty (no hardware needed):

```bash
./pop run loopback
```

Output:
```
sent=110776 recv=110776 ok=110776 (100.0%)
throughput: 111,000,134 baud (88800.11 Kbps) over 15.0s
latency: avg=0.02ms min=0.02ms max=4.07ms
         p50=0.02ms p95=0.03ms p99=0.04ms (n=110776)
```

### Two-machine serial test

Run on both machines connected via serial cable with hardware flow control (`crtscts`):

**Machine A** (e.g., workstation with USB-serial adapter):
```bash
sudo ./pop run -d /dev/ttyUSB0
```

**Machine B** (e.g., Raspberry Pi):
```bash
sudo ./pop run -d /dev/ttyAMA4
```

Both peers simultaneously send and receive messages, reporting success rates when done.

### Run options

| Option | Default | Description |
|--------|---------|-------------|
| `-d`, `--device` | (required) | Serial device path |
| `-b`, `--baudrate` | 115200 | Serial baud rate |
| `-t`, `--duration` | 15 | Test duration in seconds |
| `-f`, `--flow-control` | crtscts | Flow control: `none` or `crtscts` |
| `-w`, `--warmup` | 20 | Seconds to wait for peer before treating timeouts as errors |
| `--flush/--no-flush` | flush | Flush serial buffers on start |

The first peer to connect sets the test duration for both sides.

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

## Troubleshooting

### Hardware Flow Control (RTS/CTS) Issues

When using `crtscts` flow control with FTDI USB-serial adapters (like the FT232R), you may encounter write timeouts or communication failures even with correct wiring.

#### Why FTDI Flow Control Fails

Buffer overflows occur due to a combination of small hardware buffers and OS scheduling latency:

1. **Small FTDI buffer**: The chip has only ~62 usable bytes. RTS is asserted when 32 bytes remain—leaving little margin for delays.

2. **OS scheduling latency**: Linux provides no real-time guarantees. When the system is busy, the kernel may not drain the UART buffer before it overflows.

3. **Default 16ms latency timer**: The FTDI chip batches data for up to 16ms before sending to the host, delaying flow control response.

4. **High throughput**: At 115200 baud (~11.5 KB/s), the 62-byte buffer fills in ~5ms—faster than the default latency timer can respond.

Additionally, FTDI chips may transmit 0-3 extra characters after CTS is deasserted.

#### Diagnostic Messages

With `crtscts` mode, the tool reports CTS line state on errors:

- `CTS not asserted at startup` — peer not connected or not using flow control
- `Write timeout - CTS not asserted` — check RTS/CTS wiring
- `Write timeout despite CTS asserted` — kernel/driver issue

#### Solution: Reduce Latency Timer

The tool automatically sets the FTDI latency timer to 1ms when run as root. To disable: `--no-latency-fix`

To configure manually:
```bash
cat /sys/bus/usb-serial/devices/ttyUSB0/latency_timer   # check current
echo 1 | sudo tee /sys/bus/usb-serial/devices/ttyUSB0/latency_timer  # set to 1ms
```

This setting resets when the device is disconnected.

#### Other Troubleshooting Steps

1. **Check wiring**: RTS/CTS must be cross-connected (host RTS → device CTS, device RTS → host CTS)
2. **Try `-f none`**: Verify basic communication works without flow control
3. **Check driver conflicts**: `ls -la /sys/bus/usb/devices/*/driver | grep -i ftdi`
4. **Blacklist conflicting drivers**: `echo "blacklist lpvo_usb_gpib" | sudo tee /etc/modprobe.d/blacklist-gpib.conf`
5. **Check permissions**: User needs access to `/dev/ttyUSB*`

#### References

- [FTDI AN232B-04: Data Latency Flow](https://www.ftdichip.com/Documents/AppNotes/AN232B-04_DataLatencyFlow.pdf) — latency timer and flow control
- [Linux Kernel Bug #197109](https://bugzilla.kernel.org/show_bug.cgi?id=197109) — ftdi_sio RTS/CTS issues

### Tools Used

After the initial code was written for this tool, Claude code was used to test, diagnose, and iteratively improve reliability related to the mentioned RTS / CTS problem. It also helped significantly with the peering solution implemented to maintain a single-entrypoint test program.
