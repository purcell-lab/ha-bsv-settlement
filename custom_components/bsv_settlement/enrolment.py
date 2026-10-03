"""Public, unassigned enrolment tokens never become private driver capabilities."""
import base64
import copy
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from .api import WalletError
from .budget import canonical, sha
from .session_review import now


def token(api, row):
    fields = {"purpose":"bsv-public-unassigned-enrolment-v1",
        "entry_id":api.entry.entry_id, "budget_id":row["terms"]["budget_id"],
        "invitation_hash":sha(row["invitation"]["payload"])}
    window = api.saved.get("public_registration_windows", {}).get(row["terms"]["budget_id"])
    if window:
        fields["window"] = window["nonce"]
    payload = canonical(fields)
    raw = hmac.new(bytes.fromhex(api.identity["secret_hex"]),payload.encode(),hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def no_driver(api, proxy_id):
    """Fail closed on registered recipients, even if a legacy spending limit expired."""
    for row in api.saved["session_budgets"].values():
        if row.get("weekly_parent_id") or row["proxy_config_entry_id"] != proxy_id:
            continue
        state = api.budgets.public(row)["state"]
        if state == "revoked":
            continue
        if row.get("credit_destination"):
            if row["terms"].get("version") == 3 and state == "expired":
                continue
            return False
        if row.get("receipt") and state != "expired":
            return False
    return True


def context_hash(api, row):
    """Detect any change of consent/recipient without changing historical authority."""
    records = {key: {k: other.get(k) for k in (
        "state", "receipt", "credit_destination", "accepted_at", "binding")}
        for key, other in api.saved["session_budgets"].items()
        if other["proxy_config_entry_id"] == row["proxy_config_entry_id"]
        and not other.get("weekly_parent_id")
        and key != row["terms"]["budget_id"]}
    return sha(canonical(records))


def window_open(api, row):
    window = api.saved.get("public_registration_windows", {}).get(row["terms"]["budget_id"])
    return bool(window and window.get("state") == "open"
        and now() < datetime.fromisoformat(window["expires_at"])
        and window["invitation_hash"] == sha(row["invitation"]["payload"])
        and window["context_hash"] == context_hash(api, row))


def admin_status(api, row):
    """Only an authenticated operator sees the review hash and enrolment controls."""
    return {"available": available(api) is row,
            "context_hash": context_hash(api, row),
            "expires_at": api.saved.get("public_registration_windows", {}).get(
                row["terms"]["budget_id"], {}).get("expires_at")}


async def manage(api, row, action, data, user_id):
    """Explicitly expose one new invitation; never revoke or settle old records."""
    if not user_id or data.get("confirm_public_registration") is not True:
        raise WalletError("An administrator must explicitly confirm public registration")
    if data.get("expected_invitation_hash") != sha(row["invitation"]["payload"]):
        raise WalletError("Invitation changed. Review it again")
    if action == "open_public_registration":
        if (row["terms"]["budget_id"] != api.saved.get("latest_session_budget")
                or row["terms"].get("version") != 3
                or row["terms"].get("session_mode") != "multi_session"
                or row.get("binding") or row.get("receipt") or row.get("credit_destination")
                or api.budgets.public(row)["state"] != "awaiting_driver_consent"):
            raise WalletError("Create a fresh unapproved multi-session invitation first")
        if data.get("expected_context_hash") != context_hash(api, row):
            raise WalletError("Driver registration changed. Review it again")
        if api.collections.get(row) or api.auto_credits.get(row):
            raise WalletError("An existing settlement owns this invitation")
        if window_open(api, row):
            return api.budgets.admin_status(row)  # Retries never extend the window.
        window = {"state": "open", "opened_by": user_id, "opened_at": now().isoformat(),
                  "expires_at": min(now() + timedelta(minutes=15),
                      datetime.fromisoformat(row["terms"]["expires_at"])).isoformat(),
                  "invitation_hash": data["expected_invitation_hash"],
                  "context_hash": data["expected_context_hash"],
                  "nonce": secrets.token_urlsafe(24)}
    else:
        if row.get("receipt") or row.get("credit_destination"):
            raise WalletError("Already approved. Closing registration cannot revoke consent")
        window = {"state": "closed", "closed_at": now().isoformat(),
                  "closed_by": user_id, "nonce": secrets.token_urlsafe(24)}
    previous = copy.deepcopy(api.saved.get("public_registration_windows", {}))
    api.saved.setdefault("public_registration_windows", {})[row["terms"]["budget_id"]] = window
    try:
        await api.store.async_save(api.saved)
    except BaseException:
        api.saved["public_registration_windows"] = previous
        raise
    return api.budgets.admin_status(row)


def available(api):
    row = api.saved["session_budgets"].get(api.saved.get("latest_session_budget"))
    if (not row or row.get("receipt") or row.get("credit_destination")
            or api.budgets.public(row)["state"] != "awaiting_driver_consent"
            or row["terms"].get("session_mode") not in ("next_session_reservation","multi_session")):
        return None
    if row["terms"]["budget_id"] in api.saved.get("public_registration_windows", {}):
        return row if window_open(api, row) else None
    if not no_driver(api, row["proxy_config_entry_id"]):
        return None
    return row


def public_view(api, row):
    # Deliberately exclude history, driver identities, balances and private token.
    return {"invitation":row["invitation"],"state":"awaiting_driver_consent",
            "prices":api.budgets.prices(row),"public_enrolment":True}


async def handle(api, data):
    action = data["action"]
    if action == "public_invitation":
        row = available(api)
        return ({"state":"available",
                 "public_link_fragment":f"#join={row['terms']['budget_id']}&key={token(api,row)}"}
                if row else {"state":"unavailable"})
    row = api.saved["session_budgets"].get(data.get("join"))
    key = data.get("key")
    if not row or not isinstance(key,str) or not secrets.compare_digest(key,token(api,row)):
        raise WalletError("This public invitation is unavailable")
    # A lost approval response can be recovered only with the exact driver-signed
    # receipt. Possessing the public QR alone never grants this recovery access.
    retry = (action == "public_approve" and row.get("receipt") is not None
             and data.get("receipt") == row["receipt"])
    if not retry and row is not available(api):
        raise WalletError("This public invitation is no longer available")
    if action == "public_read":
        return public_view(api,row)
    if action != "public_approve":
        raise WalletError("Unsupported public invitation action")
    if not retry and not api.budgets.prices(row)["valid"]:
        raise WalletError("Current buy and sell rates are unavailable or stale")
    await api.budgets.accept(row,data.get("receipt"),"public_enrolment")
    private_token = api.budgets.link_token(row["terms"]["budget_id"])
    if not secrets.compare_digest(sha(private_token),row["driver_token_hash"]):
        raise WalletError("Ask the operator to recover your private driver link")
    return api.budgets.driver_view(row) | {
        "private_link_fragment":f"#budget={row['terms']['budget_id']}&token={private_token}"}
