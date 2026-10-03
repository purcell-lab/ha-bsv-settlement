"""Public, unassigned enrolment tokens never become private driver capabilities."""
import base64
import hashlib
import hmac
import secrets

from .api import WalletError
from .budget import canonical, sha


def token(api, row):
    payload = canonical({"purpose":"bsv-public-unassigned-enrolment-v1",
        "entry_id":api.entry.entry_id, "budget_id":row["terms"]["budget_id"],
        "invitation_hash":sha(row["invitation"]["payload"])})
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


def available(api):
    row = api.saved["session_budgets"].get(api.saved.get("latest_session_budget"))
    if (not row or row.get("receipt") or row.get("credit_destination")
            or api.budgets.public(row)["state"] != "awaiting_driver_consent"
            or row["terms"].get("session_mode") not in ("next_session_reservation","multi_session")
            or not no_driver(api,row["proxy_config_entry_id"])):
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
