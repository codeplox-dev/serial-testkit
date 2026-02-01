"""Session result types for serial-testkit.

Contains:
- SessionError: Raised when session exchange fails
- LatencyStats: Computed latency statistics in milliseconds
- compute_latency_stats: Compute stats from RTT samples
- SessionResult: Result from session data exchange
"""

from dataclasses import dataclass, field


class SessionError(Exception):
    """Raised when session exchange fails."""

    pass


@dataclass
class LatencyStats:
    """Computed latency statistics in milliseconds."""

    count: int
    min_ms: float
    max_ms: float
    avg_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float


def compute_latency_stats(rtt_samples: list[float]) -> LatencyStats | None:
    """Compute latency statistics from RTT samples (in seconds).

    Args:
        rtt_samples: List of round-trip times in seconds.

    Returns:
        LatencyStats with percentiles in milliseconds, or None if empty.
    """
    if not rtt_samples:
        return None

    count = len(rtt_samples)
    samples_ms = sorted(s * 1000 for s in rtt_samples)

    def percentile(sorted_data: list[float], p: float) -> float:
        idx = int(p / 100 * (len(sorted_data) - 1))
        return sorted_data[idx]

    return LatencyStats(
        count=count,
        min_ms=samples_ms[0],
        max_ms=samples_ms[-1],
        avg_ms=sum(samples_ms) / count,
        p50_ms=percentile(samples_ms, 50),
        p95_ms=percentile(samples_ms, 95),
        p99_ms=percentile(samples_ms, 99),
    )


@dataclass
class SessionResult:
    """Result from session data exchange.

    Attributes:
        success: True if session completed all expected exchanges.
        sent: Number of messages successfully written.
        received: Number of messages successfully read.
        crc_ok: Number of messages with valid CRC.
        crc_errors: Number of messages with CRC failures.
        bytes_sent: Total bytes written.
        bytes_received: Total bytes read.
        rtt_samples: Round-trip times in seconds (client only).
        elapsed_s: Total session duration in seconds.
        error: Error message if session failed.
        fin_ack_received: Client: did we get FIN_ACK?
        fin_received: Server: did we get FIN?
    """

    success: bool
    sent: int = 0
    received: int = 0
    crc_ok: int = 0
    crc_errors: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    rtt_samples: list[float] = field(default_factory=list)
    elapsed_s: float = 0.0
    error: Exception | None = None
    fin_ack_received: bool = False
    fin_received: bool = False

    @property
    def crc_pass_rate(self) -> float:
        """Return CRC pass rate as percentage (0-100)."""
        if self.received == 0:
            return 0.0
        return (self.crc_ok / self.received) * 100

    @property
    def latency_stats(self) -> LatencyStats | None:
        """Compute latency statistics from RTT samples."""
        return compute_latency_stats(self.rtt_samples)

    def throughput_baud(self, bits_per_byte: int = 10) -> float:
        """Compute throughput in baud (bits/second).

        Args:
            bits_per_byte: Bits per byte including start/stop (default 10 for 8N1).

        Returns:
            Throughput in baud, or 0 if duration is 0.
        """
        if self.elapsed_s <= 0:
            return 0.0
        total_bytes = self.bytes_sent + self.bytes_received
        return (total_bytes / self.elapsed_s) * bits_per_byte

    def throughput_kbps(self) -> float:
        """Compute throughput in Kbps (kilobits/second).

        Returns:
            Throughput in Kbps, or 0 if duration is 0.
        """
        if self.elapsed_s <= 0:
            return 0.0
        total_bytes = self.bytes_sent + self.bytes_received
        return (total_bytes * 8 / self.elapsed_s) / 1000
