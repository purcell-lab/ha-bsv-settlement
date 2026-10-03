"""Opt-in aggregate driver mandates. No server-side driver key or offline debit.

Each collection is a separately indexed, operator-signed session ticket. The
driver's root consent, expiry, latest registration and aggregate obligation are
checked again before claim, signing permit and first submission.
"""
import copy
from datetime import datetime
import json
from uuid import uuid5, UUID

from bsv import PrivateKey, PublicKey
from .api import WalletError
from .budget import canonical, message_hash, sha, approval_payload, SPENDING_STATE
from .const import DOMAIN
from .session_review import now, account_snapshot, decimal

SCOPE = "multi_session_aggregate_spending_until_expiry_or_new_driver"
DAYS = 7


def weekly_terms(terms):
    return terms.get("version") == 3 and terms.get("scope") == SCOPE


def children(api, parent):
    return [r for r in api.saved["session_budgets"].values()
            if r.get("weekly_parent_id") == parent["terms"]["budget_id"]]


def obligation(api, child):
    item = api.collections.get(child)
    if not item:
        return 0
    q = json.loads(item["quote"]["payload"])
    # Reservations never recycle due to errors, waivers, reorgs or credit receipts.
    return q["amount_sats"] + (item["fee_sats"] if "fee_sats" in item else q["max_fee_sats"])


def remaining(api, parent, excluding=None):
    used = sum(obligation(api, r) for r in children(api, parent)
               if r["terms"]["budget_id"] != excluding)
    return parent["terms"]["max_total_sats"] - used


def verify_parent(api, parent, *, active=True):
    t = parent["terms"]
    try:
        if not weekly_terms(t) or json.loads(parent["invitation"]["payload"]) != t:
            raise ValueError()
        if t["operator_identity"] != api.identity["public_key"]:
            raise ValueError()
        operator = PublicKey(bytes.fromhex(t["operator_identity"]))
        if not operator.verify(bytes.fromhex(parent["invitation"]["signature"]),
                               parent["invitation"]["payload"].encode(), hasher=message_hash):
            raise ValueError()
        receipt = parent["receipt"]
        if receipt["payload"] != approval_payload(parent["invitation"], receipt["driver_identity"]):
            raise ValueError()
        driver = PublicKey(bytes.fromhex(receipt["driver_identity"])).derive_child(
            PrivateKey(1), f"2-ev session spending-{t['budget_id']}")
        if not driver.verify(bytes.fromhex(receipt["signature"]),
                             receipt["payload"].encode(), hasher=message_hash):
            raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise WalletError("Invalid aggregate driver mandate") from None
    if not active:
        return
    if api.budgets.public(parent)["state"] != SPENDING_STATE:
        raise WalletError("Multi-session approval expired or was revoked")
    if not parent.get("credit_destination"):
        raise WalletError("Register the driver's receiving wallet to activate the multi-session approval")
    registered = datetime.fromisoformat(parent["credit_destination"]["registered_at"])
    if any(r is not parent and r.get("credit_destination")
           and not r.get("weekly_parent_id")
           and r["proxy_config_entry_id"] == parent["proxy_config_entry_id"]
           and datetime.fromisoformat(r["credit_destination"]["registered_at"]) > registered
           for r in api.saved["session_budgets"].values()):
        raise WalletError("A newer driver registration ended this approval")
    # Verify receiving proof as well; no fallback to an older/invalid driver.
    api.ongoing_credits.verified_registration(parent)


def guard_child(api, child):
    parent = api.saved["session_budgets"].get(child.get("weekly_parent_id"))
    if not parent:
        raise WalletError("Original aggregate approval is unavailable")
    verify_parent(api, parent)
    p, t = parent["terms"], child["terms"]
    if (t.get("version") != 2 or t.get("session_mode") != "existing_session"
            or child["proxy_config_entry_id"] != parent["proxy_config_entry_id"]
            or t.get("weekly_parent_hash") != sha(parent["invitation"]["payload"])
            or child.get("receipt") != parent["receipt"]
            or child.get("state") != SPENDING_STATE
            or t["operator_address"] != p["operator_address"]
            or t["operator_identity"] != p["operator_identity"]
            or t["satoshis_per_aud"] != p["satoshis_per_aud"]
            or t["expires_at"] != p["expires_at"]
            or t["max_total_sats"] > p["max_total_sats"]
            or t["max_fee_sats"] > p["max_fee_sats"]
            or any(t[k] != p[k] for k in ("pricing_rule", "import_price_entity", "export_price_entity"))
            or json.loads(child["invitation"]["payload"]) != t):
        raise WalletError("Derived session ticket changed")
    if not PublicKey(bytes.fromhex(p["operator_identity"])).verify(
            bytes.fromhex(child["invitation"]["signature"]),
            child["invitation"]["payload"].encode(), hasher=message_hash):
        raise WalletError("Derived session ticket signature is invalid")
    from .session_closure import ensure_open
    ensure_open(api, api.collections.key(child))
    item = api.collections.get(child)
    if item and obligation(api, child) > remaining(api, parent, t["budget_id"]):
        raise WalletError("Aggregate spending allowance is exhausted")


def summary(api, parent):
    t = parent["terms"]
    error = None
    try:
        verify_parent(api, parent)
    except WalletError as exc:
        error = str(exc)
    left = remaining(api, parent)
    return {"max_total_sats": t["max_total_sats"], "remaining_sats": max(0, left),
            "committed_sats": t["max_total_sats"] - left, "expires_at": t["expires_at"],
            "active": error is None and left > 0, "error": error,
            "credits_replenish_budget": False,
            "collections": [{"budget_id": r["terms"]["budget_id"],
                "session_id": r["terms"]["session_id"],
                "transaction_id": r["terms"]["transaction_id"],
                **api.collections.public(api.collections.get(r))}
                for r in children(api, parent) if api.collections.get(r)]}


async def ticket(api, parent, session_id):
    """Create a deterministic child only for a closed, post-registration account."""
    existing = next((r for r in children(api, parent) if r["terms"]["session_id"] == session_id), None)
    if existing:
        return existing
    verify_parent(api, parent)
    proxy = api.hass.data.get(DOMAIN, {}).get(parent["proxy_config_entry_id"])
    if not proxy or proxy.mode != "sensor_proxy":
        raise WalletError("Session recorder unavailable")
    await proxy.async_request_refresh()
    verify_parent(api, parent)  # Refresh can cross expiry or a registration change.
    if (proxy.data or {}).get("issues"):
        raise WalletError("Resolve recorder issues before collection")
    records = api.ongoing_credits.records(proxy)
    record = next((r for r in records if r["session_id"] == session_id), None)
    if not record:
        raise WalletError("Session is not retained")
    account = account_snapshot(record)
    start = datetime.fromisoformat(record["opened_at"])
    registered = datetime.fromisoformat(parent["credit_destination"]["registered_at"])
    expiry = datetime.fromisoformat(parent["terms"]["expires_at"])
    if not registered <= start < datetime.fromisoformat(account["ended_at"]) <= now() < expiry:
        raise WalletError("Session or collection is outside the signed multi-session window")
    if decimal(account["net_amount_aud"]) <= 0:
        raise WalletError("This account is not a driver charge; use the separate credit or zero-balance flow")
    key = parent["proxy_config_entry_id"] + "|" + session_id
    if (key in api.saved["driver_collection_index"] or key in api.saved["automatic_credit_index"]
            or key in api.saved.get("session_review_index", {}) or key in api.saved.get("closed_sessions", {})):
        raise WalletError("An existing settlement record owns this session")
    left = remaining(api, parent)
    if left <= 0:
        raise WalletError("Aggregate spending allowance is exhausted")
    p = parent["terms"]
    child_id = str(uuid5(UUID(p["budget_id"]), key))
    from .budget import SPENDING_SCOPE, payment_authority
    t = copy.deepcopy(p)
    t.update(version=2, budget_id=child_id, session_id=session_id,
             transaction_id=record["ocpp_transaction_id"], session_mode="existing_session",
             scope=SPENDING_SCOPE, max_total_sats=left, max_fee_sats=min(left, p["max_fee_sats"]),
             weekly_parent_hash=sha(parent["invitation"]["payload"]),
             account_scope="Derived session ticket under the signed aggregate mandate; not a new approval.")
    # Ticket lifetime starts now, keeping the old v2 duration check compatible.
    # The browser verifies parent scope separately before consuming this ticket.
    t["created_at"] = now().isoformat()
    t.pop("credit_receiving", None)
    t["payment_authority"] = payment_authority(t)
    payload = canonical(t)
    operator = PrivateKey(bytes.fromhex(api.identity["secret_hex"]))
    child = {"terms": t, "invitation": {"version": 1, "payload": payload,
                "signature": operator.sign(payload.encode(), hasher=message_hash).hex()},
             "state": SPENDING_STATE, "receipt": copy.deepcopy(parent["receipt"]),
             "weekly_parent_id": p["budget_id"], "proxy_config_entry_id": parent["proxy_config_entry_id"],
             "session_key": parent["proxy_config_entry_id"] + ":" + session_id,
             "accepted_at": parent["accepted_at"], "created_by": "aggregate_mandate"}
    api.saved["session_budgets"][child_id] = child
    try:
        await api.store.async_save(api.saved)
    except BaseException:
        api.saved["session_budgets"].pop(child_id, None)
        raise
    return child


async def candidates(api, parent):
    """Read eligible account summaries; never issue a quote, reserve or pay."""
    proxy = api.hass.data.get(DOMAIN, {}).get(parent["proxy_config_entry_id"])
    if not proxy or (proxy.data or {}).get("issues") or not parent.get("credit_destination"):
        return []
    registered = datetime.fromisoformat(parent["credit_destination"]["registered_at"])
    expiry = datetime.fromisoformat(parent["terms"]["expires_at"])
    mapped={r["terms"]["session_id"]:api.collections.get(r) for r in children(api,parent)}
    records={r["session_id"]:r for r in api.ongoing_credits.records(proxy)}
    return [{"session_id": r["session_id"], "transaction_id": r["ocpp_transaction_id"],
             "ended_at": r.get("ended_at"), "net_cost_aud": r.get("net_cost_aud"),
             "state":(mapped.get(r["session_id"]) or {}).get("state","not_quoted")}
            for r in sorted(records.values(),key=lambda r:r["opened_at"])
            if registered <= datetime.fromisoformat(r["opened_at"]) < expiry
            and r.get("ended_at") and r.get("net_cost_aud") is not None and decimal(r["net_cost_aud"]) > 0]
