"""Unit tests for message encoding/decoding and latency statistics."""

import io
import unittest

import message
import serialtest


class TestUint32Conversion(unittest.TestCase):
    """Test uint32 byte conversion functions."""

    def test_roundtrip_zero(self) -> None:
        self.assertEqual(message.uint32_from_bytes(message.uint32_to_bytes(0)), 0)

    def test_roundtrip_max(self) -> None:
        max_val = 2**32 - 1
        self.assertEqual(
            message.uint32_from_bytes(message.uint32_to_bytes(max_val)), max_val
        )

    def test_little_endian(self) -> None:
        # 0x01020304 in little-endian is [04, 03, 02, 01]
        self.assertEqual(message.uint32_to_bytes(0x01020304), b"\x04\x03\x02\x01")

    def test_from_bytes_little_endian(self) -> None:
        self.assertEqual(message.uint32_from_bytes(b"\x04\x03\x02\x01"), 0x01020304)


class TestEncodeDecode(unittest.TestCase):
    """Test message encode/decode roundtrip."""

    def test_roundtrip_empty(self) -> None:
        payload = b""
        encoded = message.encode(payload)
        reader = io.BytesIO(encoded)
        decoded, ok = message.decode(reader)
        self.assertTrue(ok)
        self.assertEqual(decoded, payload)

    def test_roundtrip_simple(self) -> None:
        payload = b"hello"
        encoded = message.encode(payload)
        reader = io.BytesIO(encoded)
        decoded, ok = message.decode(reader)
        self.assertTrue(ok)
        self.assertEqual(decoded, payload)

    def test_roundtrip_binary(self) -> None:
        payload = bytes(range(256))
        encoded = message.encode(payload)
        reader = io.BytesIO(encoded)
        decoded, ok = message.decode(reader)
        self.assertTrue(ok)
        self.assertEqual(decoded, payload)

    def test_encoded_format(self) -> None:
        payload = b"test"
        encoded = message.encode(payload)
        # Should be: 4-byte sync + 4-byte length + payload + 4-byte CRC
        self.assertEqual(len(encoded), 4 + 4 + len(payload) + 4)
        # First 4 bytes should be sync magic
        self.assertEqual(encoded[:4], message.SYNC_MAGIC_BYTES)
        # Next 4 bytes should be length (4 in little-endian)
        self.assertEqual(encoded[4:8], b"\x04\x00\x00\x00")
        # Next bytes should be payload
        self.assertEqual(encoded[8:12], b"test")

    def test_decode_truncated_sync(self) -> None:
        reader = io.BytesIO(b"\x00\x10")  # Only 2 bytes, need 4 for sync
        decoded, ok = message.decode(reader)
        self.assertIsNone(decoded)
        self.assertFalse(ok)

    def test_decode_truncated_length(self) -> None:
        # Valid sync magic but truncated length
        reader = io.BytesIO(message.SYNC_MAGIC_BYTES + b"\x04\x00")
        decoded, ok = message.decode(reader)
        self.assertIsNone(decoded)
        self.assertFalse(ok)

    def test_decode_truncated_payload(self) -> None:
        # Valid sync + length says 10 bytes, but only 5 provided
        reader = io.BytesIO(message.SYNC_MAGIC_BYTES + b"\x0a\x00\x00\x00hello")
        decoded, ok = message.decode(reader)
        self.assertIsNone(decoded)
        self.assertFalse(ok)

    def test_decode_corrupted_crc(self) -> None:
        payload = b"hello"
        encoded = bytearray(message.encode(payload))
        # Corrupt the last byte (part of CRC)
        encoded[-1] ^= 0xFF
        reader = io.BytesIO(bytes(encoded))
        decoded, ok = message.decode(reader)
        # Should return payload but with ok=False
        self.assertEqual(decoded, payload)
        self.assertFalse(ok)

    def test_resync_with_garbage_prefix(self) -> None:
        """Test that decoder can resync after garbage bytes."""
        payload = b"hello"
        encoded = message.encode(payload)
        # Prepend some garbage bytes
        garbage = b"\x00\x01\x02\x03\x04\x05"
        reader = io.BytesIO(garbage + encoded)
        decoded, ok = message.decode(reader)
        self.assertTrue(ok)
        self.assertEqual(decoded, payload)

    def test_resync_with_partial_message_prefix(self) -> None:
        """Test that decoder can resync when stream starts mid-message."""
        payload = b"hello"
        encoded = message.encode(payload)
        # Simulate starting mid-message: partial sync + garbage + valid message
        partial = b"\x5A\x10\x00\xff\xff"  # Looks like part of sync magic
        reader = io.BytesIO(partial + encoded)
        decoded, ok = message.decode(reader)
        self.assertTrue(ok)
        self.assertEqual(decoded, payload)

    def test_decode_huge_length_rejected(self) -> None:
        """Test that unreasonably large length values are rejected."""
        # Valid sync but huge length
        huge_length = message.uint32_to_bytes(message.MAX_MESSAGE_LENGTH + 1)
        reader = io.BytesIO(message.SYNC_MAGIC_BYTES + huge_length + b"x" * 100)
        decoded, ok = message.decode(reader)
        self.assertIsNone(decoded)
        self.assertFalse(ok)


class TestRandomPayload(unittest.TestCase):
    """Test random payload generation."""

    def test_within_size_bounds(self) -> None:
        for _ in range(100):
            payload = message.random_payload()
            self.assertGreaterEqual(len(payload), message.MIN_PAYLOAD_SIZE)
            self.assertLessEqual(len(payload), message.MAX_PAYLOAD_SIZE)

    def test_returns_bytes(self) -> None:
        payload = message.random_payload()
        self.assertIsInstance(payload, bytes)

    def test_varies(self) -> None:
        # Generate several payloads, they should not all be identical
        payloads = [message.random_payload() for _ in range(10)]
        unique = set(payloads)
        self.assertGreater(len(unique), 1)


class TestLatencyStats(unittest.TestCase):
    """Test latency statistics computation."""

    def test_empty_samples(self) -> None:
        result = serialtest.compute_latency_stats([])
        self.assertIsNone(result)

    def test_single_sample(self) -> None:
        result = serialtest.compute_latency_stats([0.010])  # 10ms in seconds
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.avg_ms, 10.0)
        self.assertAlmostEqual(result.min_ms, 10.0)
        self.assertAlmostEqual(result.max_ms, 10.0)
        self.assertEqual(result.count, 1)

    def test_multiple_samples(self) -> None:
        # 1ms, 2ms, 3ms, 4ms, 5ms in seconds
        samples = [0.001, 0.002, 0.003, 0.004, 0.005]
        result = serialtest.compute_latency_stats(samples)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.count, 5)
        self.assertAlmostEqual(result.min_ms, 1.0)
        self.assertAlmostEqual(result.max_ms, 5.0)
        self.assertAlmostEqual(result.avg_ms, 3.0)
        self.assertAlmostEqual(result.p50_ms, 3.0)

    def test_percentiles_with_outlier(self) -> None:
        # 95 samples at 1ms, 5 samples at 100ms
        # With nearest-rank: p95 index = int(95/100 * 99) = 94, p99 index = 98
        samples = [0.001] * 95 + [0.100] * 5
        result = serialtest.compute_latency_stats(samples)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.p50_ms, 1.0)
        # p95 at index 94 is still 1ms (last of the 95 1ms samples)
        self.assertAlmostEqual(result.p95_ms, 1.0)
        # p99 at index 98 is 100ms (4th of the 5 100ms samples)
        self.assertAlmostEqual(result.p99_ms, 100.0)


if __name__ == "__main__":
    unittest.main()
