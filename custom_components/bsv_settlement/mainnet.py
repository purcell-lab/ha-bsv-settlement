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
TXID = re.compile(r"^[0-9a-f]{64}$")


def utcnow():
    return datetime.now(timezone.utc)


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
        if any("coinbase" in vin for vin in details.get("vin", [])) and details["confirmations"] < 100:
            raise WalletError("Coinbase funding is not mature")
        return raw

    async def details(self, txid):
        if not TXID.fullmatch(txid):
            raise WalletError("Invalid transaction ID")
        result = await self.request("GET", f"/tx/hash/{txid}")
        if not isinstance(result, dict) or result.get("txid") != txid:
            raise WalletError("Chain transaction ID mismatch")
        return result

    async def broadcast(self, raw):
        # The only network write in this module. Called after durable approval.
        return await self.request("POST", "/tx/raw", {"txhex": raw}, raw=True)


class MainnetWalletAPI(EmbeddedWalletAPI):
    mode = "embedded_mainnet"
    network = "mainnet"

    async def load(self):
        if (self.entry.data.get("acknowledge_mainnet") is not True
                or self.entry.data.get("enable_broadcast") is not True):
            raise WalletError("Mainnet custody and guarded broadcast must be explicitly enabled")
        await super().load()
        self.saved.setdefault("driver", {"driver_public_identity": "", "driver_receive_address": ""})
        self.saved.setdefault("payments", {})
        self.saved.setdefault("active_payment", None)
        self.saved.setdefault("chain", {"balance_sats": None, "checked_at": None, "error": None})
        self.chain = WoCClient(async_get_clientsession(self.hass))
        from .session_review import SessionReviews
        self.reviews = SessionReviews(self)
        from .budget import SessionBudgets
        self.budgets = SessionBudgets(self)
        from .collection import DriverCollections
        self.collections = DriverCollections(self)
        from .auto_credit import AutomaticCredits
        self.auto_credits = AutomaticCredits(self)

    def status(self):
        result = super().status()
        active = self.saved.get("active_payment")
        payment = self.saved.get("payments", {}).get(active)
        chain = self.saved.get("chain", {})
        result.update(
            state="broadcast_enabled_approval_required", broadcast_enabled=True,
            balance_sats=chain.get("balance_sats"), balance_verified=False,
            balance_source="WhatsOnChain confirmed UTXOs, not independently SPV verified",
            chain_checked_at=chain.get("checked_at"), chain_error=chain.get("error"),
            driver_identity_status="submitted_unverified" if self.saved["driver"]["driver_public_identity"] else "not_submitted",
            last_payment=self.public_payment(payment) if payment else None,
            max_payment_sats=MAX_PAYMENT_SATS, max_fee_sats=MAX_FEE_SATS,
            latest_session_review=self.reviews.latest() if hasattr(self, "reviews") else None,
            automatic_credit=self.auto_credits.summary() if hasattr(self, "auto_credits") else None,
        )
        if hasattr(self, "auto_credits") and self.auto_credits.policy.get("enabled"):
            result["state"] = "broadcast_enabled_capped_automatic_credits"
        return result

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

    async def refresh_chain(self):
        try:
            rows = await self.chain.unspent(self.identity["address"])
            self.saved["chain"] = {"balance_sats": sum(r["value"] for r in rows),
                                   "checked_at": utcnow().isoformat(), "error": None}
            p = self.saved["payments"].get(self.saved["active_payment"])
            if p and p.get("txid") and p["state"] in (
                "broadcast_unknown", "submitted", "provider_unconfirmed", "provider_confirmed"):
                details = await self.chain.details(p["txid"])
                confirmations = details.get("confirmations", 0)
                if type(confirmations) is not int or confirmations < 0:
                    raise WalletError("Invalid chain confirmation evidence")
                p["confirmations"] = confirmations
                p["state"] = "provider_confirmed" if confirmations else "provider_unconfirmed"
            await self.store.async_save(self.saved)
            return self.status()
        except WalletError:
            self.saved["chain"] = {"balance_sats": None, "checked_at": None,
                                   "error": "chain_check_failed"}
            await self.store.async_save(self.saved)
            raise

    async def prepare_payment(self, data):
        amount, fee, reference = data["amount_sats"], data["fee_sats"], data["reference"]
        driver = copy.deepcopy(self.saved["driver"])
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
        draft_id = str(uuid4())
        payment = {
            "draft_id": draft_id, "reference": reference, "state": "prepared",
            "recipient_address": driver["driver_receive_address"],
            "driver_fingerprint": fingerprint, "amount_sats": amount, "fee_sats": fee,
            "source_txid": source["tx_hash"], "source_index": source["tx_pos"],
            "source_value": source["value"], "source_hex": raw,
            "change_sats": checked["change_sats"], "expires_at": (utcnow() + timedelta(minutes=10)).isoformat(),
            "txid": None, "signed_raw": None,
            "session_review_id": data.get("session_review_id"),
        }
        self.saved["payments"][draft_id] = payment
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
        fingerprint = hashlib.sha256(json.dumps(self.saved["driver"], sort_keys=True).encode()).hexdigest()
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
        signed = await self.hass.async_add_executor_job(
            build_transaction, self.identity["secret_hex"], p["source_hex"], p["source_index"],
            p["recipient_address"], p["amount_sats"], p["fee_sats"], True)
        p.update(signed_raw=signed["raw"], txid=signed["txid"], state="broadcast_unknown",
                 approved_at=utcnow().isoformat(), approving_user_id=approving_user_id)
        self.saved["chain"] = {"balance_sats": None, "checked_at": None,
                               "error": "refresh_required_after_submission"}
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
        return self.public_payment(p)

    async def cancel_payment(self, data):
        p = self.saved["payments"].get(data["draft_id"])
        if not p or p["state"] not in ("prepared", "expired"):
            raise WalletError("Only unsigned prepared or expired payments can be cancelled")
        p["state"] = "cancelled"
        await self.store.async_save(self.saved)
        return self.public_payment(p)
