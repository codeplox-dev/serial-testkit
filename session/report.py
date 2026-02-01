"""Session reporting for serial-testkit.

Contains:
- SessionReport: Report after session data exchange completes
"""

from dataclasses import dataclass

from common.report import Report
from session.result import SessionResult

# Minimum duration for reliable throughput measurement
THROUGHPUT_MIN_DURATION_S = 30


@dataclass
class SessionReport(Report):
    """Report after session data exchange completes."""

    result: SessionResult

    def print(self) -> None:
        """Print the session report."""
        r = self.result

        # Status line
        if r.success:
            print(
                f"Session: SUCCESS ({r.sent} sent, {r.received} received, "
                f"{r.crc_ok} ok, {r.crc_errors} errors)"
            )
        else:
            print(f"Session: FAILED ({r.error})")
            if r.sent > 0 or r.received > 0:
                print(
                    f"         ({r.sent} sent, {r.received} received, "
                    f"{r.crc_ok} ok, {r.crc_errors} errors)"
                )
            return  # Don't print throughput/latency for failed sessions

        # Throughput line (only if we have meaningful data)
        if r.elapsed_s > 0 and (r.bytes_sent > 0 or r.bytes_received > 0):
            baud = r.throughput_baud()
            kbps = r.throughput_kbps()
            print(f"Throughput: {baud:,.0f} baud ({kbps:.2f} Kbps) over {r.elapsed_s:.1f}s")
            if r.elapsed_s < THROUGHPUT_MIN_DURATION_S:
                print(
                    "(Note: throughput from short test may not reflect sustained performance)"
                )

        # Latency lines (only if we have RTT samples)
        latency = r.latency_stats
        if latency:
            print(
                f"Latency: avg={latency.avg_ms:.2f}ms min={latency.min_ms:.2f}ms "
                f"max={latency.max_ms:.2f}ms"
            )
            print(
                f"         p50={latency.p50_ms:.2f}ms p95={latency.p95_ms:.2f}ms "
                f"p99={latency.p99_ms:.2f}ms (n={latency.count})"
            )

    def success(self) -> bool:
        """Return True if session succeeded with 100% CRC pass rate."""
        return self.result.success and self.result.crc_pass_rate == 100.0
