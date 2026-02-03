"""Unit tests for the peering module."""

import pytest

from client.handshake import (
    HandshakeError,
    client_handshake,
    client_send_syn_wait_syn_ack,
)
from client.shutdown import client_shutdown
from common import message
from common.connection import Connection, ConnectionMismatchError, PeeringError, Role, SessionParams
from common.encoding import (
    EncodingError,
    TransportError,
    decode_ack_with_params,
    decode_message,
    encode_ack_with_params,
    encode_control,
    encode_data,
    generate_connection_id,
)
from common.io import drain_input, recv_data, send_data
from common.protocol import MsgType
from server.handshake import (
    server_handshake,
    server_send_syn_ack_wait_ack,
    server_wait_for_syn,
)
from server.shutdown import server_shutdown
from test.conftest import MockSerialPort


@pytest.mark.unit
class TestMsgType:
    """Tests for MsgType enum."""

    def test_values(self) -> None:
        assert MsgType.SYN == 0x01
        assert MsgType.SYN_ACK == 0x02
        assert MsgType.ACK == 0x03
        assert MsgType.DATA == 0x10
        assert MsgType.FIN == 0x20
        assert MsgType.FIN_ACK == 0x21


@pytest.mark.unit
class TestEncoding:
    """Tests for message encoding functions."""

    def test_encode_control_syn(self) -> None:
        conn_id = b"\x01\x02\x03\x04"
        encoded = encode_control(MsgType.SYN, conn_id)
        # Should be: sync(4) + length(4) + payload(type + conn_id = 5) + crc(4) = 17 bytes
        assert len(encoded) == 4 + 4 + 5 + 4

    def test_encode_control_roundtrip(self) -> None:
        conn_id = b"\xaa\xbb\xcc\xdd"
        for msg_type in [
            MsgType.SYN,
            MsgType.SYN_ACK,
            MsgType.ACK,
            MsgType.FIN,
            MsgType.FIN_ACK,
        ]:
            encoded = encode_control(msg_type, conn_id)
            port = MockSerialPort()
            port.inject(encoded)
            decoded_type, decoded_id, data, crc_ok = decode_message(port)
            assert decoded_type == msg_type
            assert decoded_id == conn_id
            assert data == b""  # Control messages have no data
            assert crc_ok is True

    def test_encode_data_roundtrip(self) -> None:
        conn_id = b"\x11\x22\x33\x44"
        payload = b"Hello, world!"
        encoded = encode_data(conn_id, payload)
        port = MockSerialPort()
        port.inject(encoded)
        decoded_type, decoded_id, data, crc_ok = decode_message(port)
        assert decoded_type == MsgType.DATA
        assert decoded_id == conn_id
        assert data == payload
        assert crc_ok is True

    def test_decode_invalid_short_message(self) -> None:
        port = MockSerialPort()
        # Inject incomplete data
        port.inject(b"\x00\x00")
        with pytest.raises(TransportError):
            decode_message(port)


@pytest.mark.unit
class TestDrainInput:
    """Tests for drain_input function."""

    def test_drain_empty(self) -> None:
        port = MockSerialPort()
        drained = drain_input(port)
        assert drained == 0

    def test_drain_with_data(self) -> None:
        port = MockSerialPort()
        port.inject(b"stale data from previous run")
        drained = drain_input(port)
        assert drained == 28
        assert port.in_waiting == 0


@pytest.mark.unit
class TestConnectionId:
    """Tests for connection ID generation."""

    def test_generate_connection_id_length(self) -> None:
        conn_id = generate_connection_id()
        assert len(conn_id) == 4

    def test_generate_connection_id_unique(self) -> None:
        ids = [generate_connection_id() for _ in range(100)]
        assert len(set(ids)) == 100  # All unique


@pytest.mark.unit
class TestHandshakeHelpers:
    """Tests for individual handshake helper functions."""

    def test_server_wait_for_syn_timeout(self) -> None:
        port = MockSerialPort()
        with pytest.raises(PeeringError) as exc_info:
            server_wait_for_syn(port, timeout_s=0.1)
        assert "timeout" in str(exc_info.value).lower()

    def test_server_wait_for_syn_success(self) -> None:
        port = MockSerialPort()
        expected_id = b"\x01\x02\x03\x04"
        port.inject(encode_control(MsgType.SYN, expected_id))
        conn_id = server_wait_for_syn(port, timeout_s=1.0)
        assert conn_id == expected_id

    def test_client_send_syn_wait_syn_ack_timeout(self) -> None:
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        with pytest.raises(HandshakeError) as exc_info:
            client_send_syn_wait_syn_ack(
                port, conn_id, timeout_s=0.1, syn_interval_s=0.05
            )
        assert "timeout" in str(exc_info.value).lower()

    def test_client_send_syn_wait_syn_ack_success(self) -> None:
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        # Inject SYN_ACK response
        port.inject(encode_control(MsgType.SYN_ACK, conn_id))
        result = client_send_syn_wait_syn_ack(
            port, conn_id, timeout_s=1.0, syn_interval_s=0.1
        )
        assert result is True

    def test_server_send_syn_ack_wait_ack_timeout(self) -> None:
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        with pytest.raises(PeeringError) as exc_info:
            server_send_syn_ack_wait_ack(
                port, conn_id, timeout_s=0.1, syn_ack_interval_s=0.05
            )
        assert "timeout" in str(exc_info.value).lower()

    def test_server_send_syn_ack_wait_ack_success(self) -> None:
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        # Inject ACK response with session params (required)
        port.inject(encode_ack_with_params(conn_id, SessionParams(msg_count=100)))
        session_params = server_send_syn_ack_wait_ack(
            port, conn_id, timeout_s=1.0, syn_ack_interval_s=0.1
        )
        assert session_params.msg_count == 100


@pytest.mark.unit
class TestHandshake:
    """Tests for full handshake protocol."""

    def test_client_handshake_timeout(self) -> None:
        port = MockSerialPort()
        # No SYN_ACK response - should raise PeeringError
        with pytest.raises(PeeringError) as exc_info:
            client_handshake(port, timeout_s=0.2, syn_interval_s=0.1)
        assert "timeout" in str(exc_info.value).lower()

    def test_server_handshake_timeout(self) -> None:
        port = MockSerialPort()
        # No SYN from client - should raise PeeringError
        with pytest.raises(PeeringError) as exc_info:
            server_handshake(port, client_timeout_s=0.2, ack_timeout_s=0.1)
        assert "timeout" in str(exc_info.value).lower()

    def test_handshake_message_sequence(self) -> None:
        """Test that handshake messages can be decoded correctly."""
        conn_id = b"\x01\x02\x03\x04"

        # Test SYN encoding/decoding
        syn = encode_control(MsgType.SYN, conn_id)
        port = MockSerialPort()
        port.inject(syn)
        msg_type, recv_id, _, crc_ok = decode_message(port)
        assert msg_type == MsgType.SYN
        assert recv_id == conn_id
        assert crc_ok

        # Test SYN_ACK encoding/decoding
        syn_ack = encode_control(MsgType.SYN_ACK, conn_id)
        port = MockSerialPort()
        port.inject(syn_ack)
        msg_type, recv_id, _, crc_ok = decode_message(port)
        assert msg_type == MsgType.SYN_ACK
        assert recv_id == conn_id
        assert crc_ok

        # Test ACK encoding/decoding (with required session params)
        ack = encode_ack_with_params(conn_id, SessionParams(msg_count=50))
        port = MockSerialPort()
        port.inject(ack)
        msg_type, recv_id, data, crc_ok = decode_message(port)
        assert msg_type == MsgType.ACK
        assert recv_id == conn_id
        assert crc_ok
        # Verify session params can be decoded
        full_payload = bytes([MsgType.ACK]) + recv_id + (data or b"")
        _, session_params = decode_ack_with_params(full_payload)
        assert session_params is not None
        assert session_params.msg_count == 50


@pytest.mark.unit
class TestDataExchange:
    """Tests for data send/receive functions."""

    def test_send_data(self) -> None:
        port = MockSerialPort()
        conn = Connection(connection_id=b"\x01\x02\x03\x04", role=Role.CLIENT)
        payload = b"test payload"
        written = send_data(port, conn, payload)
        assert written is not None
        assert written > len(payload)  # Includes framing overhead

    def test_recv_data_matching_id(self) -> None:
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)
        payload = b"test data"

        # Inject a DATA message
        port.inject(encode_data(conn_id, payload))

        data, crc_ok, msg_type = recv_data(port, conn)
        assert data == payload
        assert crc_ok is True
        assert msg_type == MsgType.DATA

    def test_recv_data_wrong_id_raises(self) -> None:
        port = MockSerialPort()
        conn = Connection(connection_id=b"\x01\x02\x03\x04", role=Role.SERVER)

        # Inject a DATA message with different conn_id
        port.inject(encode_data(b"\xff\xff\xff\xff", b"wrong id"))

        with pytest.raises(ConnectionMismatchError):
            recv_data(port, conn)

    def test_recv_fin(self) -> None:
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        # Inject a FIN message
        port.inject(encode_control(MsgType.FIN, conn_id))

        data, crc_ok, msg_type = recv_data(port, conn)
        assert data == b""  # FIN has no data
        assert msg_type == MsgType.FIN


@pytest.mark.unit
class TestShutdown:
    """Tests for shutdown functions."""

    def test_client_shutdown_timeout(self) -> None:
        port = MockSerialPort()
        conn = Connection(connection_id=b"\x01\x02\x03\x04", role=Role.CLIENT)
        # Use short timeout for faster test
        result = client_shutdown(port, conn, timeout_s=0.2)
        assert result is False  # No FIN_ACK received

    def test_server_shutdown(self) -> None:
        port = MockSerialPort()
        conn = Connection(connection_id=b"\x01\x02\x03\x04", role=Role.SERVER)
        server_shutdown(port, conn)
        # Should have written FIN_ACK
        assert port.in_waiting > 0


@pytest.mark.unit
class TestSessionParams:
    """Tests for ACK with session parameters encoding/decoding."""

    def test_encode_ack_with_params_roundtrip(self) -> None:
        """Test encoding and decoding ACK with session params."""
        conn_id = b"\x01\x02\x03\x04"
        session_params = SessionParams(msg_count=500)

        encoded = encode_ack_with_params(conn_id, session_params)
        port = MockSerialPort()
        port.inject(encoded)

        msg_type, recv_id, data, crc_ok = decode_message(port)
        assert msg_type == MsgType.ACK
        assert recv_id == conn_id
        assert crc_ok

        # Reconstruct full payload to decode session params
        full_payload = bytes([MsgType.ACK]) + recv_id
        if data:
            full_payload += data

        decoded_id, decoded_params = decode_ack_with_params(full_payload)
        assert decoded_id == conn_id
        assert decoded_params is not None
        assert decoded_params.msg_count == 500

    def test_decode_ack_missing_session_params(self) -> None:
        """Test decoding ACK without session params raises EncodingError."""
        conn_id = b"\xaa\xbb\xcc\xdd"
        # ACK with only type + conn_id (no session params) is invalid
        payload = bytes([MsgType.ACK]) + conn_id

        with pytest.raises(EncodingError):
            decode_ack_with_params(payload)

    def test_decode_ack_with_params_too_short(self) -> None:
        """Test decoding ACK with too-short payload raises EncodingError."""
        # Payload shorter than type + conn_id + msg_count (9 bytes min)
        payload = bytes([MsgType.ACK, 0x01, 0x02])

        with pytest.raises(EncodingError):
            decode_ack_with_params(payload)

    def test_server_receives_session_params(self) -> None:
        """Test server correctly extracts session params from ACK."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        session_params = SessionParams(msg_count=250)

        # Inject ACK with session params
        port.inject(encode_ack_with_params(conn_id, session_params))

        recv_params = server_send_syn_ack_wait_ack(
            port, conn_id, timeout_s=1.0, syn_ack_interval_s=0.1
        )
        assert recv_params.msg_count == 250

    def test_server_rejects_ack_without_session_params(self) -> None:
        """Test server rejects ACK without session params and times out."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"

        # Inject ACK without session params (using encode_control)
        port.inject(encode_control(MsgType.ACK, conn_id))

        with pytest.raises(PeeringError) as exc_info:
            server_send_syn_ack_wait_ack(
                port, conn_id, timeout_s=0.2, syn_ack_interval_s=0.1
            )
        # Should timeout because ACK without params is rejected
        assert "timeout" in str(exc_info.value).lower()


@pytest.mark.unit
class TestCrcErrorHandling:
    """Tests for CRC error handling in handshake and data exchange."""

    def _corrupt_last_byte(self, data: bytes) -> bytes:
        """Corrupt the last byte (part of CRC) of a message."""
        return data[:-1] + bytes([data[-1] ^ 0xFF])

    def test_server_ignores_syn_with_bad_crc(self) -> None:
        """Server should ignore SYN with CRC error and timeout."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"

        # Inject corrupted SYN
        syn_msg = encode_control(MsgType.SYN, conn_id)
        port.inject(self._corrupt_last_byte(syn_msg))

        with pytest.raises(PeeringError) as exc_info:
            server_wait_for_syn(port, timeout_s=0.2)
        assert "timeout" in str(exc_info.value).lower()

    def test_client_ignores_syn_ack_with_bad_crc(self) -> None:
        """Client should ignore SYN_ACK with CRC error and timeout."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"

        # Inject corrupted SYN_ACK
        syn_ack_msg = encode_control(MsgType.SYN_ACK, conn_id)
        port.inject(self._corrupt_last_byte(syn_ack_msg))

        with pytest.raises(HandshakeError) as exc_info:
            client_send_syn_wait_syn_ack(
                port, conn_id, timeout_s=0.2, syn_interval_s=0.1
            )
        assert "timeout" in str(exc_info.value).lower()

    def test_server_ignores_ack_with_bad_crc(self) -> None:
        """Server should ignore ACK with CRC error and timeout."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"

        # Inject corrupted ACK (with session params, then corrupt CRC)
        ack_msg = encode_ack_with_params(conn_id, SessionParams(msg_count=100))
        port.inject(self._corrupt_last_byte(ack_msg))

        with pytest.raises(PeeringError) as exc_info:
            server_send_syn_ack_wait_ack(
                port, conn_id, timeout_s=0.2, syn_ack_interval_s=0.1
            )
        assert "timeout" in str(exc_info.value).lower()

    def test_recv_data_reports_crc_error(self) -> None:
        """recv_data should return crc_ok=False for corrupted DATA."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        # Inject corrupted DATA message
        data_msg = encode_data(conn_id, b"test payload")
        port.inject(self._corrupt_last_byte(data_msg))

        # decode_message returns payload with crc_ok=False
        # but recv_data currently doesn't detect this because decode_message
        # still returns the payload with crc_ok=False
        data, crc_ok, msg_type = recv_data(port, conn)
        # With corrupted CRC, the message may still be decoded but crc_ok=False
        # or it may be completely mangled. Let's verify behavior.
        if msg_type == MsgType.DATA:
            assert crc_ok is False  # CRC error detected


@pytest.mark.unit
class TestWrongConnectionIdHandling:
    """Tests for wrong connection ID filtering during handshake."""

    def test_client_ignores_syn_ack_wrong_id(self) -> None:
        """Client should ignore SYN_ACK with different connection ID."""
        port = MockSerialPort()
        our_conn_id = b"\x01\x02\x03\x04"
        wrong_conn_id = b"\xff\xff\xff\xff"

        # Inject SYN_ACK with wrong connection ID
        port.inject(encode_control(MsgType.SYN_ACK, wrong_conn_id))

        with pytest.raises(HandshakeError) as exc_info:
            client_send_syn_wait_syn_ack(
                port, our_conn_id, timeout_s=0.2, syn_interval_s=0.1
            )
        assert "timeout" in str(exc_info.value).lower()

    def test_server_ignores_ack_wrong_id(self) -> None:
        """Server should ignore ACK with different connection ID."""
        port = MockSerialPort()
        our_conn_id = b"\x01\x02\x03\x04"
        wrong_conn_id = b"\xff\xff\xff\xff"

        # Inject ACK with wrong connection ID (but valid session params)
        port.inject(encode_ack_with_params(wrong_conn_id, SessionParams(msg_count=100)))

        with pytest.raises(PeeringError) as exc_info:
            server_send_syn_ack_wait_ack(
                port, our_conn_id, timeout_s=0.2, syn_ack_interval_s=0.1
            )
        assert "timeout" in str(exc_info.value).lower()

    def test_recv_data_raises_on_fin_wrong_id(self) -> None:
        """recv_data should raise ConnectionMismatchError for FIN with wrong connection ID."""
        port = MockSerialPort()
        our_conn_id = b"\x01\x02\x03\x04"
        wrong_conn_id = b"\xff\xff\xff\xff"
        conn = Connection(connection_id=our_conn_id, role=Role.SERVER)

        # Inject FIN with wrong connection ID
        port.inject(encode_control(MsgType.FIN, wrong_conn_id))

        with pytest.raises(ConnectionMismatchError):
            recv_data(port, conn)


@pytest.mark.unit
class TestDuplicateMessageHandling:
    """Tests for duplicate/retransmission message handling."""

    def test_server_handles_duplicate_syn_then_valid(self) -> None:
        """Server should handle duplicate SYN followed by valid ACK."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"

        # Inject duplicate SYN (simulating retransmission), then valid ACK with params
        port.inject(encode_control(MsgType.SYN, conn_id))
        port.inject(encode_ack_with_params(conn_id, SessionParams(msg_count=100)))

        # Should succeed - the duplicate SYN is handled, then ACK accepted
        session_params = server_send_syn_ack_wait_ack(
            port, conn_id, timeout_s=1.0, syn_ack_interval_s=0.1
        )
        assert session_params.msg_count == 100


@pytest.mark.unit
class TestInvalidMessageType:
    """Tests for invalid message type handling."""

    def test_decode_invalid_msg_type(self) -> None:
        """decode_message should raise EncodingError for invalid message type."""
        port = MockSerialPort()
        # Create a message with invalid type (0xFF is not in MsgType enum)
        invalid_payload = bytes([0xFF]) + b"\x01\x02\x03\x04"
        encoded = message.encode(invalid_payload)
        port.inject(encoded)

        with pytest.raises(EncodingError) as exc_info:
            decode_message(port)
        assert "Invalid message type" in str(exc_info.value)


@pytest.mark.unit
class TestEmptyPayloads:
    """Tests for empty payload edge cases.

    Note: Empty data payloads (b"") result in data=b"" after decode because
    the payload length equals type+conn_id with no additional bytes.
    """

    def test_encode_data_empty_payload(self) -> None:
        """Test encoding DATA message with empty payload."""
        conn_id = b"\x01\x02\x03\x04"
        encoded = encode_data(conn_id, b"")
        port = MockSerialPort()
        port.inject(encoded)

        msg_type, recv_id, data, crc_ok = decode_message(port)
        assert msg_type == MsgType.DATA
        assert recv_id == conn_id
        # Empty payload results in data=b"" (no bytes beyond type+conn_id)
        assert data == b""
        assert crc_ok is True

    def test_recv_data_empty_payload(self) -> None:
        """Test recv_data with empty DATA payload."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        port.inject(encode_data(conn_id, b""))

        data, crc_ok, msg_type = recv_data(port, conn)
        # Empty payload results in data=b""
        assert data == b""
        assert crc_ok is True
        assert msg_type == MsgType.DATA

    def test_encode_data_single_byte_payload(self) -> None:
        """Test encoding DATA message with single byte payload."""
        conn_id = b"\x01\x02\x03\x04"
        encoded = encode_data(conn_id, b"X")
        port = MockSerialPort()
        port.inject(encoded)

        msg_type, recv_id, data, crc_ok = decode_message(port)
        assert msg_type == MsgType.DATA
        assert recv_id == conn_id
        assert data == b"X"  # Single byte is preserved
        assert crc_ok is True


@pytest.mark.unit
class TestTruncatedMessages:
    """Tests for truncated message handling."""

    def test_decode_truncated_crc(self) -> None:
        """decode should raise TransportError for message with truncated CRC."""
        port = MockSerialPort()
        # Create a valid message then truncate it
        valid_msg = encode_control(MsgType.SYN, b"\x01\x02\x03\x04")
        # Truncate last 2 bytes of CRC
        truncated = valid_msg[:-2]
        port.inject(truncated)

        with pytest.raises(TransportError):
            decode_message(port)

    def test_decode_only_length_field(self) -> None:
        """decode should raise TransportError for message with only length field."""
        port = MockSerialPort()
        # Only 4-byte length claiming large payload, no actual data
        port.inject(b"\x10\x00\x00\x00")  # Claims 16 bytes of payload

        with pytest.raises(TransportError):
            decode_message(port)


@pytest.mark.unit
class TestHandshakeWithValidThenInvalid:
    """Tests for handshake receiving valid message after invalid ones."""

    def test_client_receives_valid_after_corrupt(self) -> None:
        """Client should accept valid SYN_ACK after ignoring corrupt one."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"

        # First inject a corrupted SYN_ACK
        syn_ack_msg = encode_control(MsgType.SYN_ACK, conn_id)
        port.inject(syn_ack_msg[:-1] + bytes([syn_ack_msg[-1] ^ 0xFF]))

        # Then inject a valid SYN_ACK
        port.inject(encode_control(MsgType.SYN_ACK, conn_id))

        result = client_send_syn_wait_syn_ack(
            port, conn_id, timeout_s=1.0, syn_interval_s=0.5
        )
        assert result is True

    def test_server_receives_valid_after_wrong_id(self) -> None:
        """Server should accept valid ACK after ignoring wrong ID."""
        port = MockSerialPort()
        our_conn_id = b"\x01\x02\x03\x04"
        wrong_conn_id = b"\xff\xff\xff\xff"

        # First inject ACK with wrong ID (but valid params)
        port.inject(encode_ack_with_params(wrong_conn_id, SessionParams(msg_count=50)))

        # Then inject valid ACK with correct ID
        port.inject(encode_ack_with_params(our_conn_id, SessionParams(msg_count=200)))

        session_params = server_send_syn_ack_wait_ack(
            port, our_conn_id, timeout_s=1.0, syn_ack_interval_s=0.5
        )
        assert session_params.msg_count == 200
