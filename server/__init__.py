"""Server package for serial-testkit.

Contains server-specific handshake and shutdown:
- handshake: server_wait_for_syn, server_send_syn_ack_wait_ack, server_handshake
- shutdown: server_shutdown

Note: run_server is not exported here to avoid circular imports with session/.
Import directly from server.runner when needed.
"""

from server.handshake import (
    server_handshake,
    server_send_syn_ack_wait_ack,
    server_wait_for_syn,
)
from server.shutdown import server_shutdown

__all__ = [
    "server_wait_for_syn",
    "server_send_syn_ack_wait_ack",
    "server_handshake",
    "server_shutdown",
]
