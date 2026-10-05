"""Shared exclusion with legacy routes; no authority creation or activation."""
from .api import WalletError

KEY = "monthly_authorities"
SCHEMA = "monthly-authorities-v1"


def monthly_owner(api, account_key):
    state = api.saved.get(KEY)
    if state is None:
        if KEY in api.saved:
            raise WalletError("Invalid monthly ownership ledger; reconcile storage")
        return None
    if (not isinstance(state, dict) or state.get("schema") != SCHEMA
            or not isinstance(state.get("bindings"), dict)):
        raise WalletError("Invalid monthly ownership ledger; reconcile storage")
    row = state["bindings"].get(account_key)
    if row is not None and (not isinstance(row, dict) or not row.get("authority_id")):
        raise WalletError("Invalid monthly account binding")
    return row


def ensure_no_monthly_owner(api, account_key):
    if monthly_owner(api, account_key) is not None:
        raise WalletError("Monthly authority already owns this account; use its original route")


def ensure_no_legacy_owner(api, account_key):
    for index in ("driver_collection_index", "automatic_credit_index",
                  "session_review_index", "closed_sessions"):
        if account_key in api.saved.get(index, {}):
            raise WalletError("Existing legacy or manual settlement owns this account")
    # Keep receiving routes immutable, even before a credit is prepared.
    if "ongoing:" + account_key in api.saved.get("ongoing_credit_routes", {}):
        raise WalletError("Existing receiving route owns this account")
    for row in api.saved.get("session_budgets", {}).values():
        sid = api.collections.session_id(row)
        if sid and row["proxy_config_entry_id"] + "|" + sid == account_key:
            # Conservative: do not repurpose old invitations/consent automatically.
            raise WalletError("Existing session invitation needs explicit migration")
