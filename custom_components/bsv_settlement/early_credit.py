"""Opt-in delivery of an existing unconfirmed credit, never new payment authority."""
import copy
from datetime import datetime
import hashlib
import re

from bsv import P2PKH, Transaction
from .api import WalletError
from .session_review import now

MODE = "unconfirmed_ancestor_proven_v1"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_RAW_HEX = 200000


class Unavailable(WalletError):
    """Safe pre-wallet deferral to the established confirmed-receipt path."""


def enabled(api):
    return policy(api).get("enabled") is True


def policy(api):
    return api.saved.get("automatic_credit_policy", {}).get("early_delivery", {})


async def configure(api, value, user_id):
    if not user_id or type(value) is not bool:
        raise WalletError("Administrator approval required for experimental receipt delivery")
    container = api.saved["automatic_credit_policy"]
    previous = copy.deepcopy(container.get("early_delivery"))
    setting = container.setdefault("early_delivery", {"enabled": False})
    if value and not setting.get("enabled"):
        setting.update(enabled=True, enabled_at=now().isoformat(), authorised_by=user_id)
    elif not value:
        setting["enabled"] = False
    try:
        await api.store.async_save(api.saved)
    except Exception:
        if previous is None:
            container.pop("early_delivery", None)
        else:
            container["early_delivery"] = previous
        raise
    return {"enabled": enabled(api), "mode": MODE, "enabled_at": setting.get("enabled_at")}


def eligible(api, item):
    if (not enabled(api) or item.get("state") not in ("submitted", "provider_unconfirmed")
            or item.get("wallet_receipt_ack") or item.get("provider_first_confirmed_at")):
        return False
    try:
        # New acknowledged submissions only. Never adopt old/uncertain payments.
        return datetime.fromisoformat(item["broadcast_acknowledged_at"]) >= datetime.fromisoformat(
            policy(api)["enabled_at"])
    except (KeyError, TypeError, ValueError):
        return False


def validate_proof(txid, proof, block):
    if (not isinstance(proof, dict) or not isinstance(block, dict)
            or proof.get("txOrId") != txid or not HEX64.fullmatch(str(proof.get("target", "")))
            or proof["target"] != block.get("hash")
            or type(proof.get("index")) is not int or proof["index"] < 0
            or type(block.get("height")) is not int or block["height"] < 1
            or not HEX64.fullmatch(str(block.get("merkleroot", "")))
            or not isinstance(proof.get("nodes"), list) or len(proof["nodes"]) > 40
            or proof["index"] >= 2 ** len(proof["nodes"])):
        raise Unavailable("Confirmed funding proof unavailable")
    value, index = bytes.fromhex(txid)[::-1], proof["index"]
    for sibling in proof["nodes"]:
        if sibling == "*":
            other = value
        elif isinstance(sibling, str) and HEX64.fullmatch(sibling):
            other = bytes.fromhex(sibling)[::-1]
        else:
            raise Unavailable("Invalid funding proof node")
        pair = other + value if index % 2 else value + other
        value = hashlib.sha256(hashlib.sha256(pair).digest()).digest()
        index //= 2
    if value[::-1].hex() != block["merkleroot"]:
        raise Unavailable("Funding Merkle root mismatch")


async def envelope(api, item):
    """Only a bounded single-confirmed-parent envelope is supported in this trial."""
    if not eligible(api, item) or item["state"] != "provider_unconfirmed":
        raise Unavailable("Early receipt not eligible")
    try:
        raw = item["signed_raw"]
        if not isinstance(raw, str) or len(raw) > MAX_RAW_HEX:
            raise Unavailable("Credit transaction too large")
        tx = Transaction.from_hex(raw)
        if tx.txid() != item["txid"] or len(tx.inputs) != 1 or not tx.inputs[0].unlocking_script:
            raise Unavailable("Unsupported credit shape")
        parent_id, index = tx.inputs[0].source_txid, tx.inputs[0].source_output_index
        if parent_id != item["source_txid"] or index != item["source_index"]:
            raise Unavailable("Funding reference changed")
        # source() checks confirmed funding and coinbase maturity, not unspentness:
        # this parent output has already been spent by this exact credit.
        parent_raw = await api.chain.source(parent_id)
        if not isinstance(parent_raw, str) or len(parent_raw) > MAX_RAW_HEX:
            raise Unavailable("Funding transaction too large")
        parent = Transaction.from_hex(parent_raw)
        if parent.txid() != parent_id or type(index) is not int or not 0 <= index < len(parent.outputs):
            raise Unavailable("Funding transaction mismatch")
        source = parent.outputs[index]
        if (source.locking_script.hex() != P2PKH().lock(api.identity["address"]).hex()
                or tx.outputs[0].satoshis != item["amount_sats"]
                or tx.outputs[0].locking_script.hex() != P2PKH().lock(item["recipient_address"]).hex()
                or source.satoshis - sum(o.satoshis for o in tx.outputs) != item["fee_sats"]
                or item["amount_sats"] + item["fee_sats"] > 1000):
            raise Unavailable("Funding or credit output mismatch")
        proofs = await api.chain.request("GET", f"/tx/{parent_id}/proof/tsc")
        if not isinstance(proofs, list) or len(proofs) != 1:
            raise Unavailable("Funding proof unavailable")
        proof = proofs[0]
        if not isinstance(proof, dict) or not HEX64.fullmatch(str(proof.get("target", ""))):
            raise Unavailable("Invalid funding block reference")
        block = await api.chain.request("GET", f"/block/hash/{proof['target']}")
        validate_proof(parent_id, proof, block)
        result = {"raw_tx": raw, "delivery_stage": MODE, "ancestry": [{
            "raw_tx": parent_raw, "txid": parent_id, "proof": proof,
            "block": {k: block[k] for k in ("hash", "height", "merkleroot")},
        }]}
        # Persist only an offer marker, not duplicate raw ancestry/private material.
        previous = item.get("early_receipt_offer")
        item["early_receipt_offer"] = previous or {"txid": item["txid"], "offered_at": now().isoformat()}
        try:
            await api.store.async_save(api.saved)
        except Exception:
            if previous is None:
                item.pop("early_receipt_offer", None)
            raise
        return result
    except Unavailable:
        raise
    except (WalletError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise Unavailable("Confirmed funding ancestry unavailable; wait for credit confirmation") from None
