"""Bounded, local-only inspection of one retained collection.

Not provider verification, signature verification or permission to retry.
Never return scripts, signed bytes, keys, capability links or permits.
"""
import json
import re

from bsv import P2PKH, Transaction

from .api import WalletError
from .collection import transaction_shape
from .session_review import digest

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def inspect(collections, budget_id):
    row = collections.api.saved.get("session_budgets", {}).get(budget_id)
    if row is None:
        raise WalletError("Unknown spending approval")
    item = collections.get(row)
    if not item:
        raise WalletError("No driver collection attempt exists")
    # No call to status/current/reconcile: those can advance retained state.
    result = {
        "budget_id": budget_id,
        "session_id": collections.session_id(row),
        "read_only": True,
        "retry_authorised": False,
        "provider_checked": False,
        "signatures_verified": False,
        "fee_verified": False,
        "input_spend_status": "not_checked",
    }
    raw = item.get("signed_raw")
    if raw is None:
        return result | {"diagnostic": "signed_transaction_not_retained"}
    try:
        if (not isinstance(raw, str) or not 2 <= len(raw) <= 16000
                or len(raw) % 2 or not re.fullmatch(r"[0-9a-f]+", raw)):
            raise ValueError()
        tx = Transaction.from_hex(raw)
        if (not tx or tx.hex() != raw or not 1 <= len(tx.inputs) <= 4
                or not 1 <= len(tx.outputs) <= 2):
            raise ValueError()
        inputs = []
        for i in tx.inputs:
            if (not isinstance(i.source_txid, str) or not HEX64.fullmatch(i.source_txid)
                    or type(i.source_output_index) is not int
                    or not 0 <= i.source_output_index <= 0xffffffff):
                raise ValueError()
            inputs.append({"txid": i.source_txid, "output_index": i.source_output_index})
        if len({(i["txid"], i["output_index"]) for i in inputs}) != len(inputs):
            raise ValueError()
        amounts = [o.satoshis for o in tx.outputs]
        if any(type(v) is not int or not 0 < v <= 2100000000000000 for v in amounts):
            raise ValueError()
        actual_id = tx.txid()
    except Exception:
        # Parser exceptions can contain fragments of private transaction data.
        return result | {"diagnostic": "invalid_retained_transaction"}

    recorded_id = item.get("txid")
    id_valid = isinstance(recorded_id, str) and bool(HEX64.fullmatch(recorded_id))
    draft_match = digest(transaction_shape(tx)) == item.get("draft_hash")
    recipient_match = False
    try:
        quote = json.loads(item["quote"]["payload"])
        address = row["terms"]["operator_address"]
        script = P2PKH().lock(address).hex()
        matches = [n for n, o in enumerate(tx.outputs) if o.locking_script.hex() == script]
        recipient_match = (
            quote["recipient_address"] == address
            and type(quote["amount_sats"]) is int
            and len(matches) == 1
            and amounts[matches[0]] == quote["amount_sats"]
            and type(item.get("output_index")) is int
            and item["output_index"] == matches[0]
        )
    except Exception:
        pass
    id_match = id_valid and actual_id == recorded_id
    return result | {
        "diagnostic": "retained_transaction_matches" if (
            id_match and draft_match and recipient_match) else "retained_evidence_mismatch",
        "recorded_txid": recorded_id if id_valid else None,
        "computed_txid": actual_id,
        "txid_matches": id_match,
        "draft_matches": draft_match,
        "recipient_matches": recipient_match,
        "inputs": inputs,
        "outputs": [{"output_index": n, "amount_sats": v} for n, v in enumerate(amounts)],
    }
