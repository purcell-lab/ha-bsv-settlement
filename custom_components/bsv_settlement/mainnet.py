"""Guarded, single-input mainnet P2PKH operator payments.

Manual identity submission is not identity verification. Manual operator-wallet
payments require exact administrator approval. AutomaticCredits has a separate
prospective, capped administrator policy and verified driver receiving keys.
DriverCollections separately
accepts driver-wallet-signed transactions under capped session mandates; it never
holds driver keys. No automatic retry creates or sends a second transaction.
"""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import time
from uuid import uuid4

import aiohttp
from bsv import PrivateKey, PublicKey, P2PKH, Transaction, TransactionInput, TransactionOutput
from bsv.constants import Network
from bsv.script.spend import Spend
from bsv.utils.address import validate_address
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WalletError
from .embedded import EmbeddedWalletAPI

MAX_PAYMENT_SATS = 100000
MAX_FEE_SATS = 1000
MIN_CHANGE_SATS = 546
BALANCE_REFRESH_SECONDS = 300
PENDING_BALANCE_REFRESH_SECONDS = 60
TXID = re.compile(r"^[0-9a-f]{64}$")


def utcnow():
    return datetime.now(timezone.utc)


def collection_display_terms(item, budget_id, session_id):
    """Whitelist frozen quote display fields, never expose the signed payload."""
    try:
        quote = json.loads(item["quote"]["payload"])
        if (quote["budget_id"] != budget_id or
                quote["account"]["session_id"] != session_id or
                type(quote["amount_sats"]) is not int or quote["amount_sats"] < 1):
            return {}
        return {key: quote[key] for key in (
            "amount_sats", "max_fee_sats", "satoshis_per_aud", "expires_at")}
    except (KeyError, TypeError, ValueError):
        return {}


def validate_driver(field, value):
    value = value.strip()
    if not value:
        return ""
    if field == "driver_public_identity":
        if not re.fullmatch(r"(02|03)[0-9a-fA-F]{64}|04[0-9a-fA-F]{128}", value):
            raise WalletError("Enter a public identity key, never a private key or seed")
        try:
            return PublicKey(bytes.fromhex(value)).hex()
        except Exception:
            raise WalletError("Invalid public identity curve point") from None
    if field == "driver_receive_address" and validate_address(value, Network.MAINNET):
        return value
    raise WalletError("Enter a valid mainnet P2PKH receiving address")


def build_transaction(secret, source_hex, index, recipient, amount, fee, sign=False):
    """Validate the owned source and exact outputs. Signed bytes stay private."""
    key = PrivateKey(bytes.fromhex(secret), network=Network.MAINNET)
    source = Transaction.from_hex(source_hex)
    if not source or source.hex() != source_hex.lower():
        raise WalletError("Invalid source transaction encoding")
    if type(index) is not int or not 0 <= index < len(source.outputs):
        raise WalletError("Invalid source output")
    output = source.outputs[index]
    if output.locking_script.hex() != P2PKH().lock(key.address()).hex():
        raise WalletError("Source output is not owned by this operator wallet")
    if (type(amount) is not int or not 1 <= amount <= MAX_PAYMENT_SATS
            or type(fee) is not int or not 1 <= fee <= MAX_FEE_SATS):
        raise WalletError("Payment or fee exceeds demonstration limits")
    if not validate_address(recipient, Network.MAINNET) or recipient == key.address():
        raise WalletError("Recipient must be a different mainnet P2PKH address")
    change = output.satoshis - amount - fee
    if change < MIN_CHANGE_SATS:
        raise WalletError("Insufficient source value or change below the conservative minimum")
    tx = Transaction(
        [TransactionInput(source_transaction=source, source_output_index=index,
                          unlocking_script_template=P2PKH().unlock(key))],
        [TransactionOutput(P2PKH().lock(recipient), satoshis=amount),
         TransactionOutput(P2PKH().lock(key.address()), satoshis=change)],
    )
    if sign:
        tx.sign()
        if not Spend({
            "sourceTXID": source.txid(), "sourceOutputIndex": index,
            "sourceSatoshis": output.satoshis, "lockingScript": output.locking_script,
            "transactionVersion": tx.version, "otherInputs": [], "outputs": tx.outputs,
            "inputIndex": 0, "unlockingScript": tx.inputs[0].unlocking_script,
            "inputSequence": tx.inputs[0].sequence, "lockTime": tx.locktime,
        }).validate():
            raise WalletError("Transaction script validation failed")
    return {"raw": tx.hex(), "txid": tx.txid() if sign else None,
            "source_txid": source.txid(), "source_value": output.satoshis,
            "change_sats": change}


class WoCClient:
    """Fixed-origin, bounded, rate-limited HTTPS. No redirects or auto retries."""

    ROOT = "https://api.whatsonchain.com/v1/bsv/main"

    def __init__(self, session):
        self.session = session
        self.lock = asyncio.Lock()
        self.last_request = 0.0

    async def request(self, method, path, body=None, raw=False):
        async with self.lock:
            await asyncio.sleep(max(0, 0.4 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                async with self.session.request(
                    method, self.ROOT + path, json=body, allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=25),
                ) as response:
                    if response.status != 200:
                        raise WalletError(f"Chain provider HTTP {response.status}; payment state must be reconciled")
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 2000000:
                            raise WalletError("Chain provider response exceeds validation limit")
                        chunks.append(chunk)
                    text = b"".join(chunks).decode()
                    return text.strip().strip('"') if raw else json.loads(text)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, UnicodeError):
                raise WalletError("Chain provider unavailable or response invalid") from None

    async def unspent(self, address):
        if not validate_address(address, Network.MAINNET):
            raise WalletError("Invalid operator address")
        data = await self.request("GET", f"/address/{address}/confirmed/unspent")
        if (not isinstance(data, dict) or data.get("error")
                or data.get("address") != address or not isinstance(data.get("result"), list)
                or data.get("next-page") or data.get("nextPage")):
            raise WalletError("Invalid or paginated UTXO response; refusing incomplete wallet data")
        rows = data["result"]
        if len(rows) > 1000:
            raise WalletError("Wallet exceeds this demonstration's UTXO limit")
        seen, clean = set(), []
        for row in rows:
            if (not isinstance(row, dict) or not TXID.fullmatch(str(row.get("tx_hash", "")))
                    or type(row.get("tx_pos")) is not int or not 0 <= row["tx_pos"] <= 0xFFFFFFFF
                    or type(row.get("value")) is not int or not 0 < row["value"] <= 2100000000000000
                    or type(row.get("height")) is not int or row["height"] <= 0
                    or type(row.get("isSpentInMempoolTx")) is not bool):
                raise WalletError("Malformed UTXO evidence")
            outpoint = (row["tx_hash"], row["tx_pos"])
            if outpoint in seen:
                raise WalletError("Duplicate UTXO evidence")
            seen.add(outpoint)
            if not row["isSpentInMempoolTx"]:
                clean.append(row)
        return clean

    async def source(self, txid):
        if not TXID.fullmatch(txid):
            raise WalletError("Invalid source transaction ID")
        raw = await self.request("GET", f"/tx/{txid}/hex", raw=True)
        details = await self.details(txid)
        if type(details.get("confirmations")) is not int or details["confirmations"] < 1:
            raise WalletError("Only provider-confirmed funding outputs may be selected")
        # WoC includes coinbase="" on normal inputs. Classify the canonical
        # null outpoint in the raw transaction, not the presence of that field.
        try:
            tx = Transaction.from_hex(raw)
            if not tx or tx.hex() != raw.lower() or tx.txid() != txid or not tx.inputs:
                raise ValueError()
            null_inputs = [i for i in tx.inputs if
                           i.source_txid == "00" * 32 and i.source_output_index == 0xFFFFFFFF]
            if null_inputs and len(tx.inputs) != 1:
                raise ValueError()
        except Exception:
            raise WalletError("Invalid funding transaction encoding or identity") from None
        if null_inputs and details["confirmations"] < 100:
            raise WalletError("Coinbase funding is not mature")
        return raw

    async def unconfirmed_unspent(self, address):
        """Recovery-only evidence; never used by ordinary funding selection."""
        if not validate_address(address, Network.MAINNET):
            raise WalletError("Invalid operator address")
        data = await self.request("GET", f"/address/{address}/unconfirmed/unspent")
        if (not isinstance(data, dict) or data.get("error")
                or data.get("address") != address or not isinstance(data.get("result"), list)
                or data.get("next-page") or data.get("nextPage") or len(data["result"]) > 1000):
            raise WalletError("Invalid or incomplete unconfirmed UTXO evidence")
        seen, clean = set(), []
        for row in data["result"]:
            if (not isinstance(row, dict) or not TXID.fullmatch(str(row.get("tx_hash", "")))
                    or type(row.get("tx_pos")) is not int or not 0 <= row["tx_pos"] <= 0xFFFFFFFF
                    or type(row.get("value")) is not int or not 0 < row["value"] <= 2100000000000000
                    or type(row.get("isSpentInMempoolTx")) is not bool):
                raise WalletError("Malformed unconfirmed UTXO evidence")
            key = (row["tx_hash"], row["tx_pos"])
            if key in seen:
                raise WalletError("Duplicate unconfirmed UTXO evidence")
            seen.add(key)
            if not row["isSpentInMempoolTx"]:
                clean.append(row)
        return clean

    async def details(self, txid):
        if not TXID.fullmatch(txid):
            raise WalletError("Invalid transaction ID")
        result = await self.request("GET", f"/tx/hash/{txid}")
        if not isinstance(result, dict) or result.get("txid") != txid:
            raise WalletError("Chain transaction ID mismatch")
        return result

    async def fee_policy(self):
        return await self.request("GET", "/feerecommendation")

    async def broadcast(self, raw):
        # The only network write in this module. Called after durable approval.
        return await self.request("POST", "/tx/raw", {"txhex": raw}, raw=True)


class MainnetWalletAPI(EmbeddedWalletAPI):
    mode = "embedded_mainnet"
    network = "mainnet"

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        from .ledger_checkpoint import CheckpointedStore
        self.store = CheckpointedStore(hass, entry, self.store)

    async def load(self):
        if (self.entry.data.get("acknowledge_mainnet") is not True
                or self.entry.data.get("enable_broadcast") is not True):
            raise WalletError("Mainnet custody and guarded broadcast must be explicitly enabled")
        await super().load()
        from .provenance_archive import ProvenanceArchive
        self.provenance_archive = ProvenanceArchive(self.hass, self.entry.entry_id)
        await self.provenance_archive.load()  # Evidence only; never blocks the wallet.
        self.saved.setdefault("driver", {"driver_public_identity": "", "driver_receive_address": ""})
        self.saved.setdefault("payments", {})
        self.saved.setdefault("active_payment", None)
        self.saved.setdefault("chain", {"balance_sats": None, "checked_at": None, "error": None})
        # Refresh once on startup, including stores written before this feature.
        self._balance_next_refresh = 0.0
        self._balance_refresh_required = True
        self.chain = WoCClient(async_get_clientsession(self.hass))
        from .session_review import SessionReviews
        self.reviews = SessionReviews(self)
        from .budget import SessionBudgets
        self.budgets = SessionBudgets(self)
        from .collection import DriverCollections
        self.collections = DriverCollections(self)
        from .auto_credit import AutomaticCredits
        self.auto_credits = AutomaticCredits(self)
        from .ongoing_credit import OngoingCredits
        self.ongoing_credits = OngoingCredits(self)
        from .credit_recovery import OperatorCreditRecovery
        self.credit_recovery = OperatorCreditRecovery(self)
        from .session_closure import ClosedSessions
        self.closures = ClosedSessions(self)
        from .confirmation_scheduler import DriverConfirmationScheduler
        self.driver_confirmations = DriverConfirmationScheduler(self)

    def status(self):
        result = super().status()
        active = self.saved.get("active_payment")
        payment = self.saved.get("payments", {}).get(active)
        chain = self.saved.get("chain", {})
        approvals, approval_counts = (self.budgets.summary_window() if hasattr(self, "budgets")
                                      else ([], {"total": 0, "shown": 0, "unresolved": 0}))
        session_rows, payment_counts = self.payment_summary_window()
        result.update(
            state="broadcast_enabled_approval_required", broadcast_enabled=True,
            balance_sats=chain.get("balance_sats"), balance_verified=False,
            balance_source="WhatsOnChain confirmed UTXOs, not independently SPV verified",
            chain_checked_at=chain.get("checked_at"), chain_error=chain.get("error"),
            chain_attempted_at=chain.get("attempted_at"),
            payment_check_error=chain.get("payment_check_error"),
            payment_check_attempted_at=chain.get("payment_check_attempted_at"),
            pending_change_sats=self.pending_change(),
            pending_change_source="Locally signed operator change; not confirmed or spendable",
            driver_identity_status="submitted_unverified" if self.saved["driver"]["driver_public_identity"] else "not_submitted",
            last_payment=self.public_payment(payment) if payment else None,
            max_payment_sats=MAX_PAYMENT_SATS, max_fee_sats=MAX_FEE_SATS,
            latest_session_review=self.reviews.latest() if hasattr(self, "reviews") else None,
            automatic_credit=self.auto_credits.summary() if hasattr(self, "auto_credits") else None,
            driver_approvals=approvals,
            driver_approvals_window=approval_counts,
            session_payments=session_rows,
            session_payments_window=payment_counts,
            ongoing_credit=self.ongoing_credits.summary() if hasattr(self, "ongoing_credits") else None,
            closed_sessions=self.closures.summary() if hasattr(self, "closures") else [],
            record_audit=self.store.audit.summary() if hasattr(self.store, "audit") else None,
        )
        if hasattr(self, "auto_credits") and self.auto_credits.policy.get("enabled"):
            result["state"] = "broadcast_enabled_capped_automatic_credits"
        return result

    def payment_summary(self):
        return MainnetWalletAPI.payment_summary_window(self)[0]

    def payment_summary_window(self):
        """Compact authenticated display records. Never include signed payloads.

        Every unresolved review/collection plus the newest resolved ones (#105)."""
        from .session_review import now
        from .summary_window import collection_unresolved, combine, review_unresolved, window
        payments, clock = self.saved.get("payments", {}), now()

        def review_open(review):
            return review_unresolved(
                review, payments.get(review.get("credit_draft_id")),
                clock >= datetime.fromisoformat(review["expires_at"]))

        budgets = self.saved.get("session_budgets", {})
        state = getattr(getattr(self, "budgets", None), "state", None)
        reviews, review_counts = window(self.saved.get("session_reviews", {}).values(), review_open)
        collections, collection_counts = window(
            [(k, v) for k, v in self.saved.get("driver_collections", {}).items() if k in budgets],
            lambda kv: collection_unresolved(kv[1], state(budgets[kv[0]]) if state else None))
        rows = []
        for review in reviews:
            r = self.reviews.public(review)
            p = r.get("credit_draft") or r.get("receipt") or {}
            rows.append({
                "review_id": review["review_id"],
                "account_kind": review.get("account_kind"),
                "session_id": r["account"]["session_id"],
                "transaction_id": r["account"]["ocpp_transaction_id"],
                "state": p.get("state", r["state"]),
                "direction": r["direction"], "amount_sats": r["amount_sats"],
                "fee_sats": p.get("fee_sats"), "txid": p.get("txid"),
                "checked_at": p.get("checked_at"),
                "confirmations": p.get("confirmations"), "output_index": p.get("output_index"),
                "error": p.get("verification_error"), "source": "manual",
            })
        for budget_id, item in collections:
            row = budgets.get(budget_id)
            if row:
                session_id = self.collections.session_id(row)
                rows.append({
                    "budget_id": budget_id,
                    "session_id": session_id,
                    "transaction_id": (row.get("binding") or {}).get(
                        "transaction_id", row["terms"].get("transaction_id")),
                    "state": item["state"], "direction": "driver_to_operator",
                    "txid": item.get("txid"), "error": item.get("error"),
                    "checked_at": item.get("checked_at"),
                    "confirmations": item.get("confirmations"), "output_index": item.get("output_index"),
                    "source": "driver", "diagnostic": copy.deepcopy(item.get("diagnostic")),
                    "recovery": copy.deepcopy(item.get("recovery")),
                    **collection_display_terms(item, budget_id, session_id),
                })
        return rows, combine(review_counts, collection_counts)

    def public_payment(self, payment):
        if payment is None:
            return None
        return {key: copy.deepcopy(payment.get(key)) for key in (
            "draft_id", "reference", "recipient_address", "amount_sats", "fee_sats",
            "change_sats", "expires_at", "state", "txid", "approved_at", "confirmations",
            "session_review_id")}

    async def set_driver(self, field, value):
        if field not in self.saved["driver"]:
            raise WalletError("Unsupported driver field")
        normalized = await self.hass.async_add_executor_job(validate_driver, field, value)
        if normalized == self.saved["driver"][field]:
            return
        active = self.saved["payments"].get(self.saved["active_payment"])
        if active and active["state"] in ("broadcast_unknown", "submitted", "provider_unconfirmed"):
            raise WalletError("Resolve the pending payment before changing driver details")
        if active and active["state"] == "prepared":
            active["state"] = "cancelled_driver_changed"
        self.saved["driver"][field] = normalized
        await self.store.async_save(self.saved)

    def signed_payments(self):
        """Only public transaction metadata is exposed by callers."""
        return [p for group in ("payments", "automatic_credits")
                for p in self.saved.get(group, {}).values() if p.get("txid")]

    def pending_change(self):
        """Show expected change separately, never add it to spendable funds."""
        total, seen = 0, set()
        for p in self.signed_payments():
            if p["state"] not in ("broadcast_unknown", "submitted", "provider_unconfirmed"):
                continue
            if p["txid"] in seen:
                continue
            seen.add(p["txid"])
            try:
                tx = Transaction.from_hex(p["signed_raw"])
                if tx.txid() != p["txid"]:
                    return None
                owned = P2PKH().lock(self.identity["address"]).hex()
                total += sum(o.satoshis for o in tx.outputs if o.locking_script.hex() == owned)
            except Exception:
                return None
        return total

    def invalidate_balance(self, reason):
        self.saved["chain"] = {"balance_sats": None, "checked_at": None, "error": reason}
        self._balance_refresh_required = True

    async def refresh_balance_if_due(self, reconcile_payment=False):
        """Read-only maintenance; failures must never change payment outcomes."""
        if not self._balance_refresh_required and time.monotonic() < self._balance_next_refresh:
            return
        try:
            await self.refresh_chain(reconcile_payment=reconcile_payment)
        except WalletError:
            # refresh_chain persists unavailable/error and sets bounded backoff.
            pass

    async def refresh_chain(self, reconcile_payment=True):
        pending = any(p["state"] in ("broadcast_unknown", "submitted", "provider_unconfirmed")
                      for p in self.signed_payments())
        self._balance_refresh_required = False
        self._balance_next_refresh = time.monotonic() + (
            PENDING_BALANCE_REFRESH_SECONDS if pending else BALANCE_REFRESH_SECONDS)
        attempted_at = utcnow().isoformat()
        try:
            rows = await self.chain.unspent(self.identity["address"])
            # A lagging indexer can still return a signed input. Do not count it
            # as spendable, even when submission outcome is unknown.
            used = {(p["source_txid"], p["source_index"]) for p in self.signed_payments()}
            rows = [r for r in rows if (r["tx_hash"], r["tx_pos"]) not in used]
            previous = self.saved.get("chain") or {}
            chain = self.saved["chain"] = {"balance_sats": sum(r["value"] for r in rows),
                                           "checked_at": utcnow().isoformat(),
                                           "attempted_at": attempted_at, "error": None}
            p = self.saved["payments"].get(self.saved["active_payment"])
            if reconcile_payment and p and p.get("txid") and p["state"] in (
                "broadcast_unknown", "submitted", "provider_unconfirmed", "provider_confirmed"):
                try:
                    details = await self.chain.details(p["txid"])
                    confirmations = details.get("confirmations", 0)
                    if type(confirmations) is not int or confirmations < 0:
                        raise ValueError
                except (WalletError, ValueError) as exc:
                    # #102: missing payment evidence (e.g. signed, never posted) keeps the
                    # balance read above (signed inputs excluded) and leaves the payment
                    # exactly as it was: never resent, cancelled, released or failed.
                    chain["payment_check_error"] = ("payment_evidence_invalid" if isinstance(exc, ValueError)
                                                    else "payment_evidence_unavailable")
                    chain["payment_check_attempted_at"] = attempted_at
                else:
                    p["confirmations"] = confirmations
                    p["state"] = "provider_confirmed" if confirmations else "provider_unconfirmed"
            elif previous.get("payment_check_error") and p and p.get("txid") and p["state"] in (
                    "broadcast_unknown", "submitted", "provider_unconfirmed"):
                # Not reconciled this time: the earlier condition still stands.
                for key in ("payment_check_error", "payment_check_attempted_at"):
                    chain[key] = previous.get(key)
            await self.store.async_save(self.saved)
            return self.status()
        except WalletError:
            self.saved["chain"] = {"balance_sats": None, "checked_at": None,
                                   "attempted_at": attempted_at, "error": "chain_check_failed"}
            await self.store.async_save(self.saved)
            raise

    async def prepare_payment(self, data):
        amount, fee, reference = data["amount_sats"], data["fee_sats"], data["reference"]
        driver = copy.deepcopy(self.saved["driver"])
        adjustment = self.saved["session_reviews"].get(data.get("session_review_id"), {})
        if adjustment.get("account_kind") == "manual_energy_adjustment":
            from .energy_adjustment import payment_driver, MAX_TOTAL
            driver = payment_driver(self, adjustment)
            if (adjustment["state"] != "credit_review_approved"
                    or amount != adjustment["amount_sats"] or amount + fee > MAX_TOTAL):
                raise WalletError("Adjustment amount, approval or total limit does not match")
        if not all(driver.values()):
            raise WalletError("Submit both driver public identity and a separately confirmed receiving address")
        fingerprint = hashlib.sha256(json.dumps(driver, sort_keys=True).encode()).hexdigest()
        for old in self.saved["payments"].values():
            if old["reference"] == reference:
                if old.get("session_review_id") != data.get("session_review_id"):
                    raise WalletError("Payment reference belongs to a different review workflow")
                if (old["amount_sats"], old["fee_sats"], old["driver_fingerprint"]) != (amount, fee, fingerprint):
                    raise WalletError("Payment reference already used with different terms")
                return self.public_payment(old)
        active = self.saved["payments"].get(self.saved["active_payment"])
        if active and active["state"] in ("prepared", "broadcast_unknown", "submitted", "provider_unconfirmed"):
            raise WalletError("Resolve or cancel the existing payment first")
        if (type(amount) is not int or not 1 <= amount <= MAX_PAYMENT_SATS
                or type(fee) is not int or not 1 <= fee <= MAX_FEE_SATS):
            raise WalletError("Payment or fee outside demonstration limits")
        from .fees import quote, validate
        fee_quote = await quote(self.chain)
        validate(fee_quote, fee)
        rows = await self.chain.unspent(self.identity["address"])
        # Never reuse a source we have signed, even if the indexer is stale.
        used = {(p["source_txid"], p["source_index"]) for p in self.saved["payments"].values() if p.get("txid")}
        if self.auto_credits.pending():
            raise WalletError("Resolve the pending automatic credit first")
        used |= self.auto_credits.used()
        candidates = [r for r in rows if r["value"] >= amount + fee + MIN_CHANGE_SATS
                      and (r["tx_hash"], r["tx_pos"]) not in used]
        if not candidates:
            raise WalletError("No suitable confirmed funding output; this POC requires one input and change")
        source = min(candidates, key=lambda r: r["value"])
        raw = await self.chain.source(source["tx_hash"])
        checked = await self.hass.async_add_executor_job(
            build_transaction, self.identity["secret_hex"], raw, source["tx_pos"],
            driver["driver_receive_address"], amount, fee, False)
        if checked["source_txid"] != source["tx_hash"] or checked["source_value"] != source["value"]:
            raise WalletError("Funding evidence does not match the raw transaction")
        validate(fee_quote, fee)
        draft_id = str(uuid4())
        payment = {
            "draft_id": draft_id, "reference": reference, "state": "prepared",
            "recipient_address": driver["driver_receive_address"],
            "driver_fingerprint": fingerprint, "amount_sats": amount, "fee_sats": fee,
            "source_txid": source["tx_hash"], "source_index": source["tx_pos"],
            "source_value": source["value"], "source_hex": raw,
            "change_sats": checked["change_sats"], "expires_at": (utcnow() + timedelta(minutes=10)).isoformat(),
            "txid": None, "signed_raw": None,
            "session_review_id": data.get("session_review_id"), "fee_quote": fee_quote,
        }
        self.saved["payments"][draft_id] = payment
        if adjustment.get("account_kind") == "manual_energy_adjustment":
            payment.update(budget_id="adjustment:" + adjustment["review_id"],
                           session_id=adjustment["account"]["session_id"],
                           transaction_id=adjustment["account"]["ocpp_transaction_id"],
                           account=copy.deepcopy(adjustment["account"]),
                           source_hash=adjustment["source_hash"],
                           net_amount_aud=adjustment["account"]["net_amount_aud"],
                           created_at=utcnow().isoformat())
        self.saved["active_payment"] = draft_id
        await self.store.async_save(self.saved)
        return self.public_payment(payment)

    async def broadcast_payment(self, data, approving_user_id):
        p = self.saved["payments"].get(data["draft_id"])
        if not approving_user_id or data.get("confirm_mainnet_payment") is not True:
            raise WalletError("Explicit administrator approval is required")
        if p is None:
            raise WalletError("Prepare the payment first")
        if p.get("session_review_id") and data.get("session_review_id") != p["session_review_id"]:
            raise WalletError("Use the session-linked credit approval action for this draft")
        for field in ("recipient_address", "amount_sats", "fee_sats"):
            if data[field] != p[field]:
                raise WalletError("Approval does not match the prepared recipient, amount and fee")
        if p["state"] in ("broadcast_unknown", "submitted", "provider_unconfirmed", "provider_confirmed"):
            return self.public_payment(p)  # No automatic rebroadcast or new signature.
        if p["state"] != "prepared":
            raise WalletError("Payment is not awaiting approval")
        if utcnow() >= datetime.fromisoformat(p["expires_at"]):
            p["state"] = "expired"
            await self.store.async_save(self.saved)
            raise WalletError("Payment approval expired")
        driver = self.saved["driver"]
        review = self.saved["session_reviews"].get(p.get("session_review_id"), {})
        if review.get("account_kind") == "manual_energy_adjustment":
            from .energy_adjustment import payment_driver
            driver = payment_driver(self, review)
        fingerprint = hashlib.sha256(json.dumps(driver, sort_keys=True).encode()).hexdigest()
        if fingerprint != p["driver_fingerprint"]:
            raise WalletError("Driver details changed; prepare a new payment")
        rows = await self.chain.unspent(self.identity["address"])
        if not any((r["tx_hash"], r["tx_pos"], r["value"]) ==
                   (p["source_txid"], p["source_index"], p["source_value"]) for r in rows):
            raise WalletError("Funding output is no longer available")
        if p.get("session_review_id"):
            review = self.saved["session_reviews"].get(p["session_review_id"])
            if (not review or review["state"] != "credit_review_approved"
                    or utcnow() >= datetime.fromisoformat(review["expires_at"])):
                raise WalletError("Session account approval expired before signing")
        from .fees import quote, validate
        fee_quote = await quote(self.chain)
        validate(fee_quote, p["fee_sats"])
        signed = await self.hass.async_add_executor_job(
            build_transaction, self.identity["secret_hex"], p["source_hex"], p["source_index"],
            p["recipient_address"], p["amount_sats"], p["fee_sats"], True)
        validate(fee_quote, p["fee_sats"], len(signed["raw"]) // 2)
        p["fee_quote"] = fee_quote
        p.update(signed_raw=signed["raw"], txid=signed["txid"], state="broadcast_unknown",
                 approved_at=utcnow().isoformat(), approving_user_id=approving_user_id)
        self.invalidate_balance("refresh_required_after_submission")
        # Commit exact signed bytes and input reservation BEFORE touching network.
        await self.store.async_save(self.saved)
        try:
            returned = await self.chain.broadcast(p["signed_raw"])
            if returned != p["txid"]:
                raise WalletError("Broadcast response did not match the prepared transaction")
            p["state"] = "submitted"  # Provider acknowledgement, not paid/confirmed.
            await self.store.async_save(self.saved)
        except WalletError:
            # Preserve ambiguous state across restarts; never release or retry.
            raise WalletError("Broadcast outcome uncertain; refresh chain status, do not prepare a replacement") from None
        finally:
            await self.refresh_balance_if_due()
        return self.public_payment(p)

    async def cancel_payment(self, data):
        p = self.saved["payments"].get(data["draft_id"])
        if not p or p["state"] not in ("prepared", "expired"):
            raise WalletError("Only unsigned prepared or expired payments can be cancelled")
        p["state"] = "cancelled"
        await self.store.async_save(self.saved)
        return self.public_payment(p)
