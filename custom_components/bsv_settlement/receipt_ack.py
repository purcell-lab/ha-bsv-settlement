"""Wallet-signed receipt reports, never payment or spending authority."""
import copy
import re

from bsv import PrivateKey, PublicKey

from .api import WalletError
from .budget import canonical, message_hash, sha
from .session_review import now

PROTOCOL = "ev credit receipt"


def payment_for(api, row, credit_id=None):
    """Resolve only a payment owned by this private receiving registration."""
    if credit_id is None:
        item = api.auto_credits.get(row)
    else:
        route = api.ongoing_credits.routes.get(credit_id) if isinstance(credit_id, str) else None
        if not route or route["recipient"]["budget_id"] != row["terms"]["budget_id"]:
            raise WalletError("Credit does not belong to this receiving registration")
        item = api.ongoing_credits.get(api.ongoing_credits.wrapper(route))
    if (not item or not item.get("txid") or not row.get("receipt")
            or item.get("recipient_address") != (row.get("credit_destination") or {}).get("address")):
        raise WalletError("No matching submitted credit for this receiving registration")
    return item


def acceptance_payload(api, row, item):
    return canonical({
        "action": "report_credit_receipt_accepted", "version": 1,
        "network": "BSV mainnet", "budget_id": row["terms"]["budget_id"],
        "credit_id": item["budget_id"], "session_id": item["session_id"],
        "transaction_id": item["transaction_id"], "txid": item["txid"],
        "output_index": 0, "amount_sats": item["amount_sats"],
        "recipient_address": item["recipient_address"],
        "driver_identity": row["receipt"]["driver_identity"],
        "operator_identity": api.identity["public_key"],
        "invitation_hash": sha(row["invitation"]["payload"]),
        "accepted": True,
    })


async def acknowledge(api, row, data):
    """Persist an authenticated wallet report, including after consent expires.

    A signature authenticates the reporting wallet, not its internal database.
    No provider call, broadcast, policy change or payment-state change occurs.
    """
    item = payment_for(api, row, data.get("credit_id"))
    expected = acceptance_payload(api, row, item)
    proof = data.get("acknowledgement")
    try:
        if (not isinstance(proof, dict) or proof.get("payload") != expected
                or not isinstance(proof.get("signature"), str)
                or not re.fullmatch(r"[0-9a-f]{16,144}", proof["signature"])):
            raise ValueError()
        key = PublicKey(bytes.fromhex(row["receipt"]["driver_identity"])).derive_child(
            PrivateKey(1), f"2-{PROTOCOL}-{row['terms']['budget_id']}")
        if not key.verify(bytes.fromhex(proof["signature"]), expected.encode(), hasher=message_hash):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise WalletError("Invalid wallet receipt acknowledgement") from None
    if item.get("wallet_receipt_ack"):
        if item["wallet_receipt_ack"]["payload"] != expected:
            raise WalletError("Stored receipt acknowledgement conflicts with this payment")
        return api.auto_credits.public(item)
    # The client only obtains a receipt after provider confirmation. Preserve
    # that prerequisite, without treating a historical report as chain finality.
    if item["state"] != "provider_confirmed":
        raise WalletError("Credit receipt acknowledgement awaits provider confirmation")
    before = copy.deepcopy(item)
    item["wallet_receipt_ack"] = {
        "payload": expected, "signature": proof["signature"], "reported_at": now().isoformat(),
    }
    try:
        await api.auto_credits.save()
    except Exception:
        item.clear()
        item.update(before)
        raise
    return api.auto_credits.public(item)
