"""Reusable recovery inspection and a fail-closed, non-payment dispatcher."""
from datetime import datetime

from .api import WalletError
from . import adjustment_renewal, collection_recovery
from .session_review import digest, now

TYPES = ("review", "budget", "credit", "payment")
CONFIRMATIONS = (*adjustment_renewal.CONFIRMATIONS[:-1], "confirm_recovery")


def resolve(api, kind, identifier):
    """Exact identifiers only. Never search for a newer or similar record."""
    if kind == "review":
        row = api.reviews.get(identifier)
        item = row.get("wallet_collection") or row.get("receipt")
        if row.get("credit_draft_id"):
            item = api.saved.get("payments", {}).get(row["credit_draft_id"])
        return row, item, row.get("direction") == "operator_to_driver"
    if kind == "budget":
        row = api.saved.get("session_budgets", {}).get(identifier)
        if row is not None:
            debit, credit = api.collections.get(row), api.auto_credits.get(row)
            if debit is not None and credit is not None:
                raise WalletError("Conflicting payment routes require manual reconciliation")
            return row, debit if debit is not None else credit, credit is not None
    elif kind == "credit":
        if identifier.startswith("adjustment:"):
            from .energy_adjustment import credit_item
            row, item = credit_item(api, identifier)
            return row, item, True
        row = api.ongoing_credits.routes.get(identifier)
        if row is not None:
            return row, api.ongoing_credits.get(api.ongoing_credits.wrapper(row)), True
    elif kind == "payment":
        item = api.saved.get("payments", {}).get(identifier)
        if item is not None:
            return item, item, True
    raise WalletError("Unknown exact settlement record")


def prepare(api, data):
    kind, identifier = data.get("record_type"), data.get("record_id")
    if kind not in TYPES or not isinstance(identifier, str) or not 1 <= len(identifier) <= 300:
        raise WalletError("Select an exact supported settlement identifier")
    row, item, credit = resolve(api, kind, identifier)
    item = item or {}
    state = item.get("state") or row.get("state") or "not_started"
    if kind == "review":
        for prefix in ("driver_payment_", "credit_"):
            if state.startswith(prefix):
                state = state.removeprefix(prefix)
                break
    out = {
        "record_type": kind, "record_id": identifier, "read_only": True,
        "state": state, "direction": row.get("direction") or (
            "operator_to_driver" if credit else "driver_to_operator" if item else "not_determined"),
        "amount_sats": item.get("amount_sats", row.get("amount_sats")),
        "txid": item.get("txid"), "confirmations": item.get("confirmations"),
        "wallet_receipt_status": "wallet_reported_accepted" if item.get("wallet_receipt_ack") else "not_recorded",
        "expected_recovery_hash": digest({"record": row, "payment": item}),
        "recovery_action": "manual_review", "executable": False,
        "blockers": [],
        "payment_sent": False, "provider_checked": False,
        "reason": "No safe automatic recovery is available for this record.",
    }
    # Provider state and receipt acceptance are independent. No recovery sends
    # existing signed bytes, clears evidence, substitutes a recipient or pays.
    if state == "broadcast_unknown" or item.get("verification_error"):
        return out | {"recovery_action": "reconcile_existing_transaction",
                      "reason": "Outcome is uncertain. Check the exact retained transaction and receiving history; never resend."}
    if state == "provider_confirmed":
        confirmed = type(item.get("confirmations")) is int and item["confirmations"] > 0
        if not confirmed:
            return out | {"recovery_action": "reconcile_existing_transaction",
                          "reason": "Confirmation fields are inconsistent; retain the payment unchanged."}
        return out | {
            "recovery_action": "sync_original_receipt" if credit and not item.get("wallet_receipt_ack") else "none",
            "reason": "Payment is provider-confirmed. Sync only its original receipt with the original wallet; do not pay again."
                      if credit else "Payment is provider-confirmed. No payment recovery required."}
    if item.get("txid") or item.get("signed_raw") or state in ("submitted", "provider_unconfirmed"):
        return out | {"recovery_action": "await_existing_confirmation",
                      "reason": "Keep the existing transaction. Receipt acceptance does not establish confirmation."}
    if any(item.get(k) is not None for k in ("submission_authorised_at", "draft_hash")):
        return out | {"recovery_action": "inspect_authorised_wallet_attempt",
                      "reason": "A signing permit may have been used. Inspect the original wallet and transaction; no reset."}
    if kind == "review" and not item:
        try:
            renewal = adjustment_renewal.prepare(api.reviews, {"review_id": identifier})
        except WalletError as exc:
            out["blockers"] = [str(exc)]
        else:
            return out | {"recovery_action": "renew_never_started_adjustment", "executable": True,
                          "amount_sats": renewal["amount_sats"],
                          "recipient_address": renewal["recipient_address"],
                          "max_total_sats": renewal["max_total_sats"],
                          "expired_at": renewal["expired_at"], "new_valid_minutes": 10,
                          "required_confirmations": list(CONFIRMATIONS),
                          "reason": "Same debt, original amount and recipient. New ten-minute expiry and fresh native-wallet consent after evidence review."}
    if kind == "budget" and item.get("state") == "wallet_attempt_reserved":
        try:
            api.collections.mandate(row)
            recovery = collection_recovery.review(api.collections, row)
            valid = now() < datetime.fromisoformat(recovery["expires_at"])
        except (WalletError, ValueError, TypeError, KeyError):
            valid = False
        if valid and recovery["eligible_for_review"]:
            return out | {"recovery_action": "release_prepermit_attempt", "executable": True,
                          "amount_sats": recovery["amount_sats"],
                          "recipient_address": recovery["recipient_address"],
                          "reason": "Review wallet and receiving history before releasing the pre-permit reservation. Original mandate expiry remains unchanged.",
                          "required_confirmations": list(CONFIRMATIONS)}
    if item:
        return out | {"recovery_action": "inspect_original_wallet",
                      "reason": "An existing attempt or prepared payment must use its original workflow; this service will not replace it."}
    if row.get("state") in ("cancelled", "waived"):
        return out | {"recovery_action": "none", "reason": "This record is closed. It will not be reopened automatically."}
    return out | {"recovery_action": "review_consent_or_account",
                  "reason": "Check original consent, expiry, account and funding. Use a separately reviewed consent, credit or waiver workflow."}


async def execute(api, action, data, user_id):
    if not user_id:
        raise WalletError("An authenticated administrator must review settlement recovery")
    plan = prepare(api, data)
    if action == "prepare_settlement_recovery":
        return plan
    if not plan["executable"] or data.get("expected_recovery_hash") != plan["expected_recovery_hash"]:
        raise WalletError("Recovery is blocked or the record changed; prepare a fresh review")
    if not all(data.get(k) is True for k in CONFIRMATIONS):
        raise WalletError("Complete all recovery evidence checks and explicitly approve recovery")
    kind, identifier = data["record_type"], data["record_id"]
    if plan["recovery_action"] == "renew_never_started_adjustment":
        reviewed = adjustment_renewal.prepare(api.reviews, {"review_id": identifier})
        return await adjustment_renewal.execute(api.reviews, "renew_expired_adjustment", {
            **data, "confirm_renewal": True, "review_id": identifier,
            "expected_review_hash": reviewed["expected_review_hash"]}, user_id)
    if plan["recovery_action"] == "release_prepermit_attempt":
        row, _, _ = resolve(api, kind, identifier)
        reviewed = collection_recovery.review(api.collections, row)
        return await collection_recovery.execute(api.collections, "recover_driver_collection", {
            **data, "budget_id": identifier, "expected_quote_hash": reviewed["quote_hash"],
            "expected_claimed_at": reviewed["claimed_at"]}, user_id)
    raise WalletError("This recovery requires its separate reviewed workflow")
