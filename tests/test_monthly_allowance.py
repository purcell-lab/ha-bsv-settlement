"""Offline foundation fixtures. No wallet, keys, HTTP or chain calls."""
from datetime import datetime, timezone

import pytest

from custom_components.bsv_settlement.monthly_allowance import (
    Allowance, AllowanceError, Attempt, DEFAULT_MONTHLY_LIMIT_SATS, Month,
)

OCT = Month(2026, 10, "UTC")
NOV = Month(2026, 11, "UTC")


def ledger():
    return Allowance("fictional-monthly-authority", "UTC")


def reserved(amount=29_900, fee=100):
    return ledger().reserve("attempt-1", "account-1", OCT,
                            debit_sats=amount, fee_reserve_sats=fee)


def pending(amount=29_900, fee=100):
    return reserved(amount, fee).wallet_pending("attempt-1", wallet_action_id="local-operation-1", current_month=OCT)


def test_selected_limit_and_immutable_reservation():
    original = ledger()
    row = original.reserve("one", "account", OCT, debit_sats=29_900, fee_reserve_sats=100)
    assert DEFAULT_MONTHLY_LIMIT_SATS == 30_000
    assert original.attempts == ()
    assert row.summary(OCT).remaining_sats == 0
    assert row.summary(OCT).spent_sats == 0
    assert row.summary(OCT).reserved_sats == 30_000


@pytest.mark.parametrize("bad", [True, False, -1, 0, 0.5, "30000", None])
def test_invalid_limits(bad):
    with pytest.raises(AllowanceError):
        Allowance("authority", "UTC", limit_sats=bad)


@pytest.mark.parametrize("debit,fee", [(True, 0), (0, 0), (-1, 0), (1, -1), (1, True), (1, "1")])
def test_invalid_amounts(debit, fee):
    with pytest.raises(AllowanceError):
        ledger().reserve("a", "s", OCT, debit_sats=debit, fee_reserve_sats=fee)


def test_total_fee_inclusive_cap():
    with pytest.raises(AllowanceError, match="including fees"):
        reserved(30_000, 1)
    row = reserved(20_000, 1_000)
    with pytest.raises(AllowanceError):
        row.reserve("two", "account-2", OCT, debit_sats=9_000, fee_reserve_sats=1)
    assert row.reserve("two", "account-2", OCT, debit_sats=8_999,
                       fee_reserve_sats=1).summary(OCT).remaining_sats == 0


def test_actual_spend_replaces_reserve_without_double_counting():
    row = pending().commit("attempt-1", actual_spent_sats=29_950, evidence_ref="verified-booking", booking_month=OCT)
    summary = row.summary(OCT)
    assert (summary.spent_sats, summary.reserved_sats, summary.remaining_sats) == (29_950, 0, 50)
    assert not summary.blocked


def test_credit_is_not_an_allowance_operation():
    assert not hasattr(ledger(), "credit")
    row = pending().commit("attempt-1", actual_spent_sats=30_000, evidence_ref="verified-booking", booking_month=OCT)
    assert row.summary(OCT).remaining_sats == 0


def test_idempotence_conflicting_terms_and_no_reopening():
    row = reserved()
    assert row.reserve("attempt-1", "account-1", OCT,
                       debit_sats=29_900, fee_reserve_sats=100) is row
    with pytest.raises(AllowanceError, match="different terms"):
        row.reserve("attempt-1", "account-1", OCT, debit_sats=100, fee_reserve_sats=0)
    released = row.release_uninvoked("attempt-1", reason="not sent")
    assert released.reserve("attempt-1", "account-1", OCT,
                            debit_sats=29_900, fee_reserve_sats=100) is released


def test_duplicate_account_rejected_even_in_new_month():
    row = pending(50, 10).commit("attempt-1", actual_spent_sats=60, evidence_ref="verified", booking_month=OCT)
    with pytest.raises(AllowanceError, match="active account"):
        row.reserve("replacement", "account-1", NOV, debit_sats=50, fee_reserve_sats=10)


def test_uninvoked_release_allows_new_key_but_not_old_key_replay():
    row = reserved().release_uninvoked("attempt-1", reason="not sent")
    new = row.reserve("new-attempt", "account-1", OCT, debit_sats=100, fee_reserve_sats=10)
    assert len(new.attempts) == 2
    assert new.attempts[0].state == "released"


@pytest.mark.parametrize("state", ["reserved", "wallet_pending", "uncertain"])
def test_month_rollover_does_not_release_unresolved_attempt(state):
    row = reserved(50, 10)
    if state != "reserved":
        row = row.wallet_pending("attempt-1", wallet_action_id="operation", current_month=OCT)
    if state == "uncertain":
        row = row.uncertain("attempt-1")
    assert row.summary(NOV).blocked
    assert "cross_period_attempt_unresolved" in row.summary(NOV).reasons
    with pytest.raises(AllowanceError):
        row.reserve("new", "new-account", NOV, debit_sats=1, fee_reserve_sats=0)
    assert row.summary(OCT).reserved_sats == 60


def test_uncertainty_blocks_new_attempts_in_same_month():
    row = pending(50, 10).uncertain("attempt-1")
    with pytest.raises(AllowanceError, match="uncertain_attempt"):
        row.reserve("new", "new-account", OCT, debit_sats=1, fee_reserve_sats=0)
    with pytest.raises(AllowanceError, match="independent reconciliation"):
        row.release_uninvoked("attempt-1", reason="browser closed")


def test_cancel_stops_new_actions_not_reconciliation():
    row = pending(50, 10).cancel()
    with pytest.raises(AllowanceError, match="authority_cancelled"):
        row.reserve("new", "new-account", OCT, debit_sats=1, fee_reserve_sats=0)
    finished = row.commit("attempt-1", actual_spent_sats=55, evidence_ref="verified", booking_month=OCT)
    assert finished.cancelled
    assert finished.summary(OCT).spent_sats == 55
    with pytest.raises(AllowanceError):
        reserved().cancel().wallet_pending("attempt-1", wallet_action_id="operation", current_month=OCT)


def test_completed_month_does_not_carry_forward_unused_allowance():
    row = pending(50, 10).commit("attempt-1", actual_spent_sats=60, evidence_ref="verified", booking_month=OCT)
    assert row.summary(NOV).remaining_sats == 30_000
    assert row.summary(NOV).spent_sats == 0
    assert not row.summary(NOV).blocked


def test_reconciliation_is_idempotent_and_retains_actual_overrun():
    row = pending().commit("attempt-1", actual_spent_sats=30_001, evidence_ref="verified", booking_month=OCT)
    assert row.commit("attempt-1", actual_spent_sats=30_001, evidence_ref="verified", booking_month=OCT) is row
    assert row.summary(OCT).over_limit_sats == 1
    assert row.summary(OCT).remaining_sats == 0
    assert row.summary(NOV).blocked
    with pytest.raises(AllowanceError, match="cannot be replaced"):
        row.commit("attempt-1", actual_spent_sats=30_000, evidence_ref="other", booking_month=OCT)


def test_cannot_commit_uninvoked_or_released_attempt():
    for row in (reserved(), reserved().release_uninvoked("attempt-1", reason="not sent")):
        with pytest.raises(AllowanceError):
            row.commit("attempt-1", actual_spent_sats=10, evidence_ref="alleged", booking_month=OCT)


def test_timezone_is_explicit_and_boundary_is_wallet_local():
    instant = datetime(2026, 10, 31, 15, tzinfo=timezone.utc)
    assert Month.at(instant, wallet_timezone="UTC") == OCT
    assert Month.at(instant, wallet_timezone="Australia/Brisbane").month == 11
    with pytest.raises(AllowanceError):
        Month.at(datetime(2026, 10, 1), wallet_timezone="UTC")
    with pytest.raises(AllowanceError):
        Month.at(instant, wallet_timezone="invented/timezone")
    with pytest.raises(AllowanceError):
        ledger().summary(Month(2026, 10, "Australia/Brisbane"))


@pytest.mark.parametrize("year,month", [(True, 1), (2026, True), (2026, 0), (2026, 13)])
def test_bad_months(year, month):
    with pytest.raises(AllowanceError):
        Month(year, month, "UTC")


def test_period_cannot_regress_after_new_month_attempt():
    row = ledger().reserve("nov", "account", NOV, debit_sats=10, fee_reserve_sats=0)
    assert "period_regression" in row.summary(OCT).reasons


def test_restore_candidate_rejects_duplicate_or_malformed_rows():
    a = Attempt("one", "account", OCT, 10)
    with pytest.raises(AllowanceError):
        Allowance("authority", "UTC", attempts=(a, a))
    with pytest.raises(AllowanceError):
        Attempt("one", "account", OCT, 10, state="committed", spent_sats=10)
    with pytest.raises(AllowanceError):
        Attempt("one", "account", OCT, 10, state="reserved", spent_sats=10)


def test_wallet_pending_is_idempotent_but_action_cannot_change():
    row = pending(50, 10)
    assert row.wallet_pending("attempt-1", wallet_action_id="local-operation-1", current_month=OCT) is row
    with pytest.raises(AllowanceError):
        row.wallet_pending("attempt-1", wallet_action_id="replacement", current_month=OCT)
    uncertain = row.uncertain("attempt-1")
    assert uncertain.uncertain("attempt-1") is uncertain


def test_reserved_attempt_cannot_start_signing_after_month_boundary():
    with pytest.raises(AllowanceError, match="before wallet signing"):
        reserved().wallet_pending("attempt-1", wallet_action_id="op", current_month=NOV)


def test_booking_period_mismatch_retains_original_reservation():
    row = pending(50, 10).uncertain("attempt-1")
    with pytest.raises(AllowanceError, match="period mismatch"):
        row.commit("attempt-1", actual_spent_sats=60, evidence_ref="nov-booking", booking_month=NOV)
    assert row.summary(OCT).reserved_sats == 60
    assert row.summary(NOV).blocked


def test_second_reserved_attempt_cannot_sign_when_first_becomes_uncertain():
    row = pending(50, 10).reserve("second", "second-account", OCT,
                                debit_sats=10, fee_reserve_sats=0).uncertain("attempt-1")
    with pytest.raises(AllowanceError, match="before wallet signing"):
        row.wallet_pending("second", wallet_action_id="second-op", current_month=OCT)
