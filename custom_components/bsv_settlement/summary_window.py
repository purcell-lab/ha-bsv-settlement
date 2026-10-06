"""Dashboard display windows that never hide unresolved records (#105).

Views only: nothing here reads time, writes storage or changes a record.
"Resolved" lists the terminal states of each state machine, where no further
operator or wallet payment action can occur (only optional housekeeping such
as cancelling a dead review may remain). Every other state, including one this
module does not know, is unresolved and stays visible (fail visible).
"""
LIMIT = 20

# Operator credit (auto_credit.py, ongoing routes via credit_recovery.py):
# credit_queued, credit_review_required, broadcast_unknown, submitted,
# provider_unconfirmed -> provider_confirmed.
CREDIT_RESOLVED = frozenset({"provider_confirmed"})
# Ongoing route without an item: waiting_for_session_end, credit_blocked,
# credit_queued, or no_operator_credit (net account >= 0, never re-opened).
ROUTE_RESOLVED = CREDIT_RESOLVED | {"no_operator_credit"}
# Driver collection (collection.py, collection_recovery.py): ready,
# wallet_attempt_reserved, recovery_ready, submission_authorised,
# broadcast_unknown, submitted, provider_unconfirmed -> provider_confirmed.
COLLECTION_RESOLVED = frozenset({"provider_confirmed"})
# Approval states (budget.py) after which no consent, claim or spend occurs.
APPROVAL_TERMINAL = frozenset({"revoked", "expired", "charge_waived"})
# Manual review (session_review.py, energy_adjustment.py) stored states.
REVIEW_RESOLVED = frozenset({"cancelled", "no_payment_due", "driver_payment_provider_confirmed"})
# Unpaid review states that only lapse: approve() and prepare_credit() refuse
# an expired review, so without a driver payment request nothing can move.
REVIEW_LAPSING = frozenset({"awaiting_account_approval", "credit_review_approved"})
# Manual credit draft (mainnet.py payments): prepared, broadcast_unknown,
# submitted, provider_unconfirmed -> provider_confirmed; or never signed and
# dead (expired, cancelled, cancelled_driver_changed). A draft is never replaced.
DRAFT_RESOLVED = frozenset({"provider_confirmed", "expired", "cancelled", "cancelled_driver_changed"})


def window(rows, unresolved, limit=LIMIT):
    """All unresolved rows plus the newest resolved rows filling to `limit`.

    Insertion order is preserved. A predicate that raises counts as unresolved.
    Returns (shown, {"total", "shown", "unresolved"}).
    """
    rows = list(rows)
    flags = []
    for row in rows:
        try:
            flags.append(bool(unresolved(row)))
        except Exception:  # noqa: BLE001 - display must fail visible, never hide.
            flags.append(True)
    resolved = [i for i, flag in enumerate(flags) if not flag]
    room = max(0, limit - (len(rows) - len(resolved)))
    keep = set(resolved[len(resolved) - room:]) if room else set()
    shown = [row for i, (row, flag) in enumerate(zip(rows, flags)) if flag or i in keep]
    return shown, {"total": len(rows), "shown": len(shown), "unresolved": len(rows) - len(resolved)}


def combine(*counts):
    return {k: sum(c[k] for c in counts) for k in ("total", "shown", "unresolved")}


def credit_unresolved(item):
    return item.get("state") not in CREDIT_RESOLVED


def route_unresolved(route, item=None):
    """A route shows its credit item's state once one exists (route_public)."""
    return (item.get("state") not in CREDIT_RESOLVED if item
            else route.get("state") not in ROUTE_RESOLVED)


def collection_unresolved(item, approval_state=None):
    """An unclaimed `ready` quote is dead once its approval is terminal:
    claim requires a live mandate and recovery only follows a reserved attempt."""
    state = item.get("state")
    if state == "ready" and approval_state in APPROVAL_TERMINAL:
        return False
    return state not in COLLECTION_RESOLVED


def review_unresolved(review, payment=None, expired=False):
    """`expired`: the review's own expires_at has passed (caller's clock)."""
    state = review.get("state")
    if state == "cancelled":
        return False
    if review.get("credit_draft_id"):
        return payment is None or payment.get("state") not in DRAFT_RESOLVED
    if expired and state in REVIEW_LAPSING and not review.get("payment_request"):
        return False
    return state not in REVIEW_RESOLVED


def approval_unresolved(state, settlements=(), settled=False):
    """`settlements`: unresolved flags of this approval's collections/credits.
    `settled`: a single-session approval whose own settlement is confirmed."""
    if any(settlements):
        return True
    return not (state in APPROVAL_TERMINAL or settled)
