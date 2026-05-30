"""
Custom exception hierarchy for WPAY ADS BOT V2.

All domain exceptions inherit from WPayBaseError so callers can
catch the entire family with a single except clause.
"""

from __future__ import annotations


class WPayBaseError(Exception):
    """Base exception for all WPAY domain errors."""

    def __init__(self, message: str = "", *, detail: str | None = None) -> None:
        self.detail = detail
        super().__init__(message)


# ── Account Errors ──────────────────────────────────────────

class AccountNotFoundError(WPayBaseError):
    """Raised when an account lookup returns no result."""


class AccountBannedError(WPayBaseError):
    """Raised when attempting to use a banned account."""


class AccountQuarantinedError(WPayBaseError):
    """Raised when attempting to use a quarantined account."""


# ── Campaign Errors ─────────────────────────────────────────

class CampaignNotFoundError(WPayBaseError):
    """Raised when a campaign lookup returns no result."""


class CampaignInactiveError(WPayBaseError):
    """Raised when attempting an operation on a non-active campaign."""


# ── Health Errors ───────────────────────────────────────────

class HealthCheckFailedError(WPayBaseError):
    """Raised when a SpamBot health check fails."""


# ── Infrastructure Errors ───────────────────────────────────

class CacheUnavailableError(WPayBaseError):
    """Raised when Redis is unreachable or returns an error."""


class CircuitOpenError(WPayBaseError):
    """Raised when the circuit breaker for an account is OPEN."""


# ── Session Errors ──────────────────────────────────────────

class SessionInvalidError(WPayBaseError):
    """Raised when a Telethon session string fails validation."""


class SessionDecryptionError(WPayBaseError):
    """Raised when session decryption fails (bad key or corrupted data)."""


# ── Plan Errors ─────────────────────────────────────────────

class PlanLimitError(WPayBaseError):
    """Raised when a user has exceeded their plan quota."""


class PlanNotFoundError(WPayBaseError):
    """Raised when a user has no plan record."""


# ── Force Join Errors ───────────────────────────────────────

class ForceJoinRequiredError(WPayBaseError):
    """Raised when a user has not joined the required channels."""


# ── Group Errors ────────────────────────────────────────────

class GroupNotFoundError(WPayBaseError):
    """Raised when a group lookup returns no result."""
