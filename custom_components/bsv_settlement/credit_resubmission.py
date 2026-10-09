"""Operator-approved resubmission of a stuck credit's exact signed bytes.

A credit stays ``broadcast_unknown`` when its submission outcome was uncertain
and the provider cannot show the transaction. The rule is never to re-sign or
replace it. Resubmitting the identical bytes is not a replacement: it has the
same transaction ID, so it cannot pay twice. If the network already has it,
the result is the same transaction.

This is allowed only after an administrator reviews an inspection. The
inspection must show three things. The provider has no record of the
transaction. The bytes still match the frozen recipient, amount and funding
outpoint. That outpoint is still unspent in the operator's provider-confirmed
UTXO set. Nothing is signed, and no other credit, recipient or amount is
touched.
"""
import copy

from bsv import Transaction
from bsv.script import P2PKH

from .api import WalletError
from .session_review import digest, now
from . import payment_timing

MAX_RESUBMISSIONS = 3


def _item(api, credit_id):
    item = api.saved["automatic_credits"].get(credit_id) if isinstance(credit_id, str) else None
    if item is None:
        raise WalletError("Credit not found")
    if item.get("state") != "broadcast_unknown" or not item.get("signed_raw") or not item.get("txid"):
        raise WalletError("Only a signed credit in broadcast_unknown can be resubmitted")
    return item


def _verify_bytes(item):
    tx = Transaction.from_hex(item["signed_raw"])
    if (tx is None or tx.txid() != item["txid"] or len(tx.inputs) != 1
            or tx.inputs[0].source_txid != item.get("source_txid")
            or tx.inputs[0].source_output_index != item.get("source_index")
            or not tx.outputs or tx.outputs[0].satoshis != item["amount_sats"]
            or tx.outputs[0].locking_script.hex() != P2PKH().lock(item["recipient_address"]).hex()):
        raise WalletError("Signed bytes do not match the frozen credit; do not resubmit")


async def inspect(api, credit_id):
    """Read-only review. Returns the evidence and a hash the broadcast must quote."""
    item = _item(api, credit_id)
    _verify_bytes(item)
    try:
        details = await api.chain.details(item["txid"])
        provider_has_transaction = isinstance(details, dict) and details.get("txid") == item["txid"]
    except WalletError:
        provider_has_transaction = False  # Not proof of failure; resubmitting identical bytes is harmless.
    unspent = await api.chain.unspent(api.identity["address"])
    outpoint = next((u for u in unspent
                     if u["tx_hash"] == item["source_txid"] and u["tx_pos"] == item["source_index"]), None)
    review = {
        "credit_id": credit_id, "txid": item["txid"], "amount_sats": item["amount_sats"],
        "fee_sats": item["fee_sats"], "recipient_address": item["recipient_address"],
        "funding_outpoint": {"txid": item["source_txid"], "index": item["source_index"],
                             "value_sats": outpoint["value"] if outpoint else None},
        "provider_has_transaction": provider_has_transaction,
        "funding_unspent_and_confirmed": outpoint is not None,
        "resubmissions": len(item.get("resubmissions", [])),
    }
    review["resubmission_allowed"] = (not provider_has_transaction and outpoint is not None
                                      and review["resubmissions"] < MAX_RESUBMISSIONS)
    review["review_hash"] = digest({k: review[k] for k in (
        "credit_id", "txid", "amount_sats", "fee_sats", "recipient_address", "funding_outpoint")})
    if provider_has_transaction:
        review["next_step"] = "The provider has this transaction; normal reconciliation will update it."
    elif outpoint is None:
        review["next_step"] = ("The funding output is spent or unconfirmed. Do not resubmit; "
                               "this credit needs separate review.")
    return review


async def resubmit(api, data, user_id):
    if not user_id or data.get("confirm_resubmit_identical_signed_bytes") is not True:
        raise WalletError("An administrator must confirm resubmitting the identical signed bytes")
    review = await inspect(api, data.get("credit_id"))
    if review["review_hash"] != data.get("expected_review_hash") or review["txid"] != data.get("expected_txid"):
        raise WalletError("The reviewed credit changed; inspect it again")
    if not review["resubmission_allowed"]:
        raise WalletError(review.get("next_step") or "Resubmission limit reached")
    item = api.saved["automatic_credits"][review["credit_id"]]
    entry = {"attempted_at": now().isoformat(), "by": user_id, "outcome": "attempting"}
    item.setdefault("resubmissions", []).append(entry)
    await api.auto_credits.save()  # Record intent before the network call.
    try:
        accepted = await api.chain.broadcast(item["signed_raw"]) == item["txid"]
    except WalletError:
        accepted = False
    if accepted:
        entry["outcome"] = "provider_accepted"
        item.update(state="submitted", error=None)
        payment_timing.stamp(item, "broadcast_acknowledged_at")
    else:
        entry["outcome"] = "uncertain"
        item["error"] = "Resubmission outcome uncertain. Reconcile this txid; never replace it."
    api.invalidate_balance("refresh_required_after_submission")
    await api.auto_credits.save()
    return review | {"outcome": entry["outcome"], "state": item["state"],
                     "resubmissions": copy.deepcopy(item["resubmissions"])}
