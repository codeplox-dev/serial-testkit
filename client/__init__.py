"""Client package for serial-testkit.

Contains client-specific handshake and shutdown:
- handshake: client_send_syn_wait_syn_ack, client_send_ack_with_params, client_handshake
- shutdown: client_shutdown

Note: run_client and ExitCode are not exported here to avoid circular imports
with session/. Import directly from client.runner when needed.
"""

from client.handshake import (
    client_handshake,
    client_send_ack_with_params,
    client_send_syn_wait_syn_ack,
)
from client.shutdown import client_shutdown

__all__ = [
    "client_send_syn_wait_syn_ack",
    "client_send_ack_with_params",
    "client_handshake",
    "client_shutdown",
]
