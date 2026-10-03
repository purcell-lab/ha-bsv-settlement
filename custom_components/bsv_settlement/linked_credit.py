"""Explicit CPFP recovery only; never a general unconfirmed-funding policy."""
from decimal import Decimal, ROUND_CEILING

from bsv import P2PKH, Transaction
from bsv.script.spend import Spend

from .api import WalletError
from .auto_credit import MAX_TOTAL, PENDING
from .fees import MAX_SIGNED_BYTES, quote


def pending_except(api, parent_id=None):
    return any(key != parent_id and item["state"] in PENDING
               for key, item in api.saved["automatic_credits"].items())


async def evidence(api, parent_id, recipient):
    """Validate the exact local parent, confirmed ancestor and unspent change."""
    parent = api.saved["automatic_credits"].get(parent_id)
    if not parent or parent.get("state") not in PENDING or not parent.get("signed_raw"):
        raise WalletError("Linked recovery requires an existing signed pending credit")
    if parent.get("recipient_address") != recipient:
        raise WalletError("Linked credits must retain the same original recipient")
    try:
        raw = parent["signed_raw"]
        tx = Transaction.from_hex(raw)
        if (tx.hex() != raw or tx.txid() != parent["txid"]
                or len(raw) // 2 > MAX_SIGNED_BYTES
                or len(tx.inputs) != 1 or len(tx.outputs) != 2
                or tx.inputs[0].source_txid != parent["source_txid"]
                or tx.inputs[0].source_output_index != parent["source_index"]
                or tx.outputs[0].satoshis != parent["amount_sats"]
                or tx.outputs[0].locking_script.hex() != P2PKH().lock(recipient).hex()
                or tx.outputs[1].locking_script.hex() != P2PKH().lock(api.identity["address"]).hex()):
            raise ValueError()
    except Exception:
        raise WalletError("Linked parent transaction differs from its saved payment") from None
    details = await api.chain.details(tx.txid())
    provider_raw = await api.chain.request("GET", f"/tx/{tx.txid()}/hex", raw=True)
    if details.get("txid") != tx.txid() or provider_raw != raw:
        raise WalletError("Linked parent provider evidence differs from saved bytes")
    confirmations = details.get("confirmations")
    if "confirmations" not in details:
        if (any(k in details for k in ("blockhash", "blockheight", "blocktime"))
                or details.get("hash") != tx.txid()
                or type(details.get("version")) is not int or details["version"] != tx.version
                or type(details.get("locktime")) is not int or details["locktime"] != tx.locktime
                or type(details.get("size")) is not int or details["size"] != len(raw) // 2
                or not isinstance(details.get("vin"), list) or len(details["vin"]) != 1
                or not isinstance(details.get("vout"), list) or len(details["vout"]) != 2):
            raise WalletError("Linked parent confirmation evidence is incomplete")
    elif type(confirmations) is not int or confirmations != 0 or details.get("blockhash"):
        raise WalletError("Linked parent is no longer unconfirmed; prepare ordinary recovery")
    # Ordinary source() requires a confirmed, mature ancestor. This excludes
    # multi-generation unconfirmed packages and their unaccounted fees.
    ancestor_raw = await api.chain.source(parent["source_txid"])
    try:
        ancestor = Transaction.from_hex(ancestor_raw)
        if ancestor.txid() != parent["source_txid"]:
            raise ValueError()
        previous = ancestor.outputs[parent["source_index"]]
        if previous.locking_script.hex() != P2PKH().lock(api.identity["address"]).hex():
            raise ValueError()
        parent_fee = previous.satoshis - sum(o.satoshis for o in tx.outputs)
        if type(parent["fee_sats"]) is not int or parent_fee != parent["fee_sats"] or parent_fee < 1:
            raise ValueError()
        if not Spend({
            "sourceTXID": ancestor.txid(), "sourceOutputIndex": parent["source_index"],
            "sourceSatoshis": previous.satoshis, "lockingScript": previous.locking_script,
            "transactionVersion": tx.version, "otherInputs": [], "outputs": tx.outputs,
            "inputIndex": 0, "unlockingScript": tx.inputs[0].unlocking_script,
            "inputSequence": tx.inputs[0].sequence, "lockTime": tx.locktime,
        }).validate():
            raise ValueError()
    except Exception:
        raise WalletError("Linked parent fee, ownership or signature cannot be verified") from None
    used = api.auto_credits.used() | {
        (p["source_txid"], p["source_index"])
        for p in api.saved["payments"].values() if p.get("txid")}
    if (tx.txid(), 1) in used:
        raise WalletError("Linked parent change already has a locally signed spend")
    rows = await api.chain.unconfirmed_unspent(api.identity["address"])
    matches = [r for r in rows if (r["tx_hash"], r["tx_pos"]) == (tx.txid(), 1)]
    if len(matches) != 1 or matches[0]["value"] != tx.outputs[1].satoshis:
        raise WalletError("Linked parent change is unavailable or already spent")
    return {
        "credit_id": parent_id, "txid": tx.txid(), "output_index": 1,
        "amount_sats": parent["amount_sats"], "fee_sats": parent_fee,
        "size_bytes": len(raw) // 2, "change_sats": tx.outputs[1].satoshis,
        "recipient_address": recipient,
    }, matches[0], raw


async def linked_quote(api, parent, amount):
    quotation = await quote(api.chain)
    size = parent["size_bytes"] + MAX_SIGNED_BYTES
    required_total = int((Decimal(quotation["rate_sat_per_kb"]) * size / 1000)
                         .to_integral_value(rounding=ROUND_CEILING))
    fee = max(quotation["fee_sats"], required_total - parent["fee_sats"])
    total = parent["amount_sats"] + parent["fee_sats"] + amount + fee
    if total > MAX_TOTAL:
        raise WalletError("Combined credits plus both fees exceed the 1000 sat total cap")
    return quotation | {
        "fee_sats": fee, "linked_parent_txid": parent["txid"],
        "package_size_bound": size, "package_fee_sats": parent["fee_sats"] + fee,
        "package_total_sats": total,
    }
