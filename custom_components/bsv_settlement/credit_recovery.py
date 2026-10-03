"""Prepare-only recovery of an original standing-credit route.

No durable approval enables the background worker. Only an exact authenticated
broadcast call gets a temporary permit; signed outcomes use the existing outbox.
"""
import copy
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP

from .api import WalletError
from .auto_credit import AutomaticCredits, MAX_TOTAL, PENDING
from .fees import quote, validate
from .mainnet import build_transaction
from .session_review import account_snapshot, decimal, digest, now


class OperatorCreditRecovery:
    def __init__(self, api):
        self.api = api
        self._permit = None

    def route(self, credit_id):
        route = self.api.ongoing_credits.routes.get(credit_id)
        if route is None:
            raise WalletError("Select an existing original operator-credit route")
        return route

    def registration(self, route):
        row = self.api.saved["session_budgets"].get(route["recipient"]["budget_id"])
        if row is None or self.api.ongoing_credits.verified_registration(row) != route["recipient"]:
            raise WalletError("Original receiving registration changed")
        if row["terms"].get("version") == 3:
            from .weekly import verify_parent
            verify_parent(self.api, row)
        return row

    def route_hash(self, route):
        return digest({k: route[k] for k in (
            "route_id", "proxy_config_entry_id", "session_id", "transaction_id",
            "recipient", "satoshis_per_aud", "policy_enabled_at")})

    async def snapshot(self, route):
        self.registration(route)
        worker = self.api.ongoing_credits
        wrapper = worker.wrapper(route)
        worker.conflict(wrapper)
        record = await self.api.collections.source(wrapper)
        account = account_snapshot(record)
        if (account["ocpp_transaction_id"] != route["transaction_id"]
                or datetime.fromisoformat(account["ended_at"]) > now()):
            raise WalletError("Original session transaction or end time is invalid")
        rate = decimal(route["satoshis_per_aud"])
        if not 0 < rate <= 100000000:
            raise WalletError("Original conversion rate is invalid")
        amount = int((-decimal(account["net_amount_aud"]) * rate).quantize(
            decimal("1"), rounding=ROUND_HALF_UP))
        if amount < 1 or amount >= MAX_TOTAL:
            raise WalletError("Recovery must be a credit within the 1000 sat total cap")
        self.registration(route)
        return account, amount

    def public(self, route):
        recovery = route.get("manual_recovery")
        if not recovery:
            raise WalletError("Prepare an original-recipient credit review first")
        item = self.api.ongoing_credits.get(self.api.ongoing_credits.wrapper(route))
        return {**copy.deepcopy(recovery["terms"]),
                "review_hash": recovery["review_hash"],
                "state": item["state"],
                "txid": item.get("txid"),
                "confirmations": item.get("confirmations"),
                "error": item.get("error"),
                "funds_reserved": False,
                "automatic_broadcast": False}

    async def prepare(self, data, user_id):
        if not user_id:
            raise WalletError("An authenticated administrator must prepare recovery")
        route = self.route(data["credit_id"])
        if route.get("manual_recovery"):
            old = route["manual_recovery"]
            replacement = data.get("replace_expired_review_hash")
            if replacement is None:
                return self.public(route)  # Never silently renew or replace a review.
            if (replacement != old["review_hash"]
                    or now() < datetime.fromisoformat(old["terms"]["expires_at"])):
                raise WalletError("Only the exact expired unsigned review can be renewed")
        elif data.get("replace_expired_review_hash") is not None:
            raise WalletError("There is no expired recovery review to renew")
        worker = self.api.ongoing_credits
        wrapper = worker.wrapper(route)
        item = worker.get(wrapper)
        if item and (item.get("txid") or item.get("signed_raw")):
            raise WalletError("A signed credit exists; reconcile it without replacement")
        before = self.route_hash(route)
        account, amount = await self.snapshot(route)
        if item and item.get("source_hash") != digest(account):
            raise WalletError("Frozen credit account changed; do not replace it")
        if worker.pending() or any(p["state"] in ("prepared", *PENDING)
                                   for p in self.api.saved["payments"].values()):
            raise WalletError("Another operator payment is unresolved")
        quotation = await AutomaticCredits.quote_fee(worker, wrapper, amount)
        fee = quotation["fee_sats"]
        source, raw = await AutomaticCredits.funding(worker, wrapper, amount, fee)
        checked = await self.api.hass.async_add_executor_job(
            build_transaction, self.api.identity["secret_hex"], raw, source["tx_pos"],
            route["recipient"]["address"], amount, fee, False)
        if (checked["source_txid"] != source["tx_hash"]
                or checked["source_value"] != source["value"]):
            raise WalletError("Funding evidence does not match the raw transaction")
        current, current_amount = await self.snapshot(route)
        if digest(current) != digest(account) or current_amount != amount or before != self.route_hash(route):
            raise WalletError("Original account or recipient changed during preparation")
        validate(quotation, fee)
        terms = {
            "credit_id": route["route_id"], "session_id": route["session_id"],
            "transaction_id": route["transaction_id"],
            "receiving_budget_id": route["recipient"]["budget_id"],
            "recipient_address": route["recipient"]["address"],
            "amount_sats": amount, "fee_sats": fee, "total_sats": amount + fee,
            "fee_quote": quotation,
            "satoshis_per_aud": route["satoshis_per_aud"], "account": account,
            "route_hash": before, "source_txid": source["tx_hash"],
            "source_index": source["tx_pos"], "source_value": source["value"],
            "change_sats": checked["change_sats"],
            "created_at": now().isoformat(),
            "expires_at": (now() + timedelta(minutes=10)).isoformat(),
        }
        recovery = {"terms": terms, "review_hash": digest(terms),
                    "unsigned_raw": checked["raw"], "prepared_by": user_id}
        previous_route = copy.deepcopy(route)
        previous_item = copy.deepcopy(item)
        key = self.api.collections.key(wrapper)
        previous_owner = self.api.saved["automatic_credit_index"].get(key)
        if item is None:
            item = {
                "budget_id": route["route_id"], "session_id": route["session_id"],
                "transaction_id": route["transaction_id"],
                "recipient_address": terms["recipient_address"], "amount_sats": amount,
                "fee_sats": fee, "net_amount_aud": account["net_amount_aud"],
                "account": copy.deepcopy(account), "source_hash": digest(account),
                "created_at": terms["created_at"],
                "policy_enabled_at": worker.policy.get("enabled_at"),
            }
        item.update(state="credit_review_required", error=None, fee_sats=fee, fee_quote=quotation)
        if route.get("manual_recovery"):
            route.setdefault("manual_recovery_history", []).append(copy.deepcopy(route["manual_recovery"]))
        route.update(manual_recovery=recovery, state="credit_review_required")
        route.pop("error", None)
        self.api.saved["automatic_credits"][route["route_id"]] = item
        self.api.saved["automatic_credit_index"][key] = route["route_id"]
        try:
            await worker.save()
        except Exception:
            route.clear()
            route.update(previous_route)
            if previous_item is None:
                self.api.saved["automatic_credits"].pop(route["route_id"], None)
            else:
                self.api.saved["automatic_credits"][route["route_id"]] = previous_item
            if previous_owner is None:
                self.api.saved["automatic_credit_index"].pop(key, None)
            else:
                self.api.saved["automatic_credit_index"][key] = previous_owner
            raise
        return self.public(route)

    def guard(self, wrapper):
        route = self.route(wrapper["standing_route_id"])
        recovery = route.get("manual_recovery") or {}
        terms = recovery.get("terms") or {}
        if (self._permit != (route["route_id"], recovery.get("review_hash"))
                or not terms or digest(terms) != recovery.get("review_hash")):
            raise WalletError("Historical credit requires separate exact approval")
        if now() >= datetime.fromisoformat(terms["expires_at"]):
            raise WalletError("Original-recipient credit review expired")
        if (not self.api.auto_credits.policy.get("enabled")
                or self.api.entry.data.get("enable_broadcast") is not True):
            raise WalletError("Operator broadcast or master credit policy is disabled")
        if self.route_hash(route) != terms["route_hash"]:
            raise WalletError("Original credit route changed after review")
        self.registration(route)
        item = self.api.ongoing_credits.get(wrapper)
        if (item is None or item["source_hash"] != digest(terms["account"])
                or any(item[k] != terms[k] for k in (
                    "recipient_address", "amount_sats", "fee_sats", "session_id", "transaction_id"))):
            raise WalletError("Frozen recovery payment changed")

    async def quote_fee(self, wrapper, amount):
        self.guard(wrapper)
        route = self.route(wrapper["standing_route_id"])
        fee = route["manual_recovery"]["terms"]["fee_sats"]
        quotation = await quote(self.api.chain)
        validate(quotation, fee)
        if amount + fee > MAX_TOTAL:
            raise WalletError("Reviewed credit exceeds the total cap")
        return quotation | {"minimum_fee_sats": quotation["fee_sats"], "fee_sats": fee}

    async def funding(self, wrapper, amount, fee):
        self.guard(wrapper)
        route = self.route(wrapper["standing_route_id"])
        terms = route["manual_recovery"]["terms"]
        if fee != terms["fee_sats"]:
            raise WalletError("Reviewed credit fee changed")
        worker = self.api.ongoing_credits
        used = worker.used() | {(p["source_txid"], p["source_index"])
                               for p in self.api.saved["payments"].values() if p.get("txid")}
        rows = await self.api.chain.unspent(self.api.identity["address"])
        source = next((r for r in rows if (
            r["tx_hash"], r["tx_pos"], r["value"]) == (
            terms["source_txid"], terms["source_index"], terms["source_value"])), None)
        if source is None or (terms["source_txid"], terms["source_index"]) in used:
            raise WalletError("Reviewed funding output is no longer available")
        raw = await self.api.chain.source(source["tx_hash"])
        checked = await self.api.hass.async_add_executor_job(
            build_transaction, self.api.identity["secret_hex"], raw, source["tx_pos"],
            terms["recipient_address"], amount, fee, False)
        if checked["raw"] != route["manual_recovery"]["unsigned_raw"]:
            raise WalletError("Reviewed unsigned transaction changed")
        return source, raw

    async def broadcast(self, data, user_id):
        if not user_id or data.get("confirm_mainnet_payment") is not True:
            raise WalletError("Explicit administrator mainnet payment approval required")
        route = self.route(data["credit_id"])
        recovery = route.get("manual_recovery") or {}
        terms = recovery.get("terms") or {}
        if data.get("expected_review_hash") != recovery.get("review_hash"):
            raise WalletError("Recovery review hash changed")
        for key in ("recipient_address", "amount_sats", "fee_sats"):
            if key not in terms or data.get(key) != terms[key]:
                raise WalletError("Approval does not match the original-recipient credit")
        worker = self.api.ongoing_credits
        wrapper = worker.wrapper(route)
        item = worker.get(wrapper)
        if item and item.get("txid"):
            return self.public(route)  # No new signature or submission, even after expiry.
        self._permit = (route["route_id"], recovery["review_hash"])
        try:
            self.guard(wrapper)
            recovery["approved_by"] = user_id
            recovery["approved_at"] = now().isoformat()
            await worker.save()
            await worker.process(wrapper)
        finally:
            self._permit = None
            item = worker.get(wrapper)
            if item:
                route["state"] = item["state"]
            await worker.save()
        return self.public(route)
