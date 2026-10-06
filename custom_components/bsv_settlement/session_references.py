"""Wallet-side, read-only: proxy sessions an unresolved wallet record still needs.

The sensor proxy asks loaded wallet coordinators for these IDs before pruning
its summary archive (#106). Nothing here changes a record. Anything not
positively terminal, or not parseable, counts as unresolved: over-pinning only
retains a summary for longer.
"""
from datetime import datetime

from . import session_review

REVIEW_TERMINAL = frozenset({"cancelled", "no_payment_due", "driver_payment_provider_confirmed"})
PAYMENT_TERMINAL = frozenset({"provider_confirmed"})
COLLECTION_TERMINAL = frozenset({"provider_confirmed", "waived"})
CREDIT_TERMINAL = frozenset({"provider_confirmed", "waived"})
ROUTE_TERMINAL = frozenset({"provider_confirmed", "no_operator_credit"})
BUDGET_TERMINAL = frozenset({"revoked", "charge_waived"})


def _split(key, entry_id):
    prefix = entry_id + "|"
    return key[len(prefix):] if isinstance(key, str) and key.startswith(prefix) else None


def _budget_dead(row):
    try:
        return (row.get("state") in BUDGET_TERMINAL
                or session_review.now() >= datetime.fromisoformat(row["terms"]["expires_at"]))
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def unresolved_proxy_sessions(api, entry_id):
    """Session IDs on proxy ``entry_id`` referenced by a non-terminal record."""
    saved = api.saved
    found = set()
    payments = saved.get("payments") or {}
    budgets = saved.get("session_budgets") or {}
    for review in (saved.get("session_reviews") or {}).values():
        if (review.get("proxy_config_entry_id") != entry_id
                or review.get("account_kind") == "manual_energy_adjustment"):
            continue
        sid = (review.get("account") or {}).get("session_id")
        payment = payments.get(review.get("credit_draft_id")) if review.get("credit_draft_id") else None
        if sid and review.get("state") not in REVIEW_TERMINAL and not (
                payment and payment.get("state") in PAYMENT_TERMINAL):
            found.add(sid)
    for row in budgets.values():
        if row.get("proxy_config_entry_id") != entry_id:
            continue
        try:
            sid = api.collections.session_id(row)
        except (KeyError, TypeError, AttributeError):
            sid = None
        if not sid or _budget_dead(row):
            continue
        bid = (row.get("terms") or {}).get("budget_id")
        settled = [items.get(bid) for items in (
            saved.get("driver_collections") or {}, saved.get("automatic_credits") or {})]
        if not any(item and item.get("state") in COLLECTION_TERMINAL | CREDIT_TERMINAL
                   for item in settled):
            found.add(sid)
    for key, bid in (saved.get("driver_collection_index") or {}).items():
        sid = _split(key, entry_id)
        item = (saved.get("driver_collections") or {}).get(bid)
        if sid is None or (item and item.get("state") in COLLECTION_TERMINAL):
            continue
        # A never-started attempt dies with its consent.
        if item and item.get("state") == "ready" and bid in budgets and _budget_dead(budgets[bid]):
            continue
        found.add(sid)
    for key, owner in (saved.get("automatic_credit_index") or {}).items():
        sid = _split(key, entry_id)
        item = (saved.get("automatic_credits") or {}).get(owner)
        if sid is not None and not (item and item.get("state") in CREDIT_TERMINAL):
            found.add(sid)
    for key, route in (saved.get("ongoing_credit_routes") or {}).items():
        sid = _split(key.removeprefix("ongoing:") if isinstance(key, str) else key, entry_id)
        if sid is not None and (route or {}).get("state") not in ROUTE_TERMINAL:
            found.add(sid)
    monthly = saved.get("monthly_authorities")
    if isinstance(monthly, dict) and isinstance(monthly.get("bindings"), dict):
        # Monthly bindings carry no settled state here; keep them all.
        found.update(sid for key in monthly["bindings"] if (sid := _split(key, entry_id)))
    return found
