"""Session data exchange package for serial-testkit.

This package handles data exchange testing after peering completes:
- Request-response message exchange
- RTT (round-trip time) measurement
- Statistics tracking (sent, received, CRC OK/errors)
- Graceful shutdown via FIN/FIN_ACK
"""

from session.exchange import client_exchange, server_exchange, wait_for_fin
from session.report import SessionReport
from session.result import LatencyStats, SessionError, SessionResult, compute_latency_stats

__all__ = [
    "LatencyStats",
    "SessionError",
    "SessionReport",
    "SessionResult",
    "client_exchange",
    "compute_latency_stats",
    "server_exchange",
    "wait_for_fin",
]
