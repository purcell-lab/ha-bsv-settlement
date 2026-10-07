"""Additive observation timestamps; never payment authority or chain finality."""
from datetime import datetime, timezone

FIELDS = (
    "broadcast_attempted_at",
    "broadcast_acknowledged_at",
    "provider_first_confirmed_at",
    "wallet_accepted_reported_at",
)


def stamp(item, field):
    """Record the first observation only; persist using the caller's save boundary."""
    if field not in FIELDS:
        raise ValueError("Unsupported payment event")
    if not item.get(field):
        item[field] = datetime.now(timezone.utc).isoformat()


def confirmed(item, confirmations):
    """Do not invent an original confirmation time for already-confirmed legacy rows."""
    if type(confirmations) is int and confirmations > 0 and item.get("state") != "provider_confirmed":
        stamp(item, "provider_first_confirmed_at")


def public(item):
    """Expose only event times, including an already-recorded signed receipt report."""
    item = item or {}
    result = {field: item.get(field) for field in FIELDS}
    result["wallet_accepted_reported_at"] = (
        (item.get("wallet_receipt_ack") or {}).get("reported_at")
        or result["wallet_accepted_reported_at"]
    )
    return result
