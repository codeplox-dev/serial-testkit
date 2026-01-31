"""Message encoding/decoding for serial communication.

Messages use a simple length-prefixed protocol with CRC32 checksums:
  [4-byte length][payload][4-byte CRC32]

All integers are little-endian unsigned 32-bit.
"""

import random
from typing import Literal, Protocol

import zlib

UINT32_SIZE = 4
BYTE_ORDER: Literal["little", "big"] = "little"

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


def random_payload() -> bytes:
    """Generate a random payload of random length."""
    size = random.randint(MIN_PAYLOAD_SIZE, MAX_PAYLOAD_SIZE)
    return bytes(random.getrandbits(8) for _ in range(size))


def encode(payload: bytes) -> bytes:
    """Encode a byte payload with length prefix and CRC32 suffix."""
    length = uint32_to_bytes(len(payload))
    crc = uint32_to_bytes(zlib.crc32(payload))
    return length + payload + crc


def decode(reader: Reader) -> tuple[bytes | None, bool]:
    """Decode a message from a reader. Returns (payload, crc_ok) or (None, False) on failure."""
    length_bytes = reader.read(UINT32_SIZE)
    if len(length_bytes) < UINT32_SIZE:
        return None, False

    length = uint32_from_bytes(length_bytes)

    payload = reader.read(length)

    crc_bytes = reader.read(UINT32_SIZE)
    if len(payload) < length or len(crc_bytes) < UINT32_SIZE:
        return None, False

    expected_crc = uint32_from_bytes(crc_bytes)
    actual_crc = zlib.crc32(payload)

    return payload, expected_crc == actual_crc
