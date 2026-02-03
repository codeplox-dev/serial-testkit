"""Unit tests for message encoding/decoding and latency statistics."""

import io

import pytest

from common import message
from session.result import compute_latency_stats


@pytest.mark.unit
class TestUint32Conversion:
    """Test uint32 byte conversion functions."""

    def test_roundtrip_zero(self) -> None:
        assert message.uint32_from_bytes(message.uint32_to_bytes(0)) == 0

    def test_roundtrip_max(self) -> None:
        max_val = 2**32 - 1
        assert message.uint32_from_bytes(message.uint32_to_bytes(max_val)) == max_val

    def test_little_endian(self) -> None:
        # 0x01020304 in little-endian is [04, 03, 02, 01]
        assert message.uint32_to_bytes(0x01020304) == b"\x04\x03\x02\x01"

    def test_from_bytes_little_endian(self) -> None:
        assert message.uint32_from_bytes(b"\x04\x03\x02\x01") == 0x01020304


@pytest.mark.unit
class TestEncodeDecode:
    """Test message encode/decode roundtrip."""

    def test_roundtrip_empty(self) -> None:
        payload = b""
        encoded = message.encode(payload)
        reader = io.BytesIO(encoded)
        decoded, ok = message.decode(reader)
        assert ok
        assert decoded == payload

    def test_roundtrip_simple(self) -> None:
        payload = b"hello"
        encoded = message.encode(payload)
        reader = io.BytesIO(encoded)
        decoded, ok = message.decode(reader)
        assert ok
        assert decoded == payload

    def test_roundtrip_binary(self) -> None:
        payload = bytes(range(256))
        encoded = message.encode(payload)
        reader = io.BytesIO(encoded)
        decoded, ok = message.decode(reader)
        assert ok
        assert decoded == payload

    def test_encoded_format(self) -> None:
        payload = b"test"
        encoded = message.encode(payload)
        # Should be: 4-byte sync + 4-byte length + payload + 4-byte CRC
        assert len(encoded) == 4 + 4 + len(payload) + 4
        # First 4 bytes should be sync magic
        assert encoded[:4] == message.SYNC_MAGIC_BYTES
        # Next 4 bytes should be length (4 in little-endian)
        assert encoded[4:8] == b"\x04\x00\x00\x00"
        # Next bytes should be payload
        assert encoded[8:12] == b"test"

    def test_decode_truncated_length(self) -> None:
        reader = io.BytesIO(b"\x04\x00")  # Only 2 bytes, need 4
        decoded, ok = message.decode(reader)
        assert decoded is None
        assert not ok

    def test_decode_truncated_payload(self) -> None:
        # Length says 10 bytes, but only 5 provided
        reader = io.BytesIO(b"\x0a\x00\x00\x00hello")
        decoded, ok = message.decode(reader)
        assert decoded is None
        assert not ok

    def test_decode_corrupted_crc(self) -> None:
        payload = b"hello"
        encoded = bytearray(message.encode(payload))
        # Corrupt the last byte (part of CRC)
        encoded[-1] ^= 0xFF
        reader = io.BytesIO(bytes(encoded))
        decoded, ok = message.decode(reader)
        # Should return payload but with ok=False
        assert decoded == payload
        assert not ok


@pytest.mark.unit
class TestRandomPayload:
    """Test random payload generation."""

    def test_within_size_bounds(self) -> None:
        for _ in range(100):
            payload = message.random_payload()
            assert len(payload) >= message.MIN_PAYLOAD_SIZE
            assert len(payload) <= message.MAX_PAYLOAD_SIZE

    def test_returns_bytes(self) -> None:
        payload = message.random_payload()
        assert isinstance(payload, bytes)

    def test_varies(self) -> None:
        # Generate several payloads, they should not all be identical
        payloads = [message.random_payload() for _ in range(10)]
        unique = set(payloads)
        assert len(unique) > 1


@pytest.mark.unit
class TestLatencyStats:
    """Test latency statistics computation."""

    def test_empty_samples(self) -> None:
        result = compute_latency_stats([])
        assert result is None

    def test_single_sample(self) -> None:
        result = compute_latency_stats([0.010])  # 10ms in seconds
        assert result is not None
        assert result.avg_ms == pytest.approx(10.0)
        assert result.min_ms == pytest.approx(10.0)
        assert result.max_ms == pytest.approx(10.0)
        assert result.count == 1

    def test_multiple_samples(self) -> None:
        # 1ms, 2ms, 3ms, 4ms, 5ms in seconds
        samples = [0.001, 0.002, 0.003, 0.004, 0.005]
        result = compute_latency_stats(samples)
        assert result is not None
        assert result.count == 5
        assert result.min_ms == pytest.approx(1.0)
        assert result.max_ms == pytest.approx(5.0)
        assert result.avg_ms == pytest.approx(3.0)
        assert result.p50_ms == pytest.approx(3.0)

    def test_percentiles_with_outlier(self) -> None:
        # 95 samples at 1ms, 5 samples at 100ms
        # With nearest-rank: p95 index = int(95/100 * 99) = 94, p99 index = 98
        samples = [0.001] * 95 + [0.100] * 5
        result = compute_latency_stats(samples)
        assert result is not None
        assert result.p50_ms == pytest.approx(1.0)
        # p95 at index 94 is still 1ms (last of the 95 1ms samples)
        assert result.p95_ms == pytest.approx(1.0)
        # p99 at index 98 is 100ms (4th of the 5 100ms samples)
        assert result.p99_ms == pytest.approx(100.0)
