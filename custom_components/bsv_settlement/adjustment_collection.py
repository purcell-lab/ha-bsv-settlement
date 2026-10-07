"""Wallet-connected adjustments, isolated from weekly/session spending authority.

Only new, explicitly authorised operator-button requests enter this route.
The driver's signed claim binds the exact operator-signed quote and its total
ceiling. The existing collection engine validates drafts and issues one permit.
No driver keys, fabricated meter readings or legacy-request migration.
"""
import copy
import json

from bsv import PrivateKey

from .api import WalletError
from .budget import canonical, message_hash, sha
from .collection import DriverCollections
from .energy_adjustment import KIND, MAX_TOTAL, check_recipient
from .session_review import digest, now

FORMAT = "wallet_connected_energy_adjustment_v1"


class AdjustmentCollections(DriverCollections):
    async def save(self):
        # Keep existing review/audit/summary state machinery aware of every
        # durable transition, including a reserved or uncertain wallet attempt.
        for review in self.api.saved.get("session_reviews", {}).values():
            item = review.get("wallet_collection")
            if review.get("wallet_collection_enabled") and item:
                review["state"] = "driver_payment_" + item["state"]
        await super().save()

    def row(self, review):
        """Internal routing adapter, never represented as a signed budget."""
        return {
            "review_id": review["review_id"],
            "proxy_config_entry_id": review["proxy_config_entry_id"],
            "terms": {"budget_id": "adjustment:" + review["review_id"],
                      "session_id": review["account"]["session_id"]},
            "receipt": {"driver_identity": review["adjustment_recipient"]["driver_identity"]},
        }

    def review(self, row):
        return self.api.reviews.get(row["review_id"])

    def get(self, row):
        return self.review(row).get("wallet_collection")

    def mandate(self, row):
        review = self.review(row)
        item = self.get(row)
        expected_state = "driver_payment_" + item["state"] if item else "awaiting_driver_payment"
        if (not review.get("wallet_collection_enabled")
                or review.get("account_kind") != KIND
                or review.get("direction") != "driver_to_operator"
                or review.get("state") != expected_state
                or not review.get("one_click_authorised_at")
                or not review.get("approved_at")
                or review.get("receipt")
                or (review.get("payment_request") or {}).get("format") != FORMAT):
            raise WalletError("This adjustment is not available for wallet collection")
        self.api.reviews.unexpired(review)
        if (digest(review["frozen_terms"]) != review["terms_hash"]
                or any(review.get(k) != value for k, value in review["frozen_terms"].items()
                       if k != "identity_verification")
                or digest(review["account"]) != review["source_hash"]
                or review["recipient_address"] != self.api.identity["address"]):
            raise WalletError("The frozen adjustment terms changed")
        check_recipient(self.api, review)
        self.manual_conflict(row)

    def manual_conflict(self, row):
        from .session_closure import ensure_open
        ensure_open(self.api, self.key(row))

    async def current(self, row, item):
        self.mandate(row)
        q = json.loads(item["quote"]["payload"])
        review = self.review(row)
        if (q != self.quote_terms(review, q["created_at"])
                or item["source_hash"] != digest(review["account"])):
            raise WalletError("Adjustment quote changed; do not pay")

    def quote_terms(self, review, created_at):
        return {
            "version": 1, "kind": FORMAT, "network": "BSV mainnet",
            "budget_id": "adjustment:" + review["review_id"],
            "review_id": review["review_id"], "terms_hash": review["terms_hash"],
            "driver_identity": review["adjustment_recipient"]["driver_identity"],
            "operator_identity": self.api.identity["public_key"],
            "recipient_address": review["recipient_address"],
            "amount_sats": review["amount_sats"],
            "max_total_sats": MAX_TOTAL,
            "max_fee_sats": MAX_TOTAL - review["amount_sats"],
            "satoshis_per_aud": review["satoshis_per_aud"],
            "price_aud_per_kwh": review["price_aud_per_kwh"],
            "account": copy.deepcopy(review["account"]),
            "expires_at": review["expires_at"], "created_at": created_at,
        }

    async def status(self, row):
        item = self.get(row)
        if item and item["state"] != "ready":
            return self.public(item)  # Never replace a reserved/uncertain attempt.
        self.mandate(row)
        if item:
            await self.current(row, item)
            return self.public(item)
        q = self.quote_terms(self.review(row), now().isoformat())
        payload = canonical(q)
        operator = PrivateKey(bytes.fromhex(self.api.identity["secret_hex"]))
        item = {
            "state": "ready", "created_at": q["created_at"],
            "source_hash": digest(q["account"]),
            "quote": {"payload": payload, "hash": sha(payload),
                      "signature": operator.sign(payload.encode(), hasher=message_hash).hex()},
        }
        self.review(row)["wallet_collection"] = item
        await self.save()
        return self.public(item)


def owner(api, identity, review_id):
    review = api.saved.get("session_reviews", {}).get(review_id)
    if (not review or review.get("account_kind") != KIND
            or not review.get("wallet_collection_enabled")
            or review["adjustment_recipient"]["driver_identity"] != identity):
        raise WalletError("Adjustment unavailable for this wallet")
    return review


def jobs(api, identity):
    worker = AdjustmentCollections(api)
    result = []
    for review in api.saved.get("session_reviews", {}).values():
        if (not review.get("wallet_collection_enabled")
                or review.get("adjustment_recipient", {}).get("driver_identity") != identity):
            continue
        row = worker.row(review)
        payment = worker.get(row)
        if payment and payment["state"] != "ready":
            continue
        try:
            worker.mandate(row)
        except WalletError:
            continue
        result.append({"kind": "adjustment", "review_id": review["review_id"],
                       "budget_id": row["terms"]["budget_id"],
                       "session_id": row["terms"]["session_id"]})
    return result


async def handle(api, identity, data):
    review = owner(api, identity, data.get("review_id"))
    worker = AdjustmentCollections(api)
    row = worker.row(review)
    if (data.get("budget_id") != row["terms"]["budget_id"]
            or data.get("session_id") != row["terms"]["session_id"]
            or data.get("confirm_recovered_attempt")):
        raise WalletError("Adjustment identity mismatch or recovery not authorised")
    action = data["action"]
    if action == "debit_status":
        return {"kind": "adjustment", "collection": await worker.status(row)}
    if action == "debit_claim":
        return await worker.claim(row, data)
    if action == "debit_authorise":
        return await worker.authorise(row, data)
    if action == "debit_report":
        return await worker.report(row, data)
    if action == "debit_failure":
        from .collection_recovery import record_failure
        return await record_failure(worker, row, data)
    raise WalletError("Unsupported adjustment collection action")
