"""Inactive, pure monthly debit accounting for the station-first PR series.

No wallet calls, signature verification, persistence, HTTP handlers or payment
authority are implemented here. Runtime adapters must verify monthly consent,
wallet period semantics, identity, origin, station scope and session ownership
before using this model. See docs/station-first-implementation.md.
"""
from dataclasses import dataclass, replace
from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_MONTHLY_LIMIT_SATS = 30_000
SCHEMA = "monthly-allowance-v1"
STATES = frozenset({"reserved", "wallet_pending", "uncertain", "committed", "released"})
UNRESOLVED = frozenset({"reserved", "wallet_pending", "uncertain"})


class AllowanceError(ValueError):
    """Invalid or unsafe accounting transition."""


def _sats(value: int, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0):
        raise AllowanceError("Satoshi amounts must be non-negative integers")
    return value


def _name(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AllowanceError("Identifiers must be non-empty strings")
    return value


@dataclass(frozen=True)
class Month:
    """Calendar period in an explicitly supplied, externally verified timezone."""

    year: int
    month: int
    timezone: str

    def __post_init__(self):
        if type(self.year) is not int or type(self.month) is not int:
            raise AllowanceError("Invalid calendar period")
        try:
            datetime(self.year, self.month, 1, tzinfo=ZoneInfo(self.timezone))
        except (ValueError, TypeError, KeyError) as exc:
            raise AllowanceError("Invalid calendar period or timezone") from exc

    @classmethod
    def at(cls, instant: datetime, *, wallet_timezone: str) -> "Month":
        """No default timezone: the caller must establish the wallet's boundary."""
        if not isinstance(instant, datetime) or instant.utcoffset() is None:
            raise AllowanceError("An aware wallet-booking timestamp is required")
        try:
            local = instant.astimezone(ZoneInfo(wallet_timezone))
        except (ValueError, TypeError, KeyError) as exc:
            raise AllowanceError("Invalid wallet timezone") from exc
        return cls(local.year, local.month, wallet_timezone)

    @property
    def key(self) -> tuple[int, int]:
        return self.year, self.month


@dataclass(frozen=True)
class Attempt:
    """One session's debit reservation, not proof of a transaction or payment."""

    attempt_id: str
    account_id: str
    month: Month
    reserved_sats: int
    state: str = "reserved"
    spent_sats: int = 0
    wallet_action_id: str | None = None
    evidence_ref: str | None = None

    def __post_init__(self):
        _name(self.attempt_id)
        _name(self.account_id)
        if not isinstance(self.month, Month):
            raise AllowanceError("Invalid attempt month")
        _sats(self.reserved_sats, positive=True)
        _sats(self.spent_sats)
        if self.state not in STATES:
            raise AllowanceError("Invalid attempt state")
        if self.state != "committed" and self.spent_sats:
            raise AllowanceError("Only committed spending is booked")
        if self.state in {"wallet_pending", "uncertain", "committed"}:
            _name(self.wallet_action_id)
        if self.state == "committed":
            _sats(self.spent_sats, positive=True)
            _name(self.evidence_ref)
        if self.state == "released":
            _name(self.evidence_ref)


@dataclass(frozen=True)
class Summary:
    limit_sats: int
    spent_sats: int
    reserved_sats: int
    remaining_sats: int
    over_limit_sats: int
    blocked: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Allowance:
    """Immutable candidate state: persist atomically before any wallet action.

    A separate integration adapter must serialise concurrent updates. Creating
    this object is NOT evidence of a driver's approval or a native wallet grant.
    """

    authority_id: str
    wallet_timezone: str
    limit_sats: int = DEFAULT_MONTHLY_LIMIT_SATS
    cancelled: bool = False
    attempts: tuple[Attempt, ...] = ()

    def __post_init__(self):
        _name(self.authority_id)
        _sats(self.limit_sats, positive=True)
        Month(2000, 1, self.wallet_timezone)
        if type(self.cancelled) is not bool or type(self.attempts) is not tuple:
            raise AllowanceError("Invalid ledger structure")
        if any(not isinstance(a, Attempt) for a in self.attempts):
            raise AllowanceError("Invalid ledger attempt")
        ids = [a.attempt_id for a in self.attempts]
        accounts = [a.account_id for a in self.attempts if a.state != "released"]
        if len(ids) != len(set(ids)) or len(accounts) != len(set(accounts)):
            raise AllowanceError("Duplicate attempt or active account")
        if any(a.month.timezone != self.wallet_timezone for a in self.attempts):
            raise AllowanceError("Cannot mix wallet period timezones")

    def _period(self, month: Month) -> None:
        if not isinstance(month, Month) or month.timezone != self.wallet_timezone:
            raise AllowanceError("Wallet period timezone mismatch")

    def _attempt(self, attempt_id: str) -> Attempt:
        for attempt in self.attempts:
            if attempt.attempt_id == attempt_id:
                return attempt
        raise AllowanceError("Unknown attempt")

    def _replace(self, attempt: Attempt) -> "Allowance":
        return replace(self, attempts=tuple(
            attempt if row.attempt_id == attempt.attempt_id else row
            for row in self.attempts
        ))

    def summary(self, month: Month) -> Summary:
        self._period(month)
        rows = [a for a in self.attempts if a.month == month]
        spent = sum(a.spent_sats for a in rows if a.state == "committed")
        reserved = sum(a.reserved_sats for a in rows if a.state in UNRESOLVED)
        reasons = []
        if self.cancelled:
            reasons.append("authority_cancelled")
        if any(a.state in UNRESOLVED and a.month != month for a in self.attempts):
            reasons.append("cross_period_attempt_unresolved")
        if any(a.month.key > month.key for a in self.attempts):
            reasons.append("period_regression")
        if any(a.state == "uncertain" for a in self.attempts):
            reasons.append("uncertain_attempt")
        if spent + reserved > self.limit_sats:
            reasons.append("allowance_exceeded")
        if any(a.state == "committed" and a.spent_sats > a.reserved_sats
               for a in self.attempts):
            reasons.append("fee_or_spend_overrun_requires_review")
        return Summary(
            self.limit_sats, spent, reserved,
            max(0, self.limit_sats - spent - reserved),
            max(0, spent + reserved - self.limit_sats),
            bool(reasons), tuple(reasons),
        )

    def reserve(self, attempt_id: str, account_id: str, month: Month,
                *, debit_sats: int, fee_reserve_sats: int) -> "Allowance":
        total = _sats(debit_sats, positive=True) + _sats(fee_reserve_sats)
        proposed = Attempt(attempt_id, account_id, month, total)
        existing = next((a for a in self.attempts if a.attempt_id == attempt_id), None)
        if existing:
            if (existing.account_id, existing.month, existing.reserved_sats) != (
                account_id, month, total
            ):
                raise AllowanceError("Idempotency key reused with different terms")
            # Repeated reads/reservations cannot reopen a released or paid attempt.
            return self
        status = self.summary(month)
        if status.blocked:
            raise AllowanceError(", ".join(status.reasons))
        if total > status.remaining_sats:
            raise AllowanceError("Monthly allowance insufficient including fees")
        return replace(self, attempts=self.attempts + (proposed,))

    def wallet_pending(self, attempt_id: str, *, wallet_action_id: str,
                       current_month: Month) -> "Allowance":
        """Persist this state BEFORE invoking the external signing operation."""
        row = self._attempt(attempt_id)
        _name(wallet_action_id)
        if row.state == "wallet_pending" and row.wallet_action_id == wallet_action_id:
            return self
        if self.cancelled or row.state != "reserved":
            raise AllowanceError("Attempt cannot enter wallet signing")
        status = self.summary(current_month)
        if row.month != current_month or status.blocked:
            raise AllowanceError("Reconcile period or allowance before wallet signing")
        return self._replace(replace(row, state="wallet_pending", wallet_action_id=wallet_action_id))

    def uncertain(self, attempt_id: str) -> "Allowance":
        row = self._attempt(attempt_id)
        if row.state == "uncertain":
            return self
        if row.state != "wallet_pending":
            raise AllowanceError("Only an invoked wallet action can become uncertain")
        return self._replace(replace(row, state="uncertain"))

    def commit(self, attempt_id: str, *, actual_spent_sats: int,
               evidence_ref: str, booking_month: Month) -> "Allowance":
        """Book independently verified wallet spending, NOT chain confirmation.

        The adapter must confirm the wallet booking period matches the reservation
        before calling. Unexpected overrun is retained as actual spend and blocks
        new spending, rather than losing evidence by rejecting a completed debit.
        """
        _sats(actual_spent_sats, positive=True)
        _name(evidence_ref)
        row = self._attempt(attempt_id)
        self._period(booking_month)
        if row.month != booking_month:
            raise AllowanceError("Wallet booking period mismatch requires reconciliation")
        if row.state == "committed":
            if (row.spent_sats, row.evidence_ref) == (actual_spent_sats, evidence_ref):
                return self
            raise AllowanceError("Committed evidence cannot be replaced")
        if row.state not in {"wallet_pending", "uncertain"}:
            raise AllowanceError("No existing wallet action to reconcile")
        return self._replace(replace(row, state="committed",
                                     spent_sats=actual_spent_sats, evidence_ref=evidence_ref))

    def release_uninvoked(self, attempt_id: str, *, reason: str) -> "Allowance":
        """Only release a reservation which never reached the wallet."""
        _name(reason)
        row = self._attempt(attempt_id)
        if row.state == "released":
            return self
        if row.state != "reserved":
            raise AllowanceError("Invoked wallet actions require independent reconciliation")
        return self._replace(replace(row, state="released", evidence_ref=reason))

    def cancel(self) -> "Allowance":
        """Stop new attempts; retain every reservation and reconciliation record."""
        return replace(self, cancelled=True)
