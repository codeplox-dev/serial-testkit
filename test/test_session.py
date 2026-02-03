"""Unit tests for session data exchange types and reporting."""

import io
from contextlib import redirect_stdout

import pytest

from common.connection import Connection, Role
from common.encoding import encode_control, encode_data
from common.protocol import MsgType
from session.exchange import client_exchange, server_exchange, wait_for_fin
from session.report import SessionReport
from session.result import SessionError, SessionResult
from test.conftest import ConnectedMockPorts, MockSerialPort


@pytest.mark.unit
class TestSessionResult:
    """Tests for SessionResult dataclass."""

    def test_default_values(self) -> None:
        """Test SessionResult has correct defaults."""
        result = SessionResult(success=True)
        assert result.success is True
        assert result.sent == 0
        assert result.received == 0
        assert result.crc_ok == 0
        assert result.crc_errors == 0
        assert result.bytes_sent == 0
        assert result.bytes_received == 0
        assert result.rtt_samples == []
        assert result.elapsed_s == 0.0
        assert result.error is None
        assert result.fin_ack_received is False
        assert result.fin_received is False

    def test_crc_pass_rate_100_percent(self) -> None:
        """Test CRC pass rate calculation with 100% success."""
        result = SessionResult(success=True, received=100, crc_ok=100)
        assert result.crc_pass_rate == 100.0

    def test_crc_pass_rate_partial(self) -> None:
        """Test CRC pass rate calculation with partial success."""
        result = SessionResult(success=True, received=100, crc_ok=95)
        assert result.crc_pass_rate == 95.0

    def test_crc_pass_rate_zero_received(self) -> None:
        """Test CRC pass rate with no messages received."""
        result = SessionResult(success=False, received=0)
        assert result.crc_pass_rate == 0.0

    def test_latency_stats_from_rtt(self) -> None:
        """Test latency_stats property computes from RTT samples."""
        # 1ms, 2ms, 3ms in seconds
        result = SessionResult(
            success=True,
            rtt_samples=[0.001, 0.002, 0.003],
        )
        stats = result.latency_stats
        assert stats is not None
        assert stats.count == 3
        assert stats.min_ms == pytest.approx(1.0)
        assert stats.max_ms == pytest.approx(3.0)
        assert stats.avg_ms == pytest.approx(2.0)

    def test_latency_stats_empty(self) -> None:
        """Test latency_stats property with no RTT samples."""
        result = SessionResult(success=True)
        assert result.latency_stats is None

    def test_throughput_baud(self) -> None:
        """Test throughput_baud calculation."""
        result = SessionResult(
            success=True,
            bytes_sent=1000,
            bytes_received=1000,
            elapsed_s=1.0,
        )
        # 2000 bytes/sec * 10 bits/byte = 20000 baud
        assert result.throughput_baud() == 20000.0

    def test_throughput_baud_zero_duration(self) -> None:
        """Test throughput_baud with zero duration."""
        result = SessionResult(
            success=True,
            bytes_sent=1000,
            elapsed_s=0.0,
        )
        assert result.throughput_baud() == 0.0

    def test_throughput_kbps(self) -> None:
        """Test throughput_kbps calculation."""
        result = SessionResult(
            success=True,
            bytes_sent=1000,
            bytes_received=1000,
            elapsed_s=1.0,
        )
        # 2000 bytes/sec * 8 bits/byte / 1000 = 16 Kbps
        assert result.throughput_kbps() == 16.0

    def test_throughput_kbps_zero_duration(self) -> None:
        """Test throughput_kbps with zero duration."""
        result = SessionResult(
            success=True,
            bytes_sent=1000,
            elapsed_s=0.0,
        )
        assert result.throughput_kbps() == 0.0


@pytest.mark.unit
class TestSessionReport:
    """Tests for SessionReport formatting."""

    def test_success_report_basic(self) -> None:
        """Test successful session report output."""
        result = SessionResult(
            success=True,
            sent=100,
            received=100,
            crc_ok=100,
            crc_errors=0,
        )
        report = SessionReport(result=result)

        output = io.StringIO()
        with redirect_stdout(output):
            report.print()

        text = output.getvalue()
        assert "Session: SUCCESS" in text
        assert "100 sent" in text
        assert "100 received" in text
        assert "100 ok" in text
        assert "0 errors" in text

    def test_success_report_with_throughput(self) -> None:
        """Test successful session report with throughput stats."""
        result = SessionResult(
            success=True,
            sent=100,
            received=100,
            crc_ok=100,
            crc_errors=0,
            bytes_sent=10000,
            bytes_received=10000,
            elapsed_s=1.0,
        )
        report = SessionReport(result=result)

        output = io.StringIO()
        with redirect_stdout(output):
            report.print()

        text = output.getvalue()
        assert "Throughput:" in text
        assert "baud" in text
        assert "Kbps" in text
        # Short test warning
        assert "short test" in text.lower()

    def test_success_report_with_latency(self) -> None:
        """Test successful session report with latency stats."""
        result = SessionResult(
            success=True,
            sent=10,
            received=10,
            crc_ok=10,
            rtt_samples=[0.002, 0.003, 0.004],  # 2ms, 3ms, 4ms
            elapsed_s=1.0,
        )
        report = SessionReport(result=result)

        output = io.StringIO()
        with redirect_stdout(output):
            report.print()

        text = output.getvalue()
        assert "Latency:" in text
        assert "avg=" in text
        assert "min=" in text
        assert "max=" in text
        assert "p50=" in text
        assert "p95=" in text
        assert "p99=" in text

    def test_failed_report_basic(self) -> None:
        """Test failed session report output."""
        result = SessionResult(
            success=False,
            error=SessionError("timeout waiting for server response"),
        )
        report = SessionReport(result=result)

        output = io.StringIO()
        with redirect_stdout(output):
            report.print()

        text = output.getvalue()
        assert "Session: FAILED" in text
        assert "timeout waiting for server response" in text

    def test_failed_report_with_partial_stats(self) -> None:
        """Test failed session report with partial exchange stats."""
        result = SessionResult(
            success=False,
            error=SessionError("timeout"),
            sent=50,
            received=49,
            crc_ok=49,
            crc_errors=0,
        )
        report = SessionReport(result=result)

        output = io.StringIO()
        with redirect_stdout(output):
            report.print()

        text = output.getvalue()
        assert "Session: FAILED" in text
        assert "50 sent" in text
        assert "49 received" in text

    def test_success_method_100_percent_crc(self) -> None:
        """Test success() returns True for 100% CRC pass rate."""
        result = SessionResult(
            success=True,
            received=100,
            crc_ok=100,
        )
        report = SessionReport(result=result)
        assert report.success() is True

    def test_success_method_partial_crc(self) -> None:
        """Test success() returns False for partial CRC pass rate."""
        result = SessionResult(
            success=True,
            received=100,
            crc_ok=95,
            crc_errors=5,
        )
        report = SessionReport(result=result)
        assert report.success() is False

    def test_success_method_failed_session(self) -> None:
        """Test success() returns False for failed session."""
        result = SessionResult(
            success=False,
            error=SessionError("timeout"),
        )
        report = SessionReport(result=result)
        assert report.success() is False

    def test_no_throughput_for_failed_session(self) -> None:
        """Test that throughput is not printed for failed sessions."""
        result = SessionResult(
            success=False,
            error=SessionError("timeout"),
            bytes_sent=1000,
            bytes_received=1000,
            elapsed_s=1.0,
        )
        report = SessionReport(result=result)

        output = io.StringIO()
        with redirect_stdout(output):
            report.print()

        text = output.getvalue()
        assert "Throughput:" not in text

    def test_no_latency_for_empty_rtt(self) -> None:
        """Test that latency is not printed when no RTT samples."""
        result = SessionResult(
            success=True,
            sent=10,
            received=10,
            crc_ok=10,
            rtt_samples=[],
        )
        report = SessionReport(result=result)

        output = io.StringIO()
        with redirect_stdout(output):
            report.print()

        text = output.getvalue()
        assert "Latency:" not in text

    def test_no_short_test_warning_for_long_duration(self) -> None:
        """Test that short test warning is not shown for long durations."""
        result = SessionResult(
            success=True,
            sent=100,
            received=100,
            crc_ok=100,
            bytes_sent=10000,
            bytes_received=10000,
            elapsed_s=60.0,  # 60 seconds
        )
        report = SessionReport(result=result)

        output = io.StringIO()
        with redirect_stdout(output):
            report.print()

        text = output.getvalue()
        assert "short test" not in text.lower()


@pytest.mark.unit
class TestWaitForFin:
    """Tests for wait_for_fin() helper function."""

    def test_wait_for_fin_immediate(self) -> None:
        """Test wait_for_fin returns True when FIN is immediately available."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        # Inject FIN message
        port.inject(encode_control(MsgType.FIN, conn_id))

        result = wait_for_fin(port, conn, timeout_s=1.0)
        assert result is True

    def test_wait_for_fin_timeout(self) -> None:
        """Test wait_for_fin returns False on timeout."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        # No FIN injected
        result = wait_for_fin(port, conn, timeout_s=0.1)
        assert result is False

    def test_wait_for_fin_wrong_conn_id(self) -> None:
        """Test wait_for_fin ignores FIN with wrong connection ID."""
        port = MockSerialPort()
        our_conn_id = b"\x01\x02\x03\x04"
        wrong_conn_id = b"\xff\xff\xff\xff"
        conn = Connection(connection_id=our_conn_id, role=Role.SERVER)

        # Inject FIN with wrong connection ID
        port.inject(encode_control(MsgType.FIN, wrong_conn_id))

        result = wait_for_fin(port, conn, timeout_s=0.1)
        assert result is False

    def test_wait_for_fin_ignores_data(self) -> None:
        """Test wait_for_fin ignores DATA messages while waiting."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        # Inject DATA then FIN
        port.inject(encode_data(conn_id, b"some data"))
        port.inject(encode_control(MsgType.FIN, conn_id))

        result = wait_for_fin(port, conn, timeout_s=1.0)
        assert result is True


@pytest.mark.unit
class TestClientExchange:
    """Tests for client_exchange() function."""

    def test_client_exchange_zero_msg_count(self) -> None:
        """Test client_exchange with msg_count=0 skips to shutdown."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.CLIENT)

        # Inject FIN_ACK for shutdown
        port.inject(encode_control(MsgType.FIN_ACK, conn_id))

        result = client_exchange(port, conn, msg_count=0)

        assert result.success is True
        assert result.sent == 0
        assert result.received == 0
        assert result.fin_ack_received is True

    def test_client_exchange_timeout_no_response(self) -> None:
        """Test client_exchange fails on timeout waiting for response.

        Uses ConnectedMockPorts which has separate read/write buffers,
        so the client can't read back its own sent data.
        """
        ports = ConnectedMockPorts()
        client_port = ports.port_a  # Client writes to B, reads from B's writes
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.CLIENT)

        # No response injected - server never responds
        result = client_exchange(client_port, conn, msg_count=1)

        assert result.success is False
        assert result.error is not None
        assert "Timeout" in str(result.error)
        assert result.sent == 1  # Client sent the message
        assert result.received == 0  # No response received

    def test_client_exchange_server_early_fin(self) -> None:
        """Test client_exchange handles server sending FIN instead of DATA."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.CLIENT)

        # Inject FIN instead of DATA response
        port.inject(encode_control(MsgType.FIN, conn_id))

        result = client_exchange(port, conn, msg_count=1)

        assert result.success is False
        assert "FIN" in str(result.error)


@pytest.mark.unit
class TestServerExchange:
    """Tests for server_exchange() function."""

    def test_server_exchange_zero_msg_count(self) -> None:
        """Test server_exchange with msg_count=0 waits for FIN."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        # Inject FIN from client
        port.inject(encode_control(MsgType.FIN, conn_id))

        result = server_exchange(port, conn, msg_count=0)

        assert result.success is True
        assert result.sent == 0
        assert result.received == 0
        assert result.fin_received is True

    def test_server_exchange_timeout_no_data(self) -> None:
        """Test server_exchange fails on timeout waiting for DATA."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        # No DATA injected
        result = server_exchange(port, conn, msg_count=1)

        assert result.success is False
        assert "Timeout" in str(result.error)
        assert result.received == 0

    def test_server_exchange_client_early_fin(self) -> None:
        """Test server_exchange handles client sending FIN instead of DATA."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        # Inject FIN instead of DATA
        port.inject(encode_control(MsgType.FIN, conn_id))

        result = server_exchange(port, conn, msg_count=1)

        assert result.success is False
        assert "FIN" in str(result.error)
        assert result.fin_received is True

    def test_server_exchange_crc_error_accumulation(self) -> None:
        """Test server_exchange counts CRC errors but continues exchange."""
        port = MockSerialPort()
        conn_id = b"\x01\x02\x03\x04"
        conn = Connection(connection_id=conn_id, role=Role.SERVER)

        # Create corrupted DATA message (corrupt last byte of CRC)
        good_data = encode_data(conn_id, b"test payload")
        corrupted_data = good_data[:-1] + bytes([good_data[-1] ^ 0xFF])
        port.inject(corrupted_data)

        # Inject FIN for shutdown
        port.inject(encode_control(MsgType.FIN, conn_id))

        result = server_exchange(port, conn, msg_count=1)

        assert result.success is True
        assert result.received == 1  # Message was received
        assert result.crc_ok == 0  # But CRC failed
        assert result.crc_errors == 1  # Error counted
        assert result.sent == 1  # Server still echoed
        assert result.fin_received is True
