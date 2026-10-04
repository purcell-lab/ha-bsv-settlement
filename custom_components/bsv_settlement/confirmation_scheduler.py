"""Fair read-only reassessment of already recorded driver transactions.

The coordinator lock serialises this worker with wallet actions. No call to
collection status/claim/report is allowed here: those can advance authority.
"""
from datetime import datetime, timezone

from .api import WalletError

MAX_CHECKS = 2
TICK_SECONDS = 15
PENDING_SECONDS = 60
CONFIRMED_SECONDS = 300
COLLECTION_STATES = {"submitted", "broadcast_unknown", "provider_unconfirmed", "provider_confirmed"}
MANUAL_STATES = {
    "driver_payment_provider_confirmed", "driver_payment_provider_unconfirmed",
    "driver_payment_evidence_unavailable",
}


def utcnow():
    return datetime.now(timezone.utc)


def age(value, now):
    """Malformed, naive or future timestamps are due, never forever deferred."""
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or parsed > now:
            return None
        return (now - parsed).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return None


class DriverConfirmationScheduler:
    def __init__(self, api):
        self.api = api
        api.saved.setdefault("driver_confirmation_schedule", {"cursor": "", "last_run": None})

    def candidates(self):
        for bid, item in self.api.saved.get("driver_collections", {}).items():
            row = self.api.saved.get("session_budgets", {}).get(bid)
            if (row and item.get("txid") and item.get("signed_raw")
                    and item.get("state") in COLLECTION_STATES):
                yield "collection:" + bid, item, row
        for rid, review in self.api.saved.get("session_reviews", {}).items():
            receipt = review.get("receipt")
            if (review.get("direction") == "driver_to_operator" and receipt
                    and receipt.get("txid") and review.get("state") in MANUAL_STATES):
                yield "manual:" + rid, review, None

    async def tick(self):
        if self.api.store.blocked:
            raise WalletError("Wallet ledger checkpoint is blocked; no confirmation reads allowed")
        now = utcnow()
        schedule = self.api.saved["driver_confirmation_schedule"]
        elapsed = age(schedule.get("last_run"), now)
        if elapsed is not None and elapsed < TICK_SECONDS:
            return
        due = []
        for key, item, row in self.candidates():
            evidence = item if row is not None else item["receipt"]
            elapsed = age(evidence.get("checked_at"), now)
            interval = CONFIRMED_SECONDS if item["state"] in (
                "provider_confirmed", "driver_payment_provider_confirmed") else PENDING_SECONDS
            if elapsed is None or elapsed >= interval:
                due.append((key, item, row))
        if not due:
            return
        cursor = schedule.get("cursor")
        cursor = cursor if isinstance(cursor, str) else ""
        due.sort(key=lambda value: (value[0] <= cursor, value[0]))
        selected = due[:MAX_CHECKS]
        # Persist fairness/cooldown before network reads so a failed provider or
        # restart cannot continually push the same first records ahead of others.
        schedule.update(cursor=selected[-1][0], last_run=now.isoformat())
        await self.api.store.async_save(self.api.saved)
        for key, item, row in selected:
            if row is not None:
                await self.api.collections.reconcile(row)
            else:
                await self.api.reviews.reconcile_driver_payment(item["review_id"])
