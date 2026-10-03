"""Terminal charge waiver, not a retry, refund, payment or ledger deletion."""
import asyncio
import copy
import json
import re
from datetime import datetime

from .api import WalletError
from .session_review import decimal, digest, now, verify_output
from .session_closure import ensure_open

SERVICES = ("prepare_existing_charge_waiver", "waive_existing_charge")
PRE_PERMIT = {"ready", "wallet_attempt_reserved", "recovery_ready"}
PERMIT_FIELDS = ("submission_authorised_at", "draft_hash", "signed_raw", "txid")


async def inspect(api, data):
    """Read-only exact-target review. Chain calls, when requested, are GET only."""
    review_id, budget_id = data.get("review_id"), data.get("budget_id")
    if bool(review_id) == bool(budget_id):
        raise WalletError("Select exactly one manual review or collection budget")
    if review_id:
        target = api.reviews.get(review_id)
        if (target["direction"] != "driver_to_operator" or not target.get("payment_request")
                or target.get("receipt") or target.get("credit_draft_id")
                or target["state"] not in ("awaiting_driver_payment", "expired_awaiting_reconciliation")):
            raise WalletError("Only an issued driver request without an allocated payment can use this waiver")
        account = target["account"]
        proxy = target["proxy_config_entry_id"]
        recipient, amount = target["recipient_address"], target["amount_sats"]
        target_kind, target_id = "manual", review_id
    else:
        row = api.saved["session_budgets"].get(budget_id)
        if not row:
            raise WalletError("Unknown collection budget")
        target = api.collections.get(row)
        if not target or target["state"] not in PRE_PERMIT or any(
                target.get(k) is not None for k in PERMIT_FIELDS):
            raise WalletError("Signing-authorised, signed or submitted collections cannot be waived here")
        quote = json.loads(target["quote"]["payload"])
        account, recipient, amount = quote["account"], quote["recipient_address"], quote["amount_sats"]
        proxy = row["proxy_config_entry_id"]
        if account["session_id"] != api.collections.session_id(row):
            raise WalletError("Collection account does not match its session")
        target_kind, target_id = "collection", budget_id
    sid = account["session_id"]
    key = proxy + "|" + sid
    ensure_open(api, key)
    if (decimal(account["net_amount_aud"]) <= 0 or type(amount) is not int
            or not 0 < amount <= 100000 or recipient != api.identity["address"]):
        raise WalletError("Only a known positive driver charge to this operator can be waived")
    current = await api.reviews.source(proxy, sid)
    if digest(current) != digest(account):
        raise WalletError("The frozen account changed; resolve the data before waiver")
    manual_owner = api.saved["session_review_index"].get(key)
    collection_owner = api.saved["driver_collection_index"].get(key)
    if ((review_id and (manual_owner != review_id or collection_owner))
            or (budget_id and (collection_owner != budget_id or manual_owner))
            or api.saved.get("automatic_credit_index", {}).get(key)):
        raise WalletError("Another settlement owns this account")
    if any(p.get("session_id") == sid for p in api.saved.get("automatic_credits", {}).values()):
        raise WalletError("An operator-credit record exists for this account")
    # Multiple invitations can exist, but a competing collection is never hidden.
    budgets = {}
    for bid, row in api.saved["session_budgets"].items():
        if row["proxy_config_entry_id"] == proxy and api.collections.session_id(row) == sid:
            if bid != budget_id and api.collections.get(row):
                raise WalletError("Another collection attempt needs reconciliation")
            budgets[bid] = {"state": row["state"], "invitation_hash": digest(row["invitation"]),
                            "receipt_hash": digest(row.get("receipt"))}
    receipt = None
    txid, index = data.get("received_txid"), data.get("received_output_index")
    if txid is not None or index is not None:
        if (not isinstance(txid, str) or not re.fullmatch(r"[0-9a-f]{64}", txid)
                or type(index) is not int or index < 0):
            raise WalletError("Supply a valid received transaction and output index")
        point = f"{txid}:{index}"
        if point in api.saved["received_outpoints"] or point in api.saved.get("unallocated_receipts", {}):
            raise WalletError("The received output already has an accounting owner")
        raw = await api.chain.request("GET", f"/tx/{txid}/hex", raw=True)
        await api.hass.async_add_executor_job(verify_output, raw, txid, index, recipient, amount)
        details = await api.chain.details(txid)
        count = details.get("confirmations")
        if type(count) is not int or count < 1:
            raise WalletError("Confirmed provider evidence is required for the separate receipt")
        block_time = details.get("blocktime", details.get("time"))
        if (type(block_time) in (int, float) and block_time <
                datetime.fromisoformat(target["created_at"]).timestamp()):
            raise WalletError("Received transaction predates this request")
        receipt = {"outpoint": point, "txid": txid, "output_index": index,
                   "recipient_address": recipient, "amount_sats": amount,
                   "confirmations": count, "checked_at": now().isoformat(),
                   "state": "received_unallocated", "refund_authorised": False,
                   "session_id": sid,
                   "evidence": "Exact raw output verified; provider confirmations, not independent SPV"}
    stable_receipt = ({k: receipt[k] for k in ("outpoint", "recipient_address", "amount_sats")}
                      if receipt else None)
    fingerprint = digest({"key": key, "target_kind": target_kind, "target_id": target_id,
                          "target": target, "account": account, "budgets": budgets,
                          "received_output": stable_receipt})
    return {"session_id": sid, "transaction_id": account["ocpp_transaction_id"],
            "target_kind": target_kind, "target_id": target_id, "prior_state": target["state"],
            "amount_sats": amount, "account": copy.deepcopy(account),
            "review_hash": fingerprint, "received_funds": receipt,
            "external_wallet_outcome_not_proven": target_kind == "collection",
            "_key": key, "_budgets": list(budgets)}


def public(plan):
    return {k: copy.deepcopy(v) for k, v in plan.items() if not k.startswith("_")}


async def execute(api, action, data, user_id):
    if not user_id:
        raise WalletError("An authenticated administrator must authorise a charge waiver")
    plan = await inspect(api, data)
    if action == "prepare_existing_charge_waiver":
        return public(plan)
    if (action != "waive_existing_charge" or data.get("expected_review_hash") != plan["review_hash"]
            or data.get("expected_amount_sats") != plan["amount_sats"]
            or type(data.get("expected_amount_sats")) is not int):
        raise WalletError("The reviewed charge changed; review it again")
    if not all(data.get(k) is True for k in (
            "confirm_waive_charge", "confirm_no_refund", "confirm_external_payments_need_separate_accounting")):
        raise WalletError("Confirm the waiver, no refund, and separate accounting of external receipts")
    if plan["received_funds"] and data.get("confirm_received_funds_unallocated") is not True:
        raise WalletError("Explicitly retain the received funds as unallocated; waiver is not a refund")
    reason = data.get("reason")
    if (not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 300
            or any(ord(c) < 32 for c in reason)):
        raise WalletError("Supply a plain-text waiver reason, 8 to 300 characters")
    before = copy.deepcopy(api.saved)
    key, sid = plan["_key"], plan["session_id"]
    container = "session_reviews" if plan["target_kind"] == "manual" else "driver_collections"
    target = api.saved[container][plan["target_id"]]
    row = {"state": "waived", "session_id": sid, "transaction_id": plan["transaction_id"],
           "account": copy.deepcopy(plan["account"]), "net_amount_aud": plan["account"]["net_amount_aud"],
           "amount_sats": plan["amount_sats"], "quality_flags": plan["account"]["quality_flags"],
           "reason": reason.strip(), "closed_at": now().isoformat(), "closed_by": user_id,
           "review_hash": plan["review_hash"], "target_kind": plan["target_kind"],
           "target_id": plan["target_id"], "prior_record": copy.deepcopy(target),
           "prior_budget_states": {bid: api.saved["session_budgets"][bid]["state"] for bid in plan["_budgets"]},
           "received_funds": copy.deepcopy(plan["received_funds"]), "refund_authorised": False,
           "external_wallet_outcome_not_proven": plan["external_wallet_outcome_not_proven"]}
    api.saved["closed_sessions"][key] = row
    if plan["received_funds"]:
        receipt = copy.deepcopy(plan["received_funds"])
        receipt.update(closure_key=key, recorded_by=user_id, reason=reason.strip())
        point = receipt["outpoint"]
        api.saved.setdefault("unallocated_receipts", {})[point] = receipt
        api.saved["received_outpoints"][point] = "waiver_receipt:" + key
    target.update(state="waived", waiver_key=key)
    # Keep historical receiving registrations valid for OTHER sessions. This
    # terminal state revokes charge authority only; it does not revoke the driver.
    for bid in plan["_budgets"]:
        budget = api.saved["session_budgets"][bid]
        if budget["state"] != "revoked":
            budget["state"] = "charge_waived"
    try:
        await api.store.async_save(api.saved)
    except (Exception, asyncio.CancelledError):
        api.saved.clear()
        api.saved.update(before)
        raise
    return {k: copy.deepcopy(v) for k, v in row.items()
            if k not in ("closed_by", "prior_record", "prior_budget_states")}
