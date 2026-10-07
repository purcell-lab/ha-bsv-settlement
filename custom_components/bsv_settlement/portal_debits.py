"""Owner-scoped transport for the existing guarded collection engine.

Login is not a spending mandate. Every operation requires an independently
verified, previously signed budget; claims and drafts retain their wallet proofs.
No capability tokens, arbitrary wallet calls or operator-payment actions exposed.
"""
import copy

from .api import WalletError
from .budget import SPENDING_STATE
from .weekly import candidates, children, ticket, verify_parent

ACTIONS = (
    "debit_jobs", "debit_status", "debit_claim", "debit_authorise",
    "debit_report", "debit_failure",
)


def owner(api, identity, budget_id):
    from .portal import owned
    row = api.saved["session_budgets"].get(budget_id) if isinstance(budget_id, str) else None
    if not row or row.get("weekly_parent_id") or not owned(api, row, identity):
        raise WalletError("This spending approval does not belong to the signed-in wallet")
    return row


async def jobs(api, identity):
    from .portal import ownership
    from .adjustment_collection import jobs as adjustment_jobs
    result, seen = adjustment_jobs(api, identity), set()
    for bid, row in ownership(api, identity).items():
        if row.get("weekly_parent_id") or api.budgets.state(row) != SPENDING_STATE:
            continue
        if row["terms"].get("version") == 3:
            try:
                verify_parent(api, row)
            except WalletError:
                continue
            routes = await candidates(api, row)
            existing = {r["terms"]["session_id"]: r for r in children(api, row)}
        elif row["terms"].get("version") == 2:
            sid = api.collections.session_id(row)
            routes = [{"session_id": sid}] if sid else []
            existing = {sid: row}
        else:
            continue
        for route in routes:
            sid = route["session_id"]
            key = row["proxy_config_entry_id"] + "|" + sid
            child = existing.get(sid)
            payment = api.collections.get(child) if child else None
            # Already-paid or waived sessions never become another wallet job.
            if key in seen or payment and payment.get("state") != "ready":
                continue
            seen.add(key)
            result.append({"budget_id": bid, "session_id": sid})
    return {"jobs": result[:100], "has_more": len(result) > 100}


async def handle(api, identity, data):
    action = data["action"]
    if action == "debit_jobs":
        return await jobs(api, identity)
    if data.get("kind") == "adjustment":
        from .adjustment_collection import handle as adjustment_handle
        return await adjustment_handle(api, identity, data)
    parent = owner(api, identity, data.get("budget_id"))
    sid = data.get("session_id")
    if not isinstance(sid, str) or not sid or len(sid) > 200:
        raise WalletError("A specific authorised session is required")
    if parent["terms"].get("version") == 3:
        # Ticket creation and all final signing gates use the existing mandate.
        row = await ticket(api, parent, sid)
    elif parent["terms"].get("version") == 2 and api.collections.session_id(parent) == sid:
        row = parent
    else:
        raise WalletError("Session does not match this spending approval")
    if action == "debit_status":
        return {"invitation": copy.deepcopy(parent["invitation"]),
                "session_invitation": copy.deepcopy(row["invitation"]),
                "binding": copy.deepcopy(row.get("binding")),
                "collection": await api.collections.status(row)}
    if action == "debit_claim":
        # Guarded recovery always requires the separate reviewed recovery UI.
        if data.get("confirm_recovered_attempt"):
            raise WalletError("Automatic collection cannot release a recovery hold")
        return await api.collections.claim(row, data)
    if action == "debit_authorise":
        return await api.collections.authorise(row, data)
    if action == "debit_report":
        return await api.collections.report(row, data)
    if action == "debit_failure":
        from .collection_recovery import record_failure
        return await record_failure(api.collections, row, data)
    raise WalletError("Unsupported debit action")
