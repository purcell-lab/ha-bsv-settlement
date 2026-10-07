"""Explicit renewal of expired, never-started wallet adjustments only."""
import asyncio
import copy
from datetime import datetime, timedelta
import re

from .api import WalletError
from .adjustment_collection import FORMAT
from .energy_adjustment import KIND, MAX_TOTAL, check_recipient
from .session_review import digest, now

CONFIRMATIONS = (
    "confirm_driver_wallet_checked", "confirm_recipient_history_checked",
    "confirm_unsigned_draft_cancelled_or_absent", "confirm_old_driver_pages_closed",
    "confirm_renewal",
)


def prepare(reviews, data):
    review = reviews.get(data["review_id"])
    request = review.get("payment_request") or {}
    if (review.get("account_kind") != KIND
            or review.get("direction") != "driver_to_operator"
            or review.get("state") != "awaiting_driver_payment"
            or review.get("wallet_collection_enabled") is not True
            or request.get("format") != FORMAT
            or not review.get("one_click_authorised_at") or not review.get("approved_at")
            or review.get("one_click_total_limit_sats") != MAX_TOTAL):
        raise WalletError("Only an original wallet-connected debit request can be renewed")
    if any(review.get(k) is not None for k in (
            "wallet_collection", "receipt", "credit_draft_id", "driver_payment_raw",
            "signed_raw", "txid", "submission_authorised_at")):
        raise WalletError("An existing wallet/payment record requires reconciliation, not renewal")
    try:
        expiry = datetime.fromisoformat(review["expires_at"])
        expired = expiry.tzinfo is not None and now() >= expiry
    except (ValueError, TypeError, KeyError):
        expired = False
    if not expired:
        raise WalletError("Only an expired request can be renewed")
    if (digest(review["frozen_terms"]) != review["terms_hash"]
            or any(review.get(k) != v for k, v in review["frozen_terms"].items()
                   if k != "identity_verification")
            or digest(review["account"]) != review["source_hash"]
            or review["recipient_address"] != reviews.api.identity["address"]
            or type(review["amount_sats"]) is not int
            or not 1 <= review["amount_sats"] < MAX_TOTAL
            or any(request.get(k) != review[k] for k in (
                "amount_sats", "recipient_address", "expires_at", "terms_hash"))
            or request.get("reference") != review["review_id"]):
        raise WalletError("Original adjustment terms changed")
    check_recipient(reviews.api, review)
    from .session_closure import ensure_open
    ensure_open(reviews.api, review["proxy_config_entry_id"] + "|" + review["account"]["session_id"])
    if len(review.get("adjustment_renewals", [])) >= 10:
        raise WalletError("Renewal audit limit reached")
    return {
        "review_id": review["review_id"], "eligible": True,
        "expected_review_hash": digest(review),
        "amount_sats": review["amount_sats"], "max_total_sats": MAX_TOTAL,
        "recipient_address": review["recipient_address"],
        "expired_at": review["expires_at"], "new_valid_minutes": 10,
        "wallet_attempt_recorded": False, "payment_sent": False,
        "requires_fresh_driver_claim": True,
        "required_confirmations": list(CONFIRMATIONS),
    }


async def execute(reviews, action, data, user_id):
    if not user_id:
        raise WalletError("An authenticated administrator must review adjustment renewal")
    result = prepare(reviews, data)
    if action == "prepare_adjustment_renewal":
        return result
    if data.get("expected_review_hash") != result["expected_review_hash"]:
        raise WalletError("Adjustment changed; prepare a fresh renewal review")
    if not all(data.get(k) is True for k in CONFIRMATIONS):
        raise WalletError("Check wallet and recipient history and close old pages before renewal")
    evidence = data.get("evidence_reference")
    if not isinstance(evidence, str) or not re.fullmatch(r"[A-Za-z0-9 _.:/-]{8,200}", evidence):
        raise WalletError("Supply a nonsecret evidence reference")
    review = reviews.get(data["review_id"])
    previous = copy.deepcopy(review)
    stamp = now()
    audit = {
        "renewed_at": stamp.isoformat(), "renewed_by": user_id,
        "evidence_reference": evidence, "previous_review_hash": result["expected_review_hash"],
        "previous_frozen_terms": copy.deepcopy(review["frozen_terms"]),
        "previous_payment_request": copy.deepcopy(review["payment_request"]),
        "operator_attested": True, "independently_verified": False,
    }
    # Same debt, identity, tariff, amount, index and recipient. Only the quote
    # expiry changes. No wallet attempt exists to reset; a fresh claim is needed.
    review["expires_at"] = (stamp + timedelta(minutes=10)).isoformat()
    review["frozen_terms"]["expires_at"] = review["expires_at"]
    review["terms_hash"] = digest(review["frozen_terms"])
    review["payment_request"].update(expires_at=review["expires_at"], terms_hash=review["terms_hash"])
    review.setdefault("adjustment_renewals", []).append(audit)
    try:
        await reviews.save()
    except (Exception, asyncio.CancelledError):
        review.clear()
        review.update(previous)
        raise
    return reviews.public(review)
