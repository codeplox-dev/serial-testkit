"""Message encoding/decoding for serial communication.

Messages use a sync-prefixed, length-prefixed protocol with CRC32 checksums:
  [4-byte sync magic][4-byte length][payload][4-byte CRC32]

The sync magic allows recovery from framing errors (e.g., when connecting
to a stream mid-message or after buffer corruption).

All integers are little-endian unsigned 32-bit.
"""

import logging
import random
import zlib
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

UINT32_SIZE = 4
UINT64_SIZE = 8
BYTE_ORDER: Literal["little", "big"] = "little"

# Sync magic for message framing (chosen to be unlikely in random data)
SYNC_MAGIC = 0x5E5A1000
SYNC_MAGIC_BYTES = SYNC_MAGIC.to_bytes(UINT32_SIZE, BYTE_ORDER, signed=False)

# Maximum message length (prevents huge allocations on corrupted length)
MAX_MESSAGE_LENGTH = 4096

# Maximum bytes to scan when resyncing (prevents infinite loop on garbage)
MAX_RESYNC_BYTES = 8192

# Payload size range for random messages
MIN_PAYLOAD_SIZE = 16
MAX_PAYLOAD_SIZE = 256


class Reader(Protocol):
    """Protocol for objects that can read bytes."""

    def read(self, size: int) -> bytes: ...


def uint32_to_bytes(value: int) -> bytes:
    """Encode unsigned 32-bit int as little-endian bytes."""
    return value.to_bytes(UINT32_SIZE, BYTE_ORDER, signed=False)


def uint32_from_bytes(data: bytes) -> int:
    """Decode little-endian bytes to unsigned 32-bit int."""
    return int.from_bytes(data, BYTE_ORDER, signed=False)


def uint64_to_bytes(value: int) -> bytes:
    """Encode unsigned 64-bit int as little-endian bytes."""
    return value.to_bytes(UINT64_SIZE, BYTE_ORDER, signed=False)


def uint64_from_bytes(data: bytes) -> int:
    """Decode little-endian bytes to unsigned 64-bit int."""
    return int.from_bytes(data, BYTE_ORDER, signed=False)


def random_payload() -> bytes:
    """Generate a random payload of random length."""
    size = random.randint(MIN_PAYLOAD_SIZE, MAX_PAYLOAD_SIZE)
    return bytes(random.getrandbits(8) for _ in range(size))


def encode(payload: bytes) -> bytes:
    """Encode a byte payload with sync magic, length prefix and CRC32 suffix."""
    length = uint32_to_bytes(len(payload))
    crc = uint32_to_bytes(zlib.crc32(payload))
    return SYNC_MAGIC_BYTES + length + payload + crc


def decode(reader: Reader) -> tuple[bytes | None, bool]:
    """Decode a message from a reader with resync capability.

    Returns (payload, crc_ok) or (None, False) on failure/timeout.

    If the stream is misaligned (e.g., due to connecting mid-message or
    buffer corruption), this function will scan for the sync magic and
    resynchronize.
    """
    # Read potential sync magic
    sync_bytes = reader.read(UINT32_SIZE)
    if len(sync_bytes) < UINT32_SIZE:
        return None, False

    # Check if we have valid sync magic
    bytes_scanned = 0
    while sync_bytes != SYNC_MAGIC_BYTES:
        if bytes_scanned >= MAX_RESYNC_BYTES:
            logger.warning(f"Failed to resync after scanning {bytes_scanned} bytes")
            return None, False

        # Shift by one byte and read another
        next_byte = reader.read(1)
        if len(next_byte) < 1:
            return None, False

        sync_bytes = sync_bytes[1:] + next_byte
        bytes_scanned += 1

    if bytes_scanned > 0:
        logger.debug(f"Resynced after skipping {bytes_scanned} bytes")

    # Read length
    length_bytes = reader.read(UINT32_SIZE)
    if len(length_bytes) < UINT32_SIZE:
        return None, False

    length = uint32_from_bytes(length_bytes)

    # Sanity check length to avoid huge allocations
    if length > MAX_MESSAGE_LENGTH:
        logger.warning(f"Message length {length} exceeds max {MAX_MESSAGE_LENGTH}, resyncing")
        return None, False

    # Read payload
    payload = reader.read(length)

    # Read CRC
    crc_bytes = reader.read(UINT32_SIZE)
    if len(payload) < length or len(crc_bytes) < UINT32_SIZE:
        return None, False

    expected_crc = uint32_from_bytes(crc_bytes)
    actual_crc = zlib.crc32(payload)

    return payload, expected_crc == actual_crc
