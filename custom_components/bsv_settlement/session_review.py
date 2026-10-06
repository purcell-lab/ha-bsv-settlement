"""Admin-reviewed session accounts. Requests do not debit an external wallet."""
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
import math
import re
from uuid import uuid4

from bsv import P2PKH, Transaction
from .api import WalletError
from .const import DOMAIN
from .confirmation import read_transaction, confirmation_count

SIGNED_STATES = {"broadcast_unknown", "submitted", "provider_unconfirmed", "provider_confirmed"}
BENIGN_FLAGS = {"interval_energy_allocation_estimated", "not_a_final_bill"}
WARNING_FLAGS = BENIGN_FLAGS | {
    "import:energy_without_matching_state", "export:energy_without_matching_state",
    "import:estimated_tariff", "export:estimated_tariff",
}
CAP = 100000


def now():
    return datetime.now(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def decimal(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise WalletError("Invalid account amount or conversion rate") from None
    if not result.is_finite():
        raise WalletError("Non-finite account amount or conversion rate")
    return result


def account_snapshot(record):
    if not record.get("ended_at") or record.get("status") != "ended_observed":
        raise WalletError("Only a closed session can enter payment review")
    # Known timing and estimated-tariff flags are disclosed, not payment vetoes.
    # Unknown flags still fail closed; a warning never substitutes for coverage.
    blockers = set(record.get("quality_flags", [])) - WARNING_FLAGS
    if blockers:
        raise WalletError("Resolve session quality issues before review: " + ", ".join(sorted(blockers)))
    for field in ("unpriced_import_wh", "unpriced_export_wh"):
        if record.get(field) is None or decimal(record[field]) != 0:
            raise WalletError("Complete interval pricing is required; unpriced energy cannot settle")
    if record.get("net_cost_aud_unrounded") is None:
        raise WalletError("The final session amount is unavailable")
    if record.get("import_kwh") is None or record.get("export_kwh") is None:
        raise WalletError("Session energy is unavailable")
    if any(not 0 <= decimal(record[field]) <= 1000000000
           for field in ("import_kwh", "export_kwh")):
        raise WalletError("Invalid session energy")
    for direction in ("import", "export"):
        field = f"estimated_rate_{direction}_wh"
        if record.get(field) is None:
            raise WalletError("Estimated tariff coverage is unavailable")
        estimated = decimal(record[field])
        # kWh is a float in the recorder summary; tolerate only conversion noise.
        if not 0 <= estimated <= decimal(record[f"{direction}_kwh"]) * 1000 + Decimal("0.00000001"):
            raise WalletError("Invalid estimated tariff coverage")
        if estimated > 0 and f"{direction}:estimated_tariff" not in record.get("quality_flags", []):
            raise WalletError("Estimated tariffs must be disclosed in the session warnings")
    amount = decimal(record["net_cost_aud_unrounded"]).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    if abs(amount) > 1000000:
        raise WalletError("Session account exceeds demonstration limits")
    return {
        key: copy.deepcopy(record.get(key)) for key in (
            "session_id", "ocpp_transaction_id", "transaction_id_source",
            "opened_at", "energy_started_at", "ended_at", "import_kwh", "export_kwh",
            "net_cost_aud_unrounded", "quality_flags")
    } | {"net_amount_aud": str(amount), "currency": "AUD"}


def verify_output(raw, expected_txid, index, recipient, amount):
    try:
        tx = Transaction.from_hex(raw)
        if not tx or tx.hex() != raw.lower() or tx.txid() != expected_txid:
            raise ValueError()
        if type(index) is not int or not 0 <= index < len(tx.outputs):
            raise ValueError()
        output = tx.outputs[index]
        if output.satoshis != amount or output.locking_script.hex() != P2PKH().lock(recipient).hex():
            raise ValueError()
    except Exception:
        raise WalletError("Transaction output does not match the requested recipient and exact amount") from None


class SessionReviews:
    def __init__(self, api):
        self.api = api
        self.hass = api.hass
        api.saved.setdefault("session_reviews", {})
        api.saved.setdefault("session_review_index", {})
        api.saved.setdefault("latest_session_review", None)
        api.saved.setdefault("received_outpoints", {})

    def get(self, review_id):
        review = self.api.saved["session_reviews"].get(review_id)
        if not review:
            raise WalletError("Session review not found")
        return review

    def public(self, review):
        if review is None:
            return None
        result = copy.deepcopy(review)
        # Private HA/user identifiers and raw signing material are never returned.
        for key in ("approved_by", "created_by", "proxy_config_entry_id", "source_hash",
                    "driver_payment_raw", "one_click_authorised_by", "tariff_provenance"):
            result.pop(key, None)
        if review.get("account_kind") != "manual_energy_adjustment":
            result["tariff_provenance"] = self.provenance(review)
        if review.get("credit_draft_id"):
            payment = self.api.saved["payments"].get(review["credit_draft_id"])
            result["credit_draft"] = self.api.public_payment(payment)
            if payment:
                result["state"] = "credit_" + payment["state"]
                if payment["state"] == "prepared" and now() >= datetime.fromisoformat(review["expires_at"]):
                    result["state"] = "credit_review_expired"
        if (review["state"] in ("awaiting_account_approval", "credit_review_approved", "awaiting_driver_payment")
                and now() >= datetime.fromisoformat(review["expires_at"])
                and not review.get("credit_draft_id")):
            result["state"] = "expired_awaiting_reconciliation" if review.get("payment_request") else "expired"
        return result

    def latest(self):
        return self.public(self.api.saved["session_reviews"].get(self.api.saved["latest_session_review"]))

    async def save(self):
        await self.api.store.async_save(self.api.saved)

    async def source(self, proxy_entry, session_id):
        proxy = self.hass.data.get(DOMAIN, {}).get(proxy_entry)
        if proxy is None or proxy.mode != "sensor_proxy":
            raise WalletError("Select a loaded read-only session recorder")
        await proxy.async_request_refresh()
        if (proxy.data or {}).get("issues"):
            raise WalletError("Resolve recorder/source availability issues before account review")
        data = proxy.data or {}
        candidates = [data.get("latest_session"), data.get("previous_session"), *proxy.archive]
        record = next((r for r in candidates if r and r["session_id"] == session_id), None)
        if record is None:
            raise WalletError("Session is not in the recorder's retained history")
        return account_snapshot(record)

    def frozen_provenance(self, proxy_entry, session_id, account_digest):
        """Copy the provenance version captured for exactly this account, if any."""
        proxy = self.hass.data.get(DOMAIN, {}).get(proxy_entry)
        lookup = getattr(proxy, "tariff_provenance", None)
        if lookup is None:
            return None
        try:
            return lookup(session_id, account_digest, detail=True)
        except Exception:  # noqa: BLE001 - evidence must never block an account review
            return None

    def provenance(self, review, detail=False):
        """Read-only operator view; missing provenance is reported, never invented."""
        from .tariff_provenance import summary, verify
        version = review.get("tariff_provenance")
        if not detail:
            result = summary(version)
            if version:
                result["digest_verified"] = verify(version)
            return result
        if not version:
            return summary(None)
        return copy.deepcopy(version) | {"status": "recorded", "digest_verified": verify(version)}

    async def unchanged_source(self, review):
        if review.get("account_kind") == "manual_energy_adjustment":
            from .energy_adjustment import check_recipient
            check_recipient(self.api, review)
            if digest(review["account"]) != review["source_hash"]:
                raise WalletError("Frozen adjustment account changed")
            return
        account = await self.source(review["proxy_config_entry_id"], review["account"]["session_id"])
        if digest(account) != review["source_hash"]:
            raise WalletError("The session account changed; do not use this frozen review")

    def unexpired(self, review):
        if now() >= datetime.fromisoformat(review["expires_at"]):
            raise WalletError("Review expired; no replacement request or payment is created automatically")

    def exact(self, review, data):
        if data.get("terms_hash") != review["terms_hash"]:
            raise WalletError("Review hash does not match the frozen terms")
        for field in ("recipient_address", "amount_sats"):
            if field in data and data[field] != review[field]:
                raise WalletError("Approval does not match the frozen recipient and amount")

    async def prepare(self, data, user_id):
        session_key = data["proxy_config_entry_id"] + "|" + data["session_id"]
        from .session_closure import ensure_open
        ensure_open(self.api, session_key)
        if self.api.saved.get("automatic_credit_index", {}).get(session_key):
            raise WalletError("Automatic credit already owns this session; reconcile that payment instead")
        if self.api.saved.get("driver_collection_index", {}).get(session_key):
            raise WalletError("Automatic collection already owns this session; reconcile that attempt instead")
        existing = self.api.saved["session_review_index"].get(session_key)
        if existing and self.get(existing)["state"] != "cancelled":
            self.api.saved["latest_session_review"] = existing
            await self.save()
            return self.public(self.get(existing))
        account = await self.source(data["proxy_config_entry_id"], data["session_id"])
        rate_entity = data["conversion_rate_entity"]
        state = self.hass.states.get(rate_entity)
        if state is None or state.attributes.get("unit_of_measurement") != "sat/AUD":
            raise WalletError("Select a conversion-rate sensor in sat/AUD")
        rate = decimal(state.state)
        if not 0 < rate <= 100000000:
            raise WalletError("Conversion rate is outside demonstration limits")
        aud = decimal(account["net_amount_aud"])
        sats = int((abs(aud) * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        if (aud != 0 and sats == 0) or sats > CAP:
            raise WalletError("Converted amount is below one satoshi or exceeds the demonstration cap")
        driver = copy.deepcopy(self.api.saved["driver"])
        if not all(driver.values()):
            raise WalletError("Enter driver public identity and receiving address before review")
        direction = "driver_to_operator" if aud > 0 else "operator_to_driver" if aud < 0 else "none"
        recipient = self.api.identity["address"] if aud > 0 else driver["driver_receive_address"] if aud < 0 else None
        if direction == "operator_to_driver" and recipient == self.api.identity["address"]:
            raise WalletError("Driver credit recipient cannot be the operator")
        review_id = str(uuid4())
        terms = {
            "review_id": review_id, "network": "BSV mainnet", "account": account,
            "direction": direction, "recipient_address": recipient, "amount_sats": sats,
            "conversion_rate_entity": rate_entity, "satoshis_per_aud": str(rate),
            "rate_observed_at": state.last_updated.isoformat(),
            "driver": driver, "identity_verification": "submitted_unverified",
            "fee_policy": "payer reviews fee separately; fee is not included in recipient amount",
            "created_at": now().isoformat(), "expires_at": (now() + timedelta(minutes=10)).isoformat(),
        }
        review = terms | {
            "frozen_terms": copy.deepcopy(terms),
            "terms_hash": digest(terms), "source_hash": digest(account),
            # Evidence only, outside the signed terms and their hash. Frozen here.
            "tariff_provenance": self.frozen_provenance(
                data["proxy_config_entry_id"], account["session_id"], digest(account)),
            "proxy_config_entry_id": data["proxy_config_entry_id"],
            "created_by": user_id, "state": "awaiting_account_approval",
            "payment_request": None, "credit_draft_id": None, "receipt": None,
        }
        self.api.saved["session_reviews"][review_id] = review
        self.api.saved["session_review_index"][session_key] = review_id
        self.api.saved["latest_session_review"] = review_id
        await self.save()
        return self.public(review)

    async def approve(self, data, user_id):
        review = self.get(data["review_id"])
        from .session_closure import ensure_open
        ensure_open(self.api, review["proxy_config_entry_id"] + "|" + review["account"]["session_id"])
        self.exact(review, data)
        if not data.get("confirm_account_review") or not data.get("confirm_driver_details"):
            raise WalletError("Explicit account and independent driver-details review is required")
        if review["state"] != "awaiting_account_approval":
            if review.get("approved_at"):
                return self.public(review)
            raise WalletError("Review is not awaiting approval")
        self.unexpired(review)
        await self.unchanged_source(review)
        self.unexpired(review)
        if review.get("account_kind") != "manual_energy_adjustment" and self.api.saved["driver"] != review["driver"]:
            raise WalletError("Driver details changed; cancel the unsigned review and prepare another")
        review.update(approved_at=now().isoformat(), approved_by=user_id,
                      identity_verification=("registered_wallet_verified_and_operator_approved"
                          if review.get("account_kind") == "manual_energy_adjustment"
                          else "administrator_attested_not_cryptographic"))
        if review["direction"] == "driver_to_operator":
            review["state"] = "awaiting_driver_payment"
            review["payment_request"] = {
                "format": "manual_bsv_payment_request_v1", "network": "BSV mainnet",
                "reference": review["review_id"],
                "ocpp_transaction_id": review["account"]["ocpp_transaction_id"],
                "recipient_address": review["recipient_address"], "amount_sats": review["amount_sats"],
                "expires_at": review["expires_at"], "terms_hash": review["terms_hash"],
                "fee_policy": "Driver approves network fee in their own wallet",
                "instructions": "Use a BSV wallet, check the exact address and amount, then supply transaction ID and output index. The address QR carries no amount or authority. This is not BRC-100/BRC-29 integration.",
            }
        elif review["direction"] == "operator_to_driver":
            review["state"] = "credit_review_approved"
        else:
            review["state"] = "no_payment_due"
        await self.save()
        return self.public(review)

    async def prepare_credit(self, data):
        review = self.get(data["review_id"])
        self.exact(review, data)
        if review["state"] != "credit_review_approved":
            raise WalletError("Approve the negative session account first")
        if review.get("credit_draft_id"):
            draft = self.api.saved["payments"][review["credit_draft_id"]]
            if draft["fee_sats"] != data["fee_sats"]:
                raise WalletError("The frozen credit draft has a different fee")
            return self.public(review)
        self.unexpired(review)
        await self.unchanged_source(review)
        if review.get("account_kind") != "manual_energy_adjustment" and self.api.saved["driver"] != review["driver"]:
            raise WalletError("Driver details changed; the credit recipient is frozen")
        draft = await self.api.prepare_payment({
            "reference": "session-credit:" + review["review_id"],
            "amount_sats": review["amount_sats"], "fee_sats": data["fee_sats"],
            "session_review_id": review["review_id"],
        })
        review["credit_draft_id"] = draft["draft_id"]
        await self.save()
        return self.public(review)

    async def prepare_adjustment_credit(self, data):
        from .fees import quote
        from .energy_adjustment import KIND, MAX_TOTAL
        review = self.get(data["review_id"])
        self.exact(review, data)
        if review.get("account_kind") != KIND:
            raise WalletError("Select a separate energy adjustment")
        if review.get("credit_draft_id"):
            return self.public(review)
        fee = (await quote(self.api.chain))["fee_sats"]
        if review["amount_sats"] + fee > MAX_TOTAL:
            raise WalletError("Adjustment plus quoted fee exceeds 1000 sat")
        return await self.prepare_credit(data | {"fee_sats": fee})

    async def broadcast_credit(self, data, user_id):
        review = self.get(data["review_id"])
        self.exact(review, data)
        if (review["state"] != "credit_review_approved" or not review.get("credit_draft_id")
                or review["credit_draft_id"] != data["draft_id"]):
            raise WalletError("Prepare the matching session credit first")
        payment = self.api.saved["payments"][review["credit_draft_id"]]
        if payment["state"] not in SIGNED_STATES:
            self.unexpired(review)
            await self.unchanged_source(review)
        # The existing wallet verifies exact recipient/amount/fee and administrator
        # confirmation, and persists the signed transaction before network submission.
        try:
            await self.api.broadcast_payment(
                data | {"session_review_id": review["review_id"]}, user_id)
        finally:
            # A crash before this linkage update remains recoverable via draft_id.
            await self.save()
        return self.public(review)

    async def verify_driver_payment(self, data, user_id):
        review = self.get(data["review_id"])
        from .session_closure import ensure_open
        ensure_open(self.api, review["proxy_config_entry_id"] + "|" + review["account"]["session_id"])
        if review["direction"] != "driver_to_operator" or not review.get("payment_request"):
            raise WalletError("Issue the reviewed driver payment request first")
        if data.get("confirm_driver_payment_reference") is not True:
            raise WalletError("Independently confirm the driver supplied this transaction for this request")
        txid, vout = data["txid"], data["output_index"]
        if not re.fullmatch(r"[0-9a-f]{64}", txid) or type(vout) is not int or vout < 0:
            raise WalletError("Invalid transaction ID or output index")
        outpoint = f"{txid}:{vout}"
        owner = self.api.saved["received_outpoints"].get(outpoint)
        if owner and owner != review["review_id"]:
            raise WalletError("This output is already allocated to another session")
        old = review.get("receipt")
        if old and old["outpoint"] != outpoint:
            raise WalletError("A payment output is already bound; reconcile it before any replacement")
        return await self._check_driver_payment(review, txid, vout, outpoint)

    async def reconcile_driver_payment(self, review_id):
        """Recheck an already attributed output without new payer authority."""
        review = self.get(review_id)
        old = review.get("receipt")
        if review.get("direction") != "driver_to_operator" or not old:
            raise WalletError("No attributed driver payment to reconcile")
        try:
            return await self._check_driver_payment(
                review, old["txid"], old["output_index"], old["outpoint"])
        except WalletError:
            if self.api.store.blocked:
                raise
            return self.public(review)

    async def _check_driver_payment(self, review, txid, vout, outpoint):
        old = review.get("receipt")
        try:
            if (not isinstance(txid, str) or not re.fullmatch(r"[0-9a-f]{64}", txid)
                    or type(vout) is not int or vout < 0):
                raise WalletError("Invalid recorded transaction ID or output index")
            created = datetime.fromisoformat(review["created_at"])
            expires = datetime.fromisoformat(review["expires_at"])
            if created.tzinfo is None or expires.tzinfo is None:
                raise WalletError("Invalid recorded payment timestamps")
            after_expiry = now() >= expires
            if old and (outpoint != f"{txid}:{vout}" or
                        self.api.saved["received_outpoints"].get(outpoint) != review["review_id"]):
                raise WalletError("The recorded payment output ownership does not match")
            raw, details = await read_transaction(self.api.chain, txid)
            if review.get("driver_payment_raw") and raw != review["driver_payment_raw"]:
                raise WalletError("Chain evidence differs from the recorded driver payment")
            await self.hass.async_add_executor_job(
                verify_output, raw, txid, vout, review["recipient_address"], review["amount_sats"])
            confirmations = confirmation_count(raw, details, txid)
            # Reject known older transactions. Absent timestamps do not prove freshness.
            block_time = details.get("blocktime", details.get("time"))
            if block_time is not None:
                if type(block_time) not in (int, float) or not math.isfinite(block_time):
                    raise WalletError("Invalid provider transaction timestamp")
                if block_time < created.timestamp():
                    raise WalletError("Provider evidence dates this payment before the request")
        except (WalletError, ValueError, TypeError, OverflowError) as exc:
            if old:
                review["state"] = "driver_payment_evidence_unavailable"
                old.update(verification_error=True, confirmations=None, checked_at=now().isoformat())
                await self.save()
            if isinstance(exc, WalletError):
                raise
            raise WalletError("Invalid recorded payment or provider evidence") from None
        self.api.saved["received_outpoints"][outpoint] = review["review_id"]
        review["driver_payment_raw"] = raw
        review["receipt"] = {
            "outpoint": outpoint, "txid": txid, "output_index": vout,
            "recipient_address": review["recipient_address"], "amount_sats": review["amount_sats"],
            "confirmations": confirmations, "checked_at": now().isoformat(),
            "evidence": "provider-reported transaction, exact output verified; not independent SPV",
            "attribution": "administrator-attested driver reference, not cryptographic payer proof",
            "verified_after_request_expiry": after_expiry,
            "verification_error": False,
        }
        review["state"] = "driver_payment_provider_confirmed" if confirmations else "driver_payment_provider_unconfirmed"
        await self.save()
        return self.public(review)

    async def cancel(self, data):
        review = self.get(data["review_id"])
        if review.get("payment_request") or review["state"] not in (
                "awaiting_account_approval", "credit_review_approved"):
            raise WalletError("Only an unissued driver request or an unsigned operator credit can be cancelled")
        if review.get("credit_draft_id"):
            payment = self.api.saved["payments"][review["credit_draft_id"]]
            if payment.get("txid") or payment["state"] not in ("prepared", "expired", "cancelled"):
                raise WalletError("A signed or submitted credit cannot be cancelled")
            if payment["state"] != "cancelled":
                await self.api.cancel_payment({"draft_id": review["credit_draft_id"]})
        review["state"] = "cancelled"
        await self.save()
        return self.public(review)

    async def execute(self, action, data, user_id):
        if not user_id:
            raise WalletError("An explicit administrator context is required")
        handlers = {
            "pay_energy_adjustment": lambda: self.pay_adjustment(data, user_id),
            "prepare_energy_adjustment": lambda: self.prepare_adjustment(data, user_id),
            "prepare_adjustment_credit": lambda: self.prepare_adjustment_credit(data),
            "prepare_session_review": lambda: self.prepare(data, user_id),
            "approve_session_review": lambda: self.approve(data, user_id),
            "prepare_session_credit": lambda: self.prepare_credit(data),
            "broadcast_session_credit": lambda: self.broadcast_credit(data, user_id),
            "verify_session_driver_payment": lambda: self.verify_driver_payment(data, user_id),
            "cancel_session_review": lambda: self.cancel(data),
        }
        if action == "session_review_status":
            result = self.public(self.get(data["review_id"])) if data.get("review_id") else self.latest()
            if result and data.get("include_tariff_provenance") and "tariff_provenance" in result:
                result["tariff_provenance"] = self.provenance(
                    self.get(result["review_id"]), detail=True)
            return result
        return await handlers[action]()

    async def prepare_adjustment(self, data, user_id):
        from .energy_adjustment import prepare
        return await prepare(self, data, user_id)

    async def pay_adjustment(self, data, user_id):
        from .energy_adjustment import pay
        return await pay(self, data, user_id)
