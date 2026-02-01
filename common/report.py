"""Reporting abstractions for serial-testkit.

Contains:
- Report ABC: Base class for all reports
- PeeringReport: Report after peering completes
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from common.connection import Role


class Report(ABC):
    """Abstract base class for test reports."""

    @abstractmethod
    def print(self) -> None:
        """Print the report to stdout."""
        pass

    @abstractmethod
    def success(self) -> bool:
        """Return True if the report indicates success."""
        pass


@dataclass
class PeeringReport(Report):
    """Report after peering handshake completes.

    When connected=True, connection_id and role are required.
    When connected=False, error should be set.
    msg_count is optional (only set by server).
    """

    connected: bool
    connection_id: bytes | None = None
    role: Role | None = None
    error: Exception | None = None
    msg_count: int | None = None  # Session params received (server only)

    def __post_init__(self) -> None:
        """Validate invariants."""
        if self.connected:
            if self.connection_id is None:
                raise ValueError("connection_id is required when connected=True")
            if self.role is None:
                raise ValueError("role is required when connected=True")

    def print(self) -> None:
        """Print the peering report."""
        if self.connected:
            # connection_id and role are guaranteed non-None by __post_init__
            assert self.connection_id is not None
            assert self.role is not None
            print(f"Peering: SUCCESS (id={self.connection_id.hex()}, role={self.role.value})")
            if self.msg_count is not None:
                print(f"Session params: msg_count={self.msg_count}")
        else:
            print(f"Peering: FAILED ({self.error})")

    def success(self) -> bool:
        """Return True if peering succeeded."""
        return self.connected
