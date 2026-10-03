"""Bounded, server-side operator credits. Never retry a signed submission."""
import copy
import json
from datetime import datetime
from decimal import ROUND_HALF_UP

from bsv import PrivateKey, PublicKey, Transaction
from .api import WalletError
from .budget import canonical, sha, message_hash
from .session_review import account_snapshot, decimal, digest, now
from .mainnet import build_transaction, MIN_CHANGE_SATS, TXID
from .fees import quote, validate, MODE

MAX_TOTAL = 1000
PROTOCOL = [2, "3241645161d8"]
PENDING = ("broadcast_unknown", "submitted", "provider_unconfirmed")
CONFIRMED_RECHECK_SECONDS = 15 * 60


class AutomaticCredits:
    def __init__(self, api):
        self.api = api
        api.saved.setdefault("automatic_credit_policy", {"enabled": False})
        api.saved.setdefault("automatic_credits", {})
        api.saved.setdefault("automatic_credit_index", {})

    @property
    def policy(self):
        return self.api.saved["automatic_credit_policy"]

    async def save(self):
        await self.api.store.async_save(self.api.saved)

    async def configure(self, enabled, user_id):
        if not user_id or type(enabled) is not bool:
            raise WalletError("An administrator must set the automatic-credit policy")
        if enabled and not self.policy.get("enabled"):
            self.api.saved["automatic_credit_policy"] = {
                "enabled": True, "enabled_at": now().isoformat(), "authorised_by": user_id,
                "max_total_sats": MAX_TOTAL, "fee_mode": MODE,
            }
        elif not enabled:
            self.policy["enabled"] = False
        await self.save()
        return self.summary()

    def get(self, row):
        return self.api.saved["automatic_credits"].get(row["terms"]["budget_id"])

    def public(self, item):
        acknowledgement = item.get("wallet_receipt_ack")
        return {k: copy.deepcopy(item[k]) for k in (
            "state", "budget_id", "session_id", "transaction_id", "recipient_address",
            "amount_sats", "fee_sats", "net_amount_aud", "txid", "confirmations",
            "created_at", "checked_at", "error", "fee_quote",
        ) if k in item} | {"quality_flags": copy.deepcopy(
            (item.get("account") or {}).get("quality_flags", [])),
            "wallet_receipt_status": "wallet_reported_accepted" if acknowledgement else "not_recorded",
            "wallet_imported_at": acknowledgement["reported_at"] if acknowledgement else None}

    def summary(self):
        return {
            "enabled": self.policy.get("enabled", False),
            "max_total_sats": MAX_TOTAL, "fee_sats": None, "fee_mode": MODE,
            "enabled_at": self.policy.get("enabled_at"),
            "payments": [self.public(i) for i in
                         list(self.api.saved["automatic_credits"].values())[-20:]],
        }

    def guard(self, row):
        if row["terms"].get("closed_session_review"):
            raise WalletError("Post-session payment consent does not register or authorise operator credits")
        if (not self.policy.get("enabled") or
                self.api.entry.data.get("enable_broadcast") is not True):
            raise WalletError("Automatic operator credits are disabled")
        self.api.collections.mandate(row)
        if (json.loads(row["invitation"]["payload"]) != row["terms"] or
                row["terms"]["operator_identity"] != self.api.identity["public_key"]):
            raise WalletError("Signed credit terms or operator identity changed")
        if datetime.fromisoformat(row["terms"]["created_at"]) < datetime.fromisoformat(self.policy["enabled_at"]):
            raise WalletError("Create a new invitation after enabling automatic credits")
        if "credit_receiving" not in row["terms"]:
            raise WalletError("A new invitation with wallet receiving terms is required")

    def destination(self, row):
        """Derive the driver's BRC-29 receiving key from both wallet identities."""
        terms = row["terms"]
        remit = terms["credit_receiving"]
        key_id = remit["derivationPrefix"] + " " + remit["derivationSuffix"]
        operator = PrivateKey(bytes.fromhex(self.api.identity["secret_hex"]))
        driver = PublicKey(bytes.fromhex(row["receipt"]["driver_identity"]))
        child = driver.derive_child(operator, f"2-{PROTOCOL[1]}-{key_id}")
        return child

    def registration_payload(self, row):
        child = self.destination(row)
        return canonical({
            "action": "register_session_credit_destination", "version": 1,
            "budget_id": row["terms"]["budget_id"],
            "invitation_hash": sha(row["invitation"]["payload"]),
            "driver_identity": row["receipt"]["driver_identity"],
            "public_key": child.hex(), "address": child.address(),
        })

    async def register(self, row, data):
        self.guard(row)
        expected = self.registration_payload(row)
        try:
            proof = data["proof"]
            key = PublicKey(bytes.fromhex(row["receipt"]["driver_identity"])).derive_child(
                PrivateKey(1), f"2-ev session spending-{row['terms']['budget_id']}")
            signature = bytes.fromhex(proof["signature"])
            if (proof["payload"] != expected or not 8 <= len(signature) <= 72 or
                    not key.verify(signature, expected.encode(), hasher=message_hash)):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise WalletError("Invalid driver receiving-key proof") from None
        if row.get("credit_destination"):
            if row["credit_destination"]["proof"]["payload"] != expected:
                raise WalletError("Receiving destination changed")
            return {"state": "credit_destination_registered"}
        if self.api.collections.session_id(row):
            record = await self.api.collections.source(row)
            if record.get("ended_at"):
                raise WalletError("Register the receiving wallet before the session ends")
        row["credit_destination"] = {
            "address": self.destination(row).address(), "proof": copy.deepcopy(proof),
            "registered_at": now().isoformat(),
        }
        await self.save()
        return {"state": "credit_destination_registered"}

    def conflict(self, row):
        key = self.api.collections.key(row)
        self.api.collections.manual_conflict(row)
        if key in self.api.saved["driver_collection_index"]:
            raise WalletError("A driver collection already owns this session")
        owner = self.api.saved["automatic_credit_index"].get(key)
        if owner and owner != row["terms"]["budget_id"]:
            raise WalletError("Another credit already owns this session")

    async def account(self, row):
        self.guard(row)
        self.conflict(row)
        destination = row.get("credit_destination")
        if not destination or destination["address"] != self.destination(row).address():
            raise WalletError("Driver receiving wallet must be registered before session end")
        record = await self.api.collections.source(row)
        result = account_snapshot(record)
        ended = datetime.fromisoformat(result["ended_at"])
        if (ended > now() or ended < datetime.fromisoformat(destination["registered_at"])
                or ended > datetime.fromisoformat(row["terms"]["expires_at"])):
            raise WalletError("Session end is outside the authorised credit window")
        expected = (row.get("binding") or {}).get("transaction_id", row["terms"]["transaction_id"])
        if result["ocpp_transaction_id"] != expected:
            raise WalletError("Bound transaction ID changed")
        return result

    def pending(self):
        return any(i["state"] in PENDING for i in self.api.saved["automatic_credits"].values())

    def blocking_pending(self, row):
        return self.pending()

    def used(self):
        return {(i["source_txid"], i["source_index"]) for i in
                self.api.saved["automatic_credits"].values() if i.get("txid")}

    async def quote_fee(self, row, amount):
        result = await quote(self.api.chain)
        if amount + result["fee_sats"] > MAX_TOTAL:
            raise WalletError("Credit plus quoted fee exceeds the 1000 sat total cap")
        return result

    async def funding(self, row, amount, fee):
        used = self.used() | {(p["source_txid"], p["source_index"])
                              for p in self.api.saved["payments"].values() if p.get("txid")}
        rows = await self.api.chain.unspent(self.api.identity["address"])
        candidates = [r for r in rows if r["value"] >= amount + fee + MIN_CHANGE_SATS
                      and (r["tx_hash"], r["tx_pos"]) not in used]
        if not candidates:
            raise WalletError("No suitable confirmed operator funding output")
        source = min(candidates, key=lambda r: r["value"])
        return source, await self.api.chain.source(source["tx_hash"])

    async def process(self, row):
        item = self.get(row)
        if item and item.get("txid"):
            await self.reconcile(item)
            return
        account = await self.account(row)
        aud = decimal(account["net_amount_aud"])
        if aud >= 0:
            return
        amount = int((-aud * decimal(row["terms"]["satoshis_per_aud"])).quantize(
            decimal("1"), rounding=ROUND_HALF_UP))
        if amount < 1 or amount >= MAX_TOTAL:
            raise WalletError("Credit plus fee exceeds 1000 sat or rounds below one satoshi")
        if item and item["source_hash"] != digest(account):
            raise WalletError("Frozen credit account changed; reconcile, do not replace it")
        quotation = await self.quote_fee(row, amount)
        fee = quotation["fee_sats"]
        if item is None:
            item = {
                "state": "credit_queued", "budget_id": row["terms"]["budget_id"],
                "session_id": account["session_id"], "transaction_id": account["ocpp_transaction_id"],
                "recipient_address": row["credit_destination"]["address"],
                "amount_sats": amount, "fee_sats": fee, "net_amount_aud": str(aud),
                "account": copy.deepcopy(account),
                "source_hash": digest(account), "created_at": now().isoformat(),
                "policy_enabled_at": self.policy["enabled_at"],
            }
            self.api.saved["automatic_credits"][row["terms"]["budget_id"]] = item
            self.api.saved["automatic_credit_index"][self.api.collections.key(row)] = row["terms"]["budget_id"]
        # Only unsigned automatic work is repriced. Reviewed recovery overrides
        # quote_fee and keeps its exact fee; signed outcomes returned above.
        item.update(fee_sats=fee, fee_quote=quotation)
        await self.save()
        manual = self.api.saved["payments"]
        if self.blocking_pending(row) or any(p["state"] in ("prepared", *PENDING) for p in manual.values()):
            raise WalletError("Another operator payment is unresolved; credit remains queued")
        source, raw = await self.funding(row, amount, fee)
        if digest(await self.account(row)) != item["source_hash"]:
            raise WalletError("Session account changed before signing")
        fresh = await self.quote_fee(row, amount)
        validate(fresh, fee)  # A rise fails unsigned; next tick can obtain new terms.
        signed = await self.api.hass.async_add_executor_job(
            build_transaction, self.api.identity["secret_hex"], raw, source["tx_pos"],
            item["recipient_address"], amount, fee, True)
        if signed["source_txid"] != source["tx_hash"] or signed["source_value"] != source["value"]:
            raise WalletError("Funding evidence does not match the raw transaction")
        self.guard(row)
        if digest(await self.account(row)) != item["source_hash"]:
            raise WalletError("Session account changed after signing; no submission")
        validate(fresh, fee, len(signed["raw"]) // 2)
        item["fee_quote"] = fresh
        item.update(signed_raw=signed["raw"], txid=signed["txid"], state="broadcast_unknown",
                    source_txid=source["tx_hash"], source_index=source["tx_pos"], error=None)
        self.api.invalidate_balance("refresh_required_after_submission")
        await self.save()  # Signed bytes and input reservation MUST precede network submission.
        try:
            if await self.api.chain.broadcast(signed["raw"]) != signed["txid"]:
                raise WalletError("Unexpected broadcast response")
            item["state"] = "submitted"
        except WalletError:
            item["error"] = "Submission outcome uncertain. Reconcile this txid; never replace it."
        await self.save()
        await self.api.refresh_balance_if_due()

    async def reconcile(self, item, *, force=False):
        if item["state"] not in (*PENDING, "provider_confirmed"):
            return
        if item["state"] == "provider_confirmed" and not force:
            try:
                age = (now() - datetime.fromisoformat(item["checked_at"])).total_seconds()
                if 0 <= age < CONFIRMED_RECHECK_SECONDS:
                    return
            except (KeyError, TypeError, ValueError):
                pass  # Old or invalid timestamps require fresh evidence, not assumed finality.
        previous = item["state"]
        try:
            details = await self.api.chain.details(item["txid"])
            raw = await self.api.chain.request("GET", f"/tx/{item['txid']}/hex", raw=True)
            if (not isinstance(details, dict) or details.get("txid") != item["txid"]
                    or raw != item["signed_raw"]):
                raise WalletError("Provider credit evidence does not match the signed transaction")
            try:
                tx = Transaction.from_hex(raw)
                matches = tx.txid() == item["txid"]
            except Exception:
                matches = False
            if not matches:
                raise WalletError("Provider credit transaction is invalid")
            confirmations = details.get("confirmations")
            if "confirmations" not in details:
                # WoC omits confirmation/block fields for an unmined transaction.
                # Recognise only its complete shape, after exact signed-byte and
                # transaction-ID verification. Missing metadata is not evidence
                # of zero confirmations; explicit null/invalid counts stay errors.
                if (any(k in details for k in ("blockhash", "blockheight", "blocktime"))
                        or details.get("hash") != item["txid"]
                        or type(details.get("version")) is not int or details["version"] != tx.version
                        or type(details.get("locktime")) is not int or details["locktime"] != tx.locktime
                        or type(details.get("size")) is not int or details["size"] != len(raw) // 2
                        or not isinstance(details.get("vin"), list) or len(details["vin"]) != len(tx.inputs)
                        or not isinstance(details.get("vout"), list) or len(details["vout"]) != len(tx.outputs)):
                    raise WalletError("Invalid confirmation evidence")
                confirmations = 0
            if type(confirmations) is not int or confirmations < 0:
                raise WalletError("Invalid confirmation evidence")
        except WalletError:
            item.update(state="broadcast_unknown", confirmations=None,
                        error="Credit evidence unavailable or inconsistent. Reconcile this txid; never replace it.")
            item.pop("receipt", None)
            self.api.invalidate_balance("credit_evidence_requires_reconciliation")
            await self.save()
            raise WalletError(item["error"]) from None
        # A proof cached for a previous block must never survive reassessment.
        item.pop("receipt", None)
        item.update(state="provider_confirmed" if confirmations else "provider_unconfirmed",
                    confirmations=confirmations, checked_at=now().isoformat(), error=None)
        if item["state"] != previous:
            self.api.invalidate_balance("refresh_required_after_confirmation_change")
        await self.save()
        if item["state"] != previous:
            await self.api.refresh_balance_if_due()

    async def tick(self):
        """Coordinator lock serialises this worker with all wallet services."""
        for row in self.api.saved["session_budgets"].values():
            item = self.get(row)
            if not (item and item.get("txid")) and (
                    not self.policy.get("enabled") or not row.get("credit_destination")
                    or not self.api.collections.session_id(row)):
                continue
            try:
                if not item:
                    source = await self.api.collections.source(row)
                    if not source.get("ended_at") or decimal(source["net_cost_aud_unrounded"]) >= 0:
                        continue
                await self.process(row)
                row.pop("credit_error", None)
            except WalletError as exc:
                row["credit_error"] = str(exc)
                if item:
                    item["error"] = str(exc)
            await self.save()

    def status(self, row):
        item = self.get(row)
        return (self.public(item) if item else {
            "state": "automatic_credit_pending" if row.get("credit_destination") else "credit_destination_required",
            "error": row.get("credit_error"),
        }) | {"direction": "operator_to_driver"}

    async def receipt(self, row):
        item = self.get(row)
        return await self.receipt_for_item(row, item)

    async def receipt_for_item(self, row, item):
        if item and item.get("txid"):
            await self.reconcile(item, force=True)
        if not item or item["state"] != "provider_confirmed":
            raise WalletError("Credit receipt awaits provider confirmation")
        if not item.get("receipt"):
            proof = await self.api.chain.request("GET", f"/tx/{item['txid']}/proof/tsc")
            if (not isinstance(proof, list) or len(proof) != 1 or
                    proof[0].get("txOrId") != item["txid"] or
                    not TXID.fullmatch(str(proof[0].get("target", "")))):
                raise WalletError("Invalid provider Merkle proof")
            block = await self.api.chain.request("GET", f"/block/hash/{proof[0]['target']}")
            if block.get("hash") != proof[0]["target"]:
                raise WalletError("Provider block identity mismatch")
            item["receipt"] = {"raw_tx": item["signed_raw"], "proof": proof[0],
                               "block": {k: block.get(k) for k in ("hash", "height", "merkleroot")}}
            await self.save()
        # Descriptive energy metadata is not part of the blockchain transaction.
        # Older payments can recover it only from the exact frozen source hash.
        account = item.get("account")
        if account is None:
            try:
                account = await self.api.reviews.source(
                    row["proxy_config_entry_id"], item["session_id"])
            except WalletError:
                account = None
        if (account is not None and
                (digest(account) != item["source_hash"] or
                 account.get("session_id") != item["session_id"] or
                 account.get("ocpp_transaction_id") != item["transaction_id"] or
                 account.get("net_amount_aud") != item["net_amount_aud"])):
            account = None
        return self.public(item) | copy.deepcopy(item["receipt"]) | {
            "remittance": row["terms"]["credit_receiving"],
            "sender_identity": self.api.identity["public_key"],
            "energy_account": ({k: copy.deepcopy(account[k]) for k in (
                "session_id", "ocpp_transaction_id", "import_kwh", "export_kwh",
                "net_amount_aud", "currency", "quality_flags")} if account else None),
        }
