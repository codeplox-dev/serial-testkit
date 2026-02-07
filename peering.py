"""Peer establishment protocol for serial communication tests.

This module handles the initial handshake between two peers to determine
which one is the initiator (controls test duration) and which is the responder.

Protocol:
1. Both sides send PEER_INIT with nanosecond timestamp
2. Earlier timestamp wins (becomes initiator)
3. Responder flushes buffers and sends PEER_ACK
4. Initiator waits for PEER_ACK before starting test
5. All subsequent messages include a test_id derived from initiator's timestamp
"""

import logging
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

import serial

from common import message

logger = logging.getLogger(__name__)

# Protocol message magic codes (64-bit hex values to avoid collision with random data)
# These are encoded as little-endian uint64 at the start of each control message
PEER_INIT_MAGIC = 0x5E91A1_1417_0001  # PEER_INIT: timestamp + duration
PEER_ACK_MAGIC = 0x5E91A1_1417_0002  # PEER_ACK: test_id
PEER_COMPLETE_MAGIC = 0x5E91A1_1417_0003  # PEER_COMPLETE: test_id


class MessageResult(Enum):
    """Result of classifying a received message during test phase."""

    COMPLETE = auto()  # Valid PEER_COMPLETE with matching test_id
    DATA = auto()  # Valid data message with matching test_id
    PEER_INIT = auto()  # PEER_INIT received (responder should re-send ACK)
    IGNORE = auto()  # Invalid or mismatched message


class PeerDevice(Protocol):
    """Protocol for devices that can participate in peering."""

    def write_msg(self, payload: bytes) -> int: ...
    def read_msg(self) -> tuple[bytes | None, bool]: ...
    def flush_buffers(self) -> None: ...


@dataclass
class PeerInfo:
    """Result of peer establishment."""

    is_initiator: bool
    test_id: int
    duration_s: int  # Effective test duration (initiator's value)
    peer_timestamp_ns: int = 0  # Peer's timestamp (for responder to re-send ACK)


# Safety timeout multiplier for responder (prevents infinite loop if PEER_COMPLETE lost)
RESPONDER_TIMEOUT_MULTIPLIER = 2


# -----------------------------------------------------------------------------
# Test ID generation
# -----------------------------------------------------------------------------


def make_test_id(timestamp_ns: int) -> int:
    """Create a test ID from the initiator's nanosecond timestamp.

    Uses a simple hash to create a 64-bit test ID.
    """
    return hash(timestamp_ns) & 0xFFFFFFFFFFFFFFFF


# -----------------------------------------------------------------------------
# Message encoding
# -----------------------------------------------------------------------------


def make_peer_init(timestamp_ns: int, duration_s: int) -> bytes:
    """Create a PEER_INIT payload with nanosecond timestamp and duration."""
    return (
        message.uint64_to_bytes(PEER_INIT_MAGIC)
        + message.uint64_to_bytes(timestamp_ns)
        + message.uint32_to_bytes(duration_s)
    )


def make_peer_ack(test_id: int) -> bytes:
    """Create a PEER_ACK payload with the agreed test ID."""
    return message.uint64_to_bytes(PEER_ACK_MAGIC) + message.uint64_to_bytes(test_id)


def make_peer_complete(test_id: int) -> bytes:
    """Create a PEER_COMPLETE payload with test ID."""
    return message.uint64_to_bytes(PEER_COMPLETE_MAGIC) + message.uint64_to_bytes(test_id)


def make_data_msg(test_id: int) -> bytes:
    """Create a data message payload with test ID prefix."""
    return message.uint64_to_bytes(test_id) + message.random_payload()


# -----------------------------------------------------------------------------
# Message parsing
# -----------------------------------------------------------------------------


# Expected payload sizes for exact matching
_PEER_INIT_SIZE = message.UINT64_SIZE * 2 + message.UINT32_SIZE  # magic + timestamp + duration
_PEER_ACK_SIZE = message.UINT64_SIZE * 2  # magic + test_id
_PEER_COMPLETE_SIZE = message.UINT64_SIZE * 2  # magic + test_id


def parse_peer_init(payload: bytes) -> tuple[int, int] | None:
    """Parse a PEER_INIT payload, returning (timestamp_ns, duration_s) or None."""
    if len(payload) != _PEER_INIT_SIZE:
        return None
    magic = message.uint64_from_bytes(payload[: message.UINT64_SIZE])
    if magic != PEER_INIT_MAGIC:
        return None
    timestamp_ns = message.uint64_from_bytes(
        payload[message.UINT64_SIZE : message.UINT64_SIZE * 2]
    )
    duration_s = message.uint32_from_bytes(payload[message.UINT64_SIZE * 2 :])
    return timestamp_ns, duration_s


def parse_peer_ack(payload: bytes) -> int | None:
    """Parse a PEER_ACK payload, returning the test ID or None if invalid."""
    if len(payload) != _PEER_ACK_SIZE:
        return None
    magic = message.uint64_from_bytes(payload[: message.UINT64_SIZE])
    if magic != PEER_ACK_MAGIC:
        return None
    return message.uint64_from_bytes(payload[message.UINT64_SIZE :])


def parse_peer_complete(payload: bytes) -> int | None:
    """Parse a PEER_COMPLETE payload, returning the test ID or None if invalid."""
    if len(payload) != _PEER_COMPLETE_SIZE:
        return None
    magic = message.uint64_from_bytes(payload[: message.UINT64_SIZE])
    if magic != PEER_COMPLETE_MAGIC:
        return None
    return message.uint64_from_bytes(payload[message.UINT64_SIZE :])


def parse_data_msg(payload: bytes, expected_test_id: int) -> tuple[bytes | None, bool]:
    """Parse a data message, returning (data, matches_test_id).

    Returns (None, False) if payload is too short.
    Returns (data, False) if test ID doesn't match.
    Returns (data, True) if test ID matches.
    """
    if len(payload) < message.UINT64_SIZE:
        return None, False
    msg_test_id = message.uint64_from_bytes(payload[: message.UINT64_SIZE])
    data = payload[message.UINT64_SIZE :]
    return data, msg_test_id == expected_test_id


# -----------------------------------------------------------------------------
# Message classification
# -----------------------------------------------------------------------------


def classify_test_message(payload: bytes, test_id: int) -> MessageResult:
    """Classify and validate a received message during test phase."""
    # Check for PEER_COMPLETE
    complete_test_id = parse_peer_complete(payload)
    if complete_test_id is not None:
        if complete_test_id == test_id:
            return MessageResult.COMPLETE
        logger.debug("Ignoring PEER_COMPLETE with wrong test_id")
        return MessageResult.IGNORE

    # Check for PEER_INIT (initiator may not have received ACK)
    peer_init_result = parse_peer_init(payload)
    if peer_init_result is not None:
        return MessageResult.PEER_INIT

    # Ignore stale PEER_ACK
    if parse_peer_ack(payload) is not None:
        logger.debug("Ignoring stale PEER_ACK during test")
        return MessageResult.IGNORE

    # Parse data message
    data, matches_test_id = parse_data_msg(payload, test_id)
    if data is None:
        logger.debug("Ignoring malformed data message")
        return MessageResult.IGNORE
    if not matches_test_id:
        logger.debug("Ignoring data message with wrong test_id")
        return MessageResult.IGNORE

    return MessageResult.DATA


# -----------------------------------------------------------------------------
# Peer establishment
# -----------------------------------------------------------------------------


def establish_peer(
    dev: PeerDevice,
    our_timestamp_ns: int,
    duration_s: int,
    timeout_s: int,
) -> PeerInfo | None:
    """Establish peer connection and determine roles.

    Both sides race to initiate; the earlier nanosecond timestamp wins.
    The winner becomes the initiator (controls test duration), the other
    becomes the responder (waits for PEER_COMPLETE).

    Args:
        dev: Device to communicate over
        our_timestamp_ns: Our nanosecond timestamp for role determination
        duration_s: Desired test duration (used if we become initiator)
        timeout_s: Maximum time to wait for peer

    Returns:
        PeerInfo on success, None on timeout/failure.
    """
    logger.info(f"Waiting for peer (timeout: {timeout_s}s)...")
    start = time.monotonic()
    peer_timestamp_ns: int | None = None
    is_initiator: bool = False
    test_id: int = 0

    while time.monotonic() - start < timeout_s:
        # Send PEER_INIT with our timestamp and duration
        try:
            dev.write_msg(make_peer_init(our_timestamp_ns, duration_s))
        except serial.SerialTimeoutException:
            continue

        # Try to read a message
        payload, _ = dev.read_msg()
        if payload is None:
            continue

        # Check for PEER_INIT from peer
        peer_init_result = parse_peer_init(payload)
        if peer_init_result is not None and peer_timestamp_ns is None:
            their_ts, their_duration = peer_init_result
            peer_timestamp_ns = their_ts
            # Earlier timestamp wins
            if our_timestamp_ns < their_ts:
                is_initiator = True
                test_id = make_test_id(our_timestamp_ns)
                logger.info(
                    f"Peer detected (initiator, test_id={test_id:016x}, "
                    f"duration: {duration_s}s)"
                )
                # Continue to wait for PEER_ACK
            else:
                # Responder uses initiator's duration
                is_initiator = False
                test_id = make_test_id(their_ts)
                logger.info(
                    f"Peer detected (responder, test_id={test_id:016x}, "
                    f"test duration: {their_duration}s)"
                )
                # Flush buffers and send PEER_ACK, then we're done
                dev.flush_buffers()
                dev.write_msg(make_peer_ack(test_id))
                return PeerInfo(
                    is_initiator=False,
                    test_id=test_id,
                    duration_s=their_duration,  # Use initiator's duration
                    peer_timestamp_ns=their_ts,
                )
            continue

        # Check for PEER_ACK (only initiator expects this)
        ack_test_id = parse_peer_ack(payload)
        if ack_test_id is not None and is_initiator and ack_test_id == test_id:
            # Don't flush here - responder may have already sent test data
            # that would be discarded. Accept some stale PEER_INIT messages
            # in the test loop (they'll be classified and handled).
            logger.debug("Received PEER_ACK, starting test")
            return PeerInfo(is_initiator=True, test_id=test_id, duration_s=duration_s)

    # Establishment failed
    if peer_timestamp_ns is None:
        logger.error(f"No peer detected within {timeout_s}s timeout")
    else:
        logger.error("Peer detected but establishment incomplete")
    return None
