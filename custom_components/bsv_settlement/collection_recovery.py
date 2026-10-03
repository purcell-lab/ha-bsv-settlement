"""Read-only recovery review and explicit, admin-only pre-permit release.

No service signs, broadcasts, invents wallet evidence or clears a signed attempt.
"""
import asyncio
import copy
import json
import re
import secrets

from bsv import PrivateKey
from .api import WalletError
from .budget import canonical, message_hash, sha
from .session_review import now

STAGES = {
    "check_quote": "Check the signed session account",
    "wallet_identity": "Check driver wallet identity",
    "wallet_network": "Check driver wallet network",
    "sign_claim": "Sign the collection claim",
    "claim_collection": "Reserve the collection attempt",
    "create_draft": "Create the unsigned wallet draft",
    "inspect_draft": "Validate the unsigned wallet draft",
    "authorise_draft": "Request the one-use signing permit",
    "recheck_wallet": "Recheck wallet before signing",
    "sign_payment": "Sign the authorised transaction",
    "inspect_signed": "Check the signed transaction",
    "submit_payment": "Submit the exact signed transaction",
}
CODES = {
    "network_request_failed": "A network request failed; the response or wallet outcome may be unknown.",
    "request_timeout": "A request timed out; the response or wallet outcome may be unknown.",
    "wallet_rejected": "The wallet rejected or could not complete the requested operation.",
    "validation_failed": "The quote, wallet draft or signing-permit checks did not pass.",
    "unexpected_error": "Collection stopped unexpectedly; inspect the wallet before recovery.",
}
DRAFT_REASONS = {
    "unsigned_draft_required": "The wallet did not return an unsigned draft.",
    "invalid_beef": "The wallet transaction could not be decoded as complete Atomic BEEF.",
    "unsupported_shape": "The transaction version, locktime, input count or output count is unsupported.",
    "funding_evidence_missing": "Complete matching funding transactions and final input sequences are required.",
    "payment_output_mismatch": "The outputs do not match the exact operator payment and supported change format.",
    "invalid_fee": "The draft fee is not a valid nonnegative integer.",
    "fee_limit_exceeded": "The wallet draft fee exceeds the approved fee cap.",
    "total_limit_exceeded": "The total wallet debit exceeds the approved spending limit.",
}
DRAFT_FIELDS = {"payment_sats", "fee_sats", "fee_cap_sats", "total_debit_sats",
                "total_cap_sats", "input_count", "output_count"}


async def record_failure(collections, row, data):
    item = collections.attempt(row, data)
    report = data.get("diagnostic")
    if (not isinstance(report, dict) or set(report) not in (
            {"event_id", "stage", "code"}, {"event_id", "stage", "code", "reason", "details"})
            or not isinstance(report["event_id"], str)
            or not re.fullmatch(r"[0-9a-f-]{36}", report["event_id"])
            or not isinstance(report["stage"], str) or not isinstance(report["code"], str)
            or report["stage"] not in STAGES or report["code"] not in CODES):
        raise WalletError("Invalid collection diagnostic")
    if "reason" in report and (
            report["stage"] not in ("inspect_draft", "inspect_signed")
            or report["code"] != "validation_failed"
            or not isinstance(report["reason"], str) or report["reason"] not in DRAFT_REASONS
            or not isinstance(report["details"], dict) or not set(report["details"]) <= DRAFT_FIELDS
            or any(type(v) is not int or not 0 <= v <= 2100000000000000
                   for v in report["details"].values())):
        raise WalletError("Invalid bounded draft diagnostic")
    if item.get("diagnostic", {}).get("event_id") == report["event_id"]:
        return {"diagnostic_saved": True}
    # Preserve the first failure, including across repeated refresh/reports.
    if not item.get("diagnostic"):
        message = DRAFT_REASONS.get(report.get("reason"), CODES[report["code"]])
        labels = {"payment_sats": "Payment", "fee_sats": "Wallet fee", "fee_cap_sats": "Fee cap",
                  "total_debit_sats": "Total debit", "total_cap_sats": "Total cap"}
        for key, label in labels.items():
            if key in report.get("details", {}):
                message += f" {label}: {report['details'][key]} sat."
        item["diagnostic"] = {
            **report, "recorded_at": now().isoformat(), "reported_by": "driver_browser",
            "verified": False, "step": STAGES[report["stage"]], "message": message,
        }
        try:
            await collections.save()
        except (Exception, asyncio.CancelledError):
            item.pop("diagnostic", None)
            raise
    return {"diagnostic_saved": True}


def review(collections, row):
    item = collections.get(row)
    if not item:
        raise WalletError("No driver collection attempt exists")
    quote = json.loads(item["quote"]["payload"])
    blocked = item["state"] != "wallet_attempt_reserved" or any(
        item.get(k) is not None for k in (
            "submission_authorised_at", "draft_hash", "signed_raw", "txid"))
    return {
        "budget_id": row["terms"]["budget_id"],
        "session_id": collections.session_id(row),
        "transaction_id": quote["account"]["ocpp_transaction_id"],
        "quote_hash": item["quote"]["hash"], "claimed_at": item.get("claimed_at"),
        "state": item["state"], "amount_sats": quote["amount_sats"],
        "max_fee_sats": quote["max_fee_sats"],
        "recipient_address": quote["recipient_address"], "expires_at": quote["expires_at"],
        "diagnostic": copy.deepcopy(item.get("diagnostic")),
        "eligible_for_review": not blocked,
        "reason": "Reconcile the existing signed/authorised attempt; do not release it." if blocked else
                  "No signing permit recorded. Wallet and recipient-history evidence must still be reviewed.",
        "payment_sent": False,
    }


async def execute(collections, action, data, user_id):
    if not user_id:
        raise WalletError("An authenticated administrator must review collection recovery")
    row = collections.api.saved["session_budgets"].get(data["budget_id"])
    if not row:
        raise WalletError("Unknown spending approval")
    if action == "get_reviewed_collection_link":
        return await reviewed_link(collections, row, data)
    result = review(collections, row)
    if action == "prepare_collection_recovery":
        return result
    item = collections.get(row)
    if (not result["eligible_for_review"]
            or data.get("expected_quote_hash") != result["quote_hash"]
            or data.get("expected_claimed_at") != result["claimed_at"]):
        raise WalletError("Collection changed or already reached signing; do not release it")
    if not all(data.get(k) is True for k in (
            "confirm_driver_wallet_checked", "confirm_recipient_history_checked",
            "confirm_unsigned_draft_cancelled_or_absent", "confirm_old_driver_pages_closed")):
        raise WalletError("Review wallet and recipient evidence and close old driver pages first")
    evidence = data.get("evidence_reference")
    if not isinstance(evidence, str) or not re.fullmatch(r"[A-Za-z0-9 _.:/-]{8,200}", evidence):
        raise WalletError("Supply a nonsecret review reference, not wallet data or a capability URL")
    await collections.current(row, item)  # Existing mandate, expiry and frozen account must still hold.
    # Duplicate calls cannot pass the compare-and-set after this state change.
    previous = copy.deepcopy(item)
    old_records = copy.deepcopy(collections.api.saved.get("collection_recoveries", []))
    audit = {
        **result, "reviewed_at": now().isoformat(), "reviewed_by": user_id,
        "evidence_reference": evidence, "previous_attempt_hash": item.get("attempt_token_hash"),
        "operator_attested": True, "independently_verified": False,
    }
    records = collections.api.saved.setdefault("collection_recoveries", [])
    if len(records) >= 100:
        raise WalletError("Recovery audit retention limit reached; export and review before continuing")
    quote = json.loads(item["quote"]["payload"])
    quote["recovery_generation"] = quote.get("recovery_generation", 0) + 1
    quote["created_at"] = now().isoformat()
    payload = canonical(quote)
    key = PrivateKey(bytes.fromhex(collections.api.identity["secret_hex"]))
    new_quote = {"payload": payload, "hash": sha(payload),
                 "signature": key.sign(payload.encode(), hasher=message_hash).hex()}
    records.append(audit)
    # Preserve account, amount, recipient, fee and ORIGINAL mandate expiry.
    item.clear()
    item.update(state="recovery_ready", source_hash=previous["source_hash"],
                quote=new_quote, created_at=previous["created_at"],
                recovery={"generation": quote["recovery_generation"],
                          "reviewed_at": audit["reviewed_at"],
                          "requires_driver_confirmation": True})
    try:
        await collections.save()
    except (Exception, asyncio.CancelledError):
        item.clear()
        item.update(previous)
        collections.api.saved["collection_recoveries"] = old_records
        raise
    return collections.public(item) | {"released_for_driver_review": True, "payment_sent": False}


async def reviewed_link(collections, row, data):
    """Redisplay an existing capability, never replace a mandate or payment.

    Called only by the authenticated administrator service under the coordinator
    lock. The public driver endpoint deliberately has no corresponding action.
    """
    item = collections.get(row)
    if (not item or item["state"] != "recovery_ready"
            or not item.get("recovery", {}).get("requires_driver_confirmation")
            or any(item.get(k) is not None for k in (
                "claimed_at", "attempt_token_hash", "submission_authorised_at",
                "draft_hash", "signed_raw", "txid"))):
        raise WalletError("Only an unclaimed, reviewed collection can redisplay its private link")
    if (data.get("expected_quote_hash") != item["quote"]["hash"]
            or data.get("confirm_private_link_disclosure") is not True):
        raise WalletError("Confirm private link disclosure for the unchanged reviewed quote")
    # Reuse expiry, approval, manual-payment conflict and frozen-account guards.
    await collections.current(row, item)
    if (collections.api.saved["driver_collection_index"].get(collections.key(row))
            != row["terms"]["budget_id"]):
        raise WalletError("This approval does not own the session collection")
    if row.get("driver_link_scheme") != "hmac-sha256-v1":
        raise WalletError("The original private link cannot be safely redisplayed")
    token = collections.api.budgets.link_token(row["terms"]["budget_id"])
    if not secrets.compare_digest(sha(token), row.get("driver_token_hash", "")):
        raise WalletError("The original private link cannot be safely redisplayed")
    quote = json.loads(item["quote"]["payload"])
    return {
        "budget_id": row["terms"]["budget_id"],
        "session_id": collections.session_id(row),
        "transaction_id": quote["account"]["ocpp_transaction_id"],
        "state": item["state"], "amount_sats": quote["amount_sats"],
        "max_fee_sats": quote["max_fee_sats"], "expires_at": quote["expires_at"],
        "driver_link_fragment": f"#budget={row['terms']['budget_id']}&token={token}",
        "same_link": True, "requires_driver_confirmation": True, "payment_sent": False,
    }
