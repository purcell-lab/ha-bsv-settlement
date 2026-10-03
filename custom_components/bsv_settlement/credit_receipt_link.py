"""Admin-only redisclosure of the original private link for a confirmed credit."""
import json
import re
import secrets

from bsv import PrivateKey, PublicKey

from .api import WalletError
from .budget import approval_payload, message_hash, sha
from .pairing import external_origin


def retrieve(api, data, user_id):
    """Read stored evidence only; never rotate capability or run settlement."""
    if not user_id or data.get("confirm_private_link_disclosure") is not True:
        raise WalletError("An administrator must confirm private link disclosure")
    credit_id = data.get("credit_id")
    item = api.saved["automatic_credits"].get(credit_id) if isinstance(credit_id, str) else None
    if (not item or item.get("state") != "provider_confirmed"
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("txid", "")))
            or type(item.get("confirmations")) is not int or item["confirmations"] < 1
            or item.get("txid") != data.get("expected_txid")
            or item.get("recipient_address") != data.get("expected_recipient_address")
            or item.get("budget_id") != credit_id):
        raise WalletError("Review the exact confirmed credit, transaction and original recipient")
    route = api.ongoing_credits.routes.get(credit_id)
    budget_id = route["recipient"]["budget_id"] if route else credit_id
    row = api.saved["session_budgets"].get(budget_id)
    if not row:
        raise WalletError("Original receiving registration is not retained")
    session_key = row["proxy_config_entry_id"] + "|" + item["session_id"]
    if api.saved["automatic_credit_index"].get(session_key) != credit_id:
        raise WalletError("This credit does not own the settled session")
    if (row["terms"].get("version") != 2
            or row["terms"].get("weekly_parent_hash")):
        raise WalletError("Only original single-session receipt links can be recovered; multi-session links need separate review")
    if route and (route["session_id"] != item["session_id"]
            or route["transaction_id"] != item["transaction_id"]
            or route["recipient"]["address"] != item["recipient_address"]
            or route["proxy_config_entry_id"] != row["proxy_config_entry_id"]):
        raise WalletError("Credit route does not match its original receiving registration")
    if not route and (api.collections.session_id(row) != item["session_id"]
            or (row.get("binding") or {}).get("transaction_id", row["terms"]["transaction_id"])
            != item["transaction_id"]):
        raise WalletError("Credit does not match its original session")
    try:
        terms, receipt, destination = row["terms"], row["receipt"], row["credit_destination"]
        operator = PublicKey(bytes.fromhex(api.identity["public_key"]))
        if (json.loads(row["invitation"]["payload"]) != terms
                or terms["operator_identity"] != api.identity["public_key"]
                or not operator.verify(bytes.fromhex(row["invitation"]["signature"]),
                    row["invitation"]["payload"].encode(), hasher=message_hash)
                or receipt["payload"] != approval_payload(row["invitation"], receipt["driver_identity"])):
            raise ValueError()
        verifier = PublicKey(bytes.fromhex(receipt["driver_identity"])).derive_child(
            PrivateKey(1), f"2-ev session spending-{budget_id}")
        if (not verifier.verify(bytes.fromhex(receipt["signature"]),
                receipt["payload"].encode(), hasher=message_hash)
                or destination["address"] != item["recipient_address"]
                or destination["address"] != api.auto_credits.destination(row).address()
                or destination["proof"]["payload"] != api.auto_credits.registration_payload(row)
                or not verifier.verify(bytes.fromhex(destination["proof"]["signature"]),
                    destination["proof"]["payload"].encode(), hasher=message_hash)
                or route and route["recipient"]["driver_identity"] != receipt["driver_identity"]):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise WalletError("Original receiving proof is missing or invalid") from None
    collection = api.collections.get(row)
    if collection and collection.get("state") not in ("provider_confirmed", "waived"):
        raise WalletError("Original page has an unsettled driver collection; review it separately")
    # Do not redisclose a live one-session mandate for unrelated credits if its
    # original account has no terminal evidence. Opening it could collect later.
    original_session = api.collections.session_id(row)
    if item["session_id"] != original_session and not collection:
        original_credit = api.auto_credits.get(row)
        closure = api.saved.get("closed_sessions", {}).get(api.collections.key(row))
        if not (original_credit and original_credit.get("state") == "provider_confirmed"
                or closure and closure.get("state") in ("waived", "closed_zero")):
            raise WalletError("Original session lacks terminal evidence; review it separately")
    if row.get("driver_link_scheme") != "hmac-sha256-v1":
        raise WalletError("The original private link cannot be safely recovered")
    token = api.budgets.link_token(budget_id)
    if not secrets.compare_digest(sha(token), row.get("driver_token_hash", "")):
        raise WalletError("The original private link cannot be safely recovered")
    try:
        origin = external_origin(api.hass)
    except WalletError:
        raise WalletError("Receipt link recovery requires a configured public HTTPS external URL") from None
    fragment = f"#budget={budget_id}&token={token}"
    return {
        "credit_id": credit_id, "budget_id": budget_id, "session_id": item["session_id"],
        "transaction_id": item["transaction_id"], "txid": item["txid"],
        "amount_sats": item["amount_sats"], "recipient_address": item["recipient_address"],
        "state": item["state"], "confirmations": item["confirmations"],
        "chain_checked_at": item.get("checked_at"), "chain_status_refreshed": False,
        "expires_at": row["terms"]["expires_at"],
        "driver_link_fragment": fragment,
        "driver_url": origin + "/bsv_settlement/driver/index.html" + fragment,
        "same_link": True, "payment_sent": False, "authority_changed": False,
        "privacy_notice": "Private original session capability. Share only with the original receiving driver; this is not a new receipt-only capability.",
    }
