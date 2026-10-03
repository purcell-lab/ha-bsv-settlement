"""Audited closure of unpaid accounts. Never signs or broadcasts a payment."""
import asyncio
import copy
from datetime import datetime
from decimal import ROUND_HALF_UP

from .api import WalletError
from .const import DOMAIN
from .session_review import account_snapshot, decimal, digest, now, WARNING_FLAGS
from .budget import sha

ACCEPTABLE = {"import:energy_without_matching_state", "export:energy_without_matching_state"}
FINAL = {"waived", "closed_zero"}


def ensure_open(api, key):
    if api.saved.get("closed_sessions", {}).get(key, {}).get("state") in FINAL:
        raise WalletError("This account is closed without payment. Do not collect or credit it automatically")


def reviewed_snapshot(record, accepted_flags=()):
    """Retain warnings; relax only the explicitly reviewed allocation flags."""
    allowed = set(accepted_flags)
    if not allowed <= ACCEPTABLE:
        raise WalletError("This metering issue cannot be accepted as provisional")
    clean = copy.deepcopy(record)
    clean["quality_flags"] = [f for f in record.get("quality_flags", []) if f not in allowed]
    result = account_snapshot(clean)
    for field in ("import_kwh", "export_kwh"):
        if not 0 <= decimal(result[field]) <= 1000000000:
            raise WalletError("Invalid session energy")
    if datetime.fromisoformat(result["ended_at"]) > now():
        raise WalletError("Session end is in the future")
    result["quality_flags"] = copy.deepcopy(record.get("quality_flags", []))
    return result


def collection_account(row, record):
    closed = row["terms"].get("closed_session_review")
    if not closed:
        return account_snapshot(record)
    account = reviewed_snapshot(record, closed["accepted_flags"])
    if digest(account) != digest(closed["account"]):
        raise WalletError("The reviewed closed account changed; do not collect")
    return account


class ClosedSessions:
    def __init__(self, api):
        self.api = api
        api.saved.setdefault("closed_sessions", {})

    def summary(self):
        return [{k: copy.deepcopy(r.get(k)) for k in (
            "session_id", "transaction_id", "state", "net_amount_aud", "reason", "closed_at",
            "quality_flags", "amount_sats", "received_funds")} for r in self.api.saved.get("closed_sessions", {}).values()]

    async def inspect(self, data):
        api = self.api
        proxy = api.hass.data.get(DOMAIN, {}).get(data["proxy_config_entry_id"])
        if proxy is None or proxy.mode != "sensor_proxy":
            raise WalletError("Select a loaded session recorder")
        await proxy.async_request_refresh()
        if (proxy.data or {}).get("issues"):
            raise WalletError("Resolve recorder availability issues first")
        records = [(proxy.data or {}).get("latest_session"), (proxy.data or {}).get("previous_session"), *proxy.archive]
        record = next((r for r in records if r and r["session_id"] == data["session_id"]), None)
        if not record or not record.get("ended_at"):
            raise WalletError("Select a completed session from retained history")
        flags = sorted(set(record.get("quality_flags", [])) - WARNING_FLAGS)
        warnings = sorted(set(record.get("quality_flags", [])) & WARNING_FLAGS)
        # No missing baseline, unknown amount, unpriced interval or other hard
        # quality error may be hidden by either a consent or waiver action.
        account = reviewed_snapshot(record, set(flags) & ACCEPTABLE)
        key = data["proxy_config_entry_id"] + "|" + data["session_id"]
        final = api.saved.get("closed_sessions", {}).get(key)
        if final:
            return {"state": final["state"], "session_id": data["session_id"],
                    "account": account, "reason": final["reason"], "closed": True}
        pending = []
        for row in api.saved["session_budgets"].values():
            if row["proxy_config_entry_id"] != data["proxy_config_entry_id"] or api.collections.session_id(row) != data["session_id"]:
                continue
            if api.collections.get(row) or api.auto_credits.get(row):
                raise WalletError("A settlement already exists. Track or reconcile it; do not create another request or waive it")
            if api.budgets.public(row)["state"] not in ("expired", "revoked"):
                if row.get("receipt"):
                    raise WalletError("A signed approval already exists. Use its guarded recovery workflow")
                pending.append(row)
        if len(pending) > 1:
            raise WalletError("Multiple pending invitations require reconciliation")
        for index in ("driver_collection_index", "automatic_credit_index"):
            if key in api.saved.get(index, {}):
                raise WalletError("An existing settlement owns this account; reconcile it")
        rid = api.saved.get("session_review_index", {}).get(key)
        if rid and api.saved["session_reviews"][rid]["state"] != "cancelled":
            raise WalletError("A manual review already exists. Cancel an unsigned review or reconcile its payment")
        for item in api.saved.get("automatic_credits", {}).values():
            if item.get("session_id") == data["session_id"]:
                raise WalletError("An operator credit record exists. Do not waive a driver's credit")
        state = api.hass.states.get(data["conversion_rate_entity"])
        if state is None or state.attributes.get("unit_of_measurement") != "sat/AUD":
            raise WalletError("Select a valid conversion sensor")
        rate = decimal(state.state)
        if not 0 < rate <= 100000000:
            raise WalletError("Invalid conversion rate")
        aud = decimal(account["net_amount_aud"])
        amount = int((aud * rate).quantize(decimal("1"), rounding=ROUND_HALF_UP))
        if aud < 0:
            raise WalletError("This account is a credit owed to the driver. Use the operator-credit workflow, not waiver")
        if aud > 0 and amount < 1:
            raise WalletError("Account rounds below one satoshi; review the conversion")
        old = pending[0] if pending else None
        details = {"account": account, "satoshis_per_aud": str(rate),
                   "conversion_rate_entity": data["conversion_rate_entity"],
                   "proxy_config_entry_id": data["proxy_config_entry_id"],
                   "pending_budget_id": old["terms"]["budget_id"] if old else None,
                   "pending_invitation_hash": sha(old["invitation"]["payload"]) if old else None}
        return {"state": "data_review_required" if flags else "ready_for_resolution",
                "session_id": data["session_id"], "account": account, "amount_sats": amount,
                "satoshis_per_aud": str(rate), "accepted_flags": flags,
                "warning_flags": warnings,
                "review_hash": digest(details), "pending_budget_id": details["pending_budget_id"],
                "pending_invitation_hash": details["pending_invitation_hash"], "closed": False}

    async def execute(self, action, data, user_id):
        from .owned_waiver import SERVICES, execute
        if action in SERVICES:
            return await execute(self.api, action, data, user_id)
        if not user_id:
            raise WalletError("An authenticated administrator must review account closure")
        plan = await self.inspect(data)
        if action == "prepare_session_closure":
            return plan
        if plan.get("closed"):
            raise WalletError("This account is already closed")
        if data.get("expected_review_hash") != plan["review_hash"] or data.get("confirm_account_review") is not True:
            raise WalletError("Account or approval changed. Review the current account before confirming")
        reason = data.get("reason", "")
        if not isinstance(reason, str) or not 8 <= len(reason.strip()) <= 300 or any(ord(c) < 32 for c in reason):
            raise WalletError("Supply a short plain-text reason, 8 to 300 characters")
        if plan["accepted_flags"] and data.get("confirm_provisional_metering") is not True:
            raise WalletError("Explicitly accept and disclose the provisional metering warning")
        api = self.api
        if action == "request_closed_session_consent":
            if plan["amount_sats"] <= 0:
                raise WalletError("A zero account needs no driver consent")
            maximum = data.get("max_total_sats", 1000)
            fee = data.get("max_fee_sats", 1000)
            if type(maximum) is not int or maximum < plan["amount_sats"] + (1 if fee else 0):
                raise WalletError("Total limit must cover the final account and leave fee headroom")
            args = {k: data[k] for k in ("proxy_config_entry_id", "session_id", "conversion_rate_entity")}
            args.update({k: data[k] for k in ("operator_name", "operator_contact", "max_total_sats",
                                           "max_fee_sats", "valid_minutes") if k in data})
            if plan["pending_budget_id"]:
                if data.get("confirm_replace_pending") is not True:
                    raise WalletError("Confirm revocation of the existing unapproved invitation")
                args.update(replace_pending_budget_id=plan["pending_budget_id"],
                            expected_invitation_hash=plan["pending_invitation_hash"], confirm_replace_pending=True)
            return await api.budgets.create(args, user_id, closed_review={
                "account": plan["account"], "accepted_flags": plan["accepted_flags"],
                "reason": reason.strip(), "reviewed_at": now().isoformat(),
                "amount_sats": plan["amount_sats"], "satoshis_per_aud": plan["satoshis_per_aud"]})
        if action != "waive_session_charge" or data.get("confirm_no_payment") is not True:
            raise WalletError("Confirm closure without payment")
        key = data["proxy_config_entry_id"] + "|" + data["session_id"]
        # Retire all pending invitations for this account, not an unrelated
        # future reservation. Signed/attempted records were refused above.
        if plan["pending_budget_id"]:
            previous_state = api.saved["session_budgets"][plan["pending_budget_id"]]["state"]
            api.saved["session_budgets"][plan["pending_budget_id"]]["state"] = "revoked"
        row = {"session_id": data["session_id"], "transaction_id": plan["account"]["ocpp_transaction_id"],
               "state": "waived" if decimal(plan["account"]["net_amount_aud"]) > 0 else "closed_zero",
               "account": copy.deepcopy(plan["account"]), "net_amount_aud": plan["account"]["net_amount_aud"],
               "quality_flags": plan["account"]["quality_flags"], "reason": reason.strip(),
               "closed_at": now().isoformat(), "closed_by": user_id, "review_hash": plan["review_hash"]}
        api.saved.setdefault("closed_sessions", {})[key] = row
        try:
            await api.store.async_save(api.saved)
        except (Exception, asyncio.CancelledError):
            api.saved["closed_sessions"].pop(key, None)
            if plan["pending_budget_id"]:
                api.saved["session_budgets"][plan["pending_budget_id"]]["state"] = previous_state
            raise
        return {k: copy.deepcopy(v) for k, v in row.items() if k != "closed_by"}
