"""Browser-held driver wallet collection. This server never signs driver inputs.

Persist a one-use signing permit BEFORE the browser calls signAction(noSend=True).
Persist signed bytes and a broadcast-unknown marker BEFORE server submission.
Lost responses, reloads and failures never release the attempt automatically.
"""
import copy
from datetime import datetime
from decimal import ROUND_HALF_UP
import re
import secrets
import json

from bsv import P2PKH, PrivateKey, PublicKey, Transaction
from .api import WalletError
from .budget import canonical, sha, message_hash, SPENDING_STATE, approval_payload
from .session_review import account_snapshot, decimal, digest, now
from .const import DOMAIN

HEX64 = re.compile(r"^[0-9a-f]{64}$")
P2PKH_HEX = re.compile(r"^76a914[0-9a-f]{40}88ac$")


def transaction_shape(tx):
    return {
        "version": tx.version, "locktime": tx.locktime,
        "inputs": [{"txid": i.source_txid, "index": i.source_output_index,
                    "sequence": i.sequence} for i in tx.inputs],
        "outputs": [{"script": o.locking_script.hex(), "satoshis": o.satoshis} for o in tx.outputs],
    }


class DriverCollections:
    def __init__(self, api):
        self.api = api
        api.saved.setdefault("driver_collections", {})
        api.saved.setdefault("driver_collection_index", {})

    async def save(self):
        await self.api.store.async_save(self.api.saved)

    def get(self, row):
        return self.api.saved["driver_collections"].get(row["terms"]["budget_id"])

    def public(self, item):
        return {k: copy.deepcopy(item[k]) for k in (
            "state", "quote", "txid", "output_index", "fee_sats", "confirmations",
            "created_at", "claimed_at", "submission_authorised_at", "checked_at", "error",
            "diagnostic", "recovery"
        ) if k in item}

    def mandate(self, row):
        if (row["terms"].get("version") != 2 or
                self.api.budgets.public(row)["state"] != SPENDING_STATE or not row.get("receipt")):
            raise WalletError("A current version 2 spending approval is required")
        if row["receipt"]["payload"] != approval_payload(row["invitation"], row["receipt"]["driver_identity"]):
            raise WalletError("The spending mandate does not match its signed terms")

    def session_id(self, row):
        return (row.get("binding") or {}).get("session_id") if row["terms"].get(
            "session_mode") == "next_session_reservation" else row["terms"]["session_id"]

    def key(self, row):
        return row["proxy_config_entry_id"] + "|" + self.session_id(row)

    async def source(self, row):
        proxy = self.api.hass.data.get(DOMAIN, {}).get(row["proxy_config_entry_id"])
        if proxy is None or proxy.mode != "sensor_proxy":
            raise WalletError("The original session recorder is unavailable")
        await proxy.async_request_refresh()
        if (proxy.data or {}).get("issues"):
            raise WalletError("Resolve recorder issues before collecting payment")
        data = proxy.data or {}
        record = next((r for r in [data.get("latest_session"), data.get("previous_session"), *proxy.archive]
                       if r and r["session_id"] == self.session_id(row)), None)
        if record is None:
            raise WalletError("The bound session is not in retained history")
        return record

    def manual_conflict(self, row):
        rid = self.api.saved.get("session_review_index", {}).get(self.key(row))
        if rid and self.api.saved["session_reviews"][rid]["state"] != "cancelled":
            raise WalletError("A manual payment review already owns this session")

    async def status(self, row):
        if hasattr(self.api, "auto_credits") and self.api.auto_credits.get(row):
            return self.api.auto_credits.status(row)
        old = self.get(row)
        if old and old["state"] != "ready":
            return self.public(old)
        try:
            self.mandate(row)
            if not self.session_id(row):
                return {"state": "waiting_for_operator_binding"}
            self.manual_conflict(row)
            if self.key(row) in self.api.saved.get("automatic_credit_index", {}):
                raise WalletError("Automatic credit already owns this session")
            record = await self.source(row)
            if not record.get("ended_at"):
                return {"state": "waiting_for_session_end"}
            account = account_snapshot(record)
            if datetime.fromisoformat(account["ended_at"]) > now():
                raise WalletError("Session end is in the future")
            if row.get("binding") and account["ocpp_transaction_id"] != row["binding"]["transaction_id"]:
                raise WalletError("Bound transaction ID changed")
            if old:
                if old["source_hash"] != digest(account):
                    raise WalletError("The frozen session account changed")
                return self.public(old)
            aud = decimal(account["net_amount_aud"])
            if aud < 0:
                if hasattr(self.api, "auto_credits") and self.api.auto_credits.policy.get("enabled"):
                    return self.api.auto_credits.status(row)
                return {"state": "operator_credit_review_required", "net_amount_aud": str(aud)}
            if aud == 0:
                return {"state": "no_payment_due"}
            terms = row["terms"]
            amount = int((aud * decimal(terms["satoshis_per_aud"])).quantize(
                DecimalOne, rounding=ROUND_HALF_UP))
            if amount < 1 or amount > terms["max_total_sats"]:
                raise WalletError("The final account exceeds the signed total limit or rounds below one satoshi")
            if amount == terms["max_total_sats"] and terms["max_fee_sats"] > 0:
                raise WalletError("The final account leaves no room within the signed total for a network fee")
            fee_cap = min(terms["max_fee_sats"], terms["max_total_sats"] - amount)
            owner = self.api.saved["driver_collection_index"].get(self.key(row))
            if owner and owner != terms["budget_id"]:
                raise WalletError("Another spending approval already owns this session")
            q = {
                "version": 1, "budget_id": terms["budget_id"], "network": "BSV mainnet",
                "invitation_hash": sha(row["invitation"]["payload"]),
                "driver_identity": row["receipt"]["driver_identity"],
                "recipient_address": terms["operator_address"],
                "operator_identity": terms["operator_identity"],
                "amount_sats": amount, "max_fee_sats": fee_cap,
                "max_total_sats": terms["max_total_sats"],
                "satoshis_per_aud": terms["satoshis_per_aud"],
                "expires_at": terms["expires_at"], "account": account,
                "created_at": now().isoformat(),
            }
            payload = canonical(q)
            operator = PrivateKey(bytes.fromhex(self.api.identity["secret_hex"]))
            item = {"state": "ready", "created_at": q["created_at"], "source_hash": digest(account),
                    "quote": {"payload": payload, "hash": sha(payload),
                              "signature": operator.sign(payload.encode(), hasher=message_hash).hex()}}
            self.api.saved["driver_collections"][terms["budget_id"]] = item
            self.api.saved["driver_collection_index"][self.key(row)] = terms["budget_id"]
            await self.save()
            return self.public(item)
        except WalletError as exc:
            return {"state": "collection_blocked", "error": str(exc)}

    async def current(self, row, item):
        self.mandate(row)
        self.manual_conflict(row)
        account = account_snapshot(await self.source(row))
        if digest(account) != item["source_hash"]:
            raise WalletError("Session account changed after the quote was frozen")
        self.mandate(row)  # Network/recorder awaits can cross expiry.

    async def claim(self, row, data):
        state = await self.status(row)
        item = self.get(row)
        if not item or state["state"] not in ("ready", "recovery_ready"):
            raise WalletError("Collection is not ready or already has an attempt; do not retry payment")
        if state["state"] == "recovery_ready" and data.get("confirm_recovered_attempt") is not True:
            raise WalletError("The driver must explicitly confirm the reviewed recovery")
        token = data.get("attempt_token")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            raise WalletError("Invalid collection attempt")
        if any(r.get("budget_id") == row["terms"]["budget_id"]
               and r.get("previous_attempt_hash") == sha(token)
               for r in self.api.saved.get("collection_recoveries", [])):
            raise WalletError("The previous attempt token was retired; use a fresh driver confirmation")
        payload = canonical({
            "version": 1, "action": "claim_session_collection",
            "budget_id": row["terms"]["budget_id"], "quote_hash": item["quote"]["hash"],
            "attempt_token_hash": sha(token), "driver_identity": row["receipt"]["driver_identity"],
        })
        try:
            proof = data["proof"]
            if set(proof) != {"payload", "signature"} or proof["payload"] != payload:
                raise ValueError()
            child = PublicKey(bytes.fromhex(row["receipt"]["driver_identity"])).derive_child(
                PrivateKey(1), f"2-ev session spending-{row['terms']['budget_id']}")
            sig = bytes.fromhex(proof["signature"])
            if not 8 <= len(sig) <= 72 or not child.verify(sig, payload.encode(), hasher=message_hash):
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise WalletError("Collection must be claimed by the approved driver wallet") from None
        await self.current(row, item)
        item.update(state="wallet_attempt_reserved", attempt_token_hash=sha(token), claimed_at=now().isoformat())
        await self.save()  # A failure here NEVER grants permission to call createAction.
        return self.public(item) | {"claimed": True}

    def attempt(self, row, data):
        item = self.get(row)
        token = data.get("attempt_token")
        if (not item or not isinstance(token, str) or len(token) != 43 or
                not secrets.compare_digest(item.get("attempt_token_hash", ""), sha(token))):
            raise WalletError("Invalid collection attempt")
        return item

    async def validate_draft(self, row, item, draft):
        q = json.loads(item["quote"]["payload"])
        if not isinstance(draft, dict) or set(draft) != {"inputs", "outputs", "version", "locktime"}:
            raise WalletError("Invalid wallet transaction shape")
        if draft["version"] != 1 or draft["locktime"] != 0:
            raise WalletError("Only immediate version 1 transactions are supported")
        ins, outs = draft["inputs"], draft["outputs"]
        if not isinstance(ins, list) or not 1 <= len(ins) <= 4 or not isinstance(outs, list) or not 1 <= len(outs) <= 2:
            raise WalletError("This demonstration supports at most four inputs and one change output")
        seen, value = set(), 0
        for i in ins:
            if (not isinstance(i, dict) or set(i) != {"txid", "index", "sequence"} or
                    not isinstance(i["txid"], str) or not HEX64.fullmatch(i["txid"]) or
                    type(i["index"]) is not int or i["index"] < 0 or i["sequence"] != 0xFFFFFFFF):
                raise WalletError("Invalid funding input")
            point = (i["txid"], i["index"])
            if point in seen:
                raise WalletError("Duplicate funding input")
            seen.add(point)
            raw = await self.api.chain.source(i["txid"])  # Requires provider-confirmed parents.
            source = Transaction.from_hex(raw)
            if not source or source.txid() != i["txid"] or i["index"] >= len(source.outputs):
                raise WalletError("Funding evidence does not match the wallet input")
            value += source.outputs[i["index"]].satoshis
        wanted = P2PKH().lock(q["recipient_address"]).hex()
        matches = []
        for n, o in enumerate(outs):
            if (not isinstance(o, dict) or set(o) != {"script", "satoshis"} or
                    not isinstance(o["script"], str) or not P2PKH_HEX.fullmatch(o["script"]) or
                    type(o["satoshis"]) is not int or not 0 < o["satoshis"] <= 2100000000000000):
                raise WalletError("Only positive P2PKH payment and wallet-change outputs are supported")
            if o["script"] == wanted:
                matches.append(n)
        if len(matches) != 1 or outs[matches[0]]["satoshis"] != q["amount_sats"]:
            raise WalletError("The wallet transaction does not pay the exact operator amount")
        fee = value - sum(o["satoshis"] for o in outs)
        if fee < 0 or fee > q["max_fee_sats"] or fee + q["amount_sats"] > q["max_total_sats"]:
            raise WalletError("Network fee or total wallet debit exceeds the signed approval")
        return fee, matches[0]

    async def authorise(self, row, data):
        item = self.attempt(row, data)
        if item["state"] != "wallet_attempt_reserved":
            raise WalletError("A submission permit was already issued; reconcile, never submit again")
        await self.current(row, item)
        fee, index = await self.validate_draft(row, item, data.get("draft"))
        await self.current(row, item)
        item.update(state="submission_authorised", draft_hash=digest(data["draft"]),
                    fee_sats=fee, output_index=index, submission_authorised_at=now().isoformat())
        await self.save()  # One-use permit; even an uncertain response is never reissued.
        return self.public(item) | {"submit_once": True, "draft_hash": item["draft_hash"]}

    async def report(self, row, data):
        item = self.attempt(row, data)
        if item["state"] not in ("submission_authorised", "broadcast_unknown", "submitted", "provider_unconfirmed", "provider_confirmed"):
            raise WalletError("No submission permit exists")
        raw = data.get("raw_tx")
        try:
            if not isinstance(raw, str) or len(raw) > 16000 or not re.fullmatch(r"[0-9a-f]+", raw):
                raise ValueError()
            tx = Transaction.from_hex(raw)
            if not tx or tx.hex() != raw or digest(transaction_shape(tx)) != item["draft_hash"]:
                raise ValueError()
            if any(not i.unlocking_script or not i.unlocking_script.hex() for i in tx.inputs):
                raise ValueError()
        except Exception:
            raise WalletError("Reported transaction differs from the authorised unsigned draft") from None
        txid = tx.txid()
        if item.get("txid"):
            if item["txid"] != txid:
                raise WalletError("A different payment is already recorded; reconcile before any other action")
            return await self.reconcile(row)  # Lost response: check only, NEVER broadcast twice.
        await self.current(row, item)
        if self.api.entry.data.get("enable_broadcast") is not True:
            raise WalletError("Mainnet broadcast is disabled")
        item.update(txid=txid, state="broadcast_unknown", signed_raw=raw)
        await self.save()
        try:
            returned = await self.api.chain.broadcast(raw)
            if returned != txid:
                raise WalletError("Provider returned a different transaction ID")
            item["state"] = "submitted"
        except WalletError as exc:
            item["error"] = "Submission outcome uncertain: " + str(exc)
        await self.save()
        return await self.reconcile(row)

    async def reconcile(self, row):
        item = self.get(row)
        if not item or not item.get("txid"):
            return self.public(item) if item else {"state": "no_payment_attempt"}
        try:
            raw = await self.api.chain.request("GET", f"/tx/{item['txid']}/hex", raw=True)
            tx = Transaction.from_hex(raw)
            if not tx or tx.txid() != item["txid"] or digest(transaction_shape(tx)) != item["draft_hash"]:
                raise WalletError("Chain evidence differs from the authorised payment")
            details = await self.api.chain.details(item["txid"])
            confirmations = details.get("confirmations")
            if type(confirmations) is not int or confirmations < 0:
                raise WalletError("Invalid provider confirmation evidence")
            point = f"{item['txid']}:{item['output_index']}"
            owner = self.api.saved["received_outpoints"].get(point)
            own = "collection:" + row["terms"]["budget_id"]
            if owner and owner != own:
                raise WalletError("Payment output already belongs to another session")
            self.api.saved["received_outpoints"][point] = own
            item.update(state="provider_confirmed" if confirmations else "provider_unconfirmed",
                        confirmations=confirmations, checked_at=now().isoformat())
            item.pop("error", None)
        except WalletError as exc:
            item.update(error=str(exc), checked_at=now().isoformat())
        await self.save()
        return self.public(item)


DecimalOne = decimal("1")
