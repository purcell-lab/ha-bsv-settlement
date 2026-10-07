"""Authenticated wallet-native registration under the operator's weekly terms.

Only the enabled ongoing-credit site's operator-created weekly template supplies
limits and recorder configuration. Client requests cannot select limits, a site,
recipient or historical account. Offers do not assign or pay anything.
"""
from datetime import datetime
import json

from bsv import PublicKey
from .api import WalletError
from .budget import SPENDING_STATE, message_hash, canonical, sha
from .const import DOMAIN
from .session_review import decimal, now
from .weekly import initial_session, verify_parent, remaining

ACTIONS = ("registration_offer", "registration_read", "registration_accept", "registration_receive")


def registration_context(api, proxy_id, excluding=None):
    return sha(canonical({bid: {k: row.get(k) for k in ("state", "receipt", "credit_destination")}
        for bid, row in api.saved["session_budgets"].items()
        if row is not excluding and row["proxy_config_entry_id"] == proxy_id
        and not row.get("weekly_parent_id") and (row.get("receipt") or row.get("credit_destination"))}))


def template(api):
    policy = api.ongoing_credits.policy
    if not policy.get("enabled") or not api.auto_credits.policy.get("enabled"):
        raise WalletError("Automatic wallet registration requires the enabled operator-credit policy")
    rows = [r for r in api.saved["session_budgets"].values()
            if r["proxy_config_entry_id"] == policy.get("proxy_config_entry_id")
            and r["terms"].get("version") == 3
            and not r.get("portal_driver_identity") and not r.get("weekly_parent_id")
            and r["state"] != "revoked"]
    if not rows:
        raise WalletError("The operator must configure weekly invitation terms first")
    row = max(rows, key=lambda r: r["terms"]["created_at"])
    t, inv = row["terms"], row["invitation"]
    if (json.loads(inv["payload"]) != t or t["operator_identity"] != api.identity["public_key"]
            or not PublicKey(bytes.fromhex(t["operator_identity"])).verify(
                bytes.fromhex(inv["signature"]), inv["payload"].encode(), hasher=message_hash)):
        raise WalletError("Operator weekly terms could not be verified")
    return row


def unassigned(api, proxy_id, sid, excluding=None):
    key = proxy_id + "|" + sid
    if any(key in api.saved.get(index, {}) for index in (
            "driver_collection_index", "automatic_credit_index", "session_review_index", "closed_sessions")):
        return False
    from .monthly_ownership import monthly_owner
    if monthly_owner(api, key) is not None:
        return False
    if any(r["proxy_config_entry_id"] == proxy_id and r["session_id"] == sid
           for r in api.ongoing_credits.routes.values()):
        return False
    proxy = api.hass.data.get(DOMAIN, {}).get(proxy_id)
    record = next((r for r in api.ongoing_credits.records(proxy) if r["session_id"] == sid), None) if proxy else None
    for row in api.saved["session_budgets"].values():
        if row is excluding or row["proxy_config_entry_id"] != proxy_id:
            continue
        initial = row["terms"].get("included_session") or {}
        if (api.collections.session_id(row) == sid or initial.get("session_id") == sid):
            # A signed historical owner stays the owner even after expiry.
            if row.get("receipt") or api.budgets.state(row) not in ("expired", "revoked"):
                return False
        if record and row["terms"].get("version") == 3 and row.get("credit_destination"):
            try:
                started = datetime.fromisoformat(record["opened_at"])
                # Old expired registrations outside this session's time window
                # are not owners of a newer session.
                if not (datetime.fromisoformat(row["credit_destination"]["registered_at"])
                        <= started < datetime.fromisoformat(row["terms"]["expires_at"])):
                    continue
                verify_parent(api, row, active=False)
                return False
            except (WalletError, KeyError, ValueError, TypeError):
                # Malformed prior receiving evidence is a blocker, not permission
                # to redirect a potentially owned session.
                return False
    return True


def scoped(api, identity, bid):
    row = api.saved["session_budgets"].get(bid) if isinstance(bid, str) else None
    if not row or row.get("portal_driver_identity") != identity:
        raise WalletError("This invitation is not for the authenticated wallet")
    return row


def view(api, row):
    return {"invitation": row["invitation"], "state": api.budgets.state(row),
            "prices": api.budgets.prices(row)}


async def offer(api, identity):
    from .portal import ownership
    for row in ownership(api, identity).values():
        if row["terms"].get("version") == 3 and api.budgets.state(row) == SPENDING_STATE:
            try:
                verify_parent(api, row)
            except WalletError:
                continue
            # Never reset the aggregate allowance when it is exhausted.
            return {"state": "existing_approval" if remaining(api, row) > 0 else "budget_exhausted"}
    source = template(api)
    proxy_id, t = source["proxy_config_entry_id"], source["terms"]
    # Reuse an unchanged pending offer; no signature, persistence or new limit.
    pending = [r for r in api.saved["session_budgets"].values()
               if r.get("portal_driver_identity") == identity
               and r["proxy_config_entry_id"] == proxy_id
               and api.budgets.state(r) == "awaiting_driver_consent"]
    if pending:
        return view(api, max(pending, key=lambda r: r["terms"]["created_at"]))
    maximum = min(1000, t["max_total_sats"])
    duration = int((datetime.fromisoformat(t["expires_at"])
                    - datetime.fromisoformat(t["created_at"])).total_seconds() // 60)
    data = {"proxy_config_entry_id": proxy_id, "conversion_rate_entity": t["conversion_rate_entity"],
            "operator_name": t["operator_name"], "operator_contact": t["operator_contact"],
            "multi_session": True, "max_total_sats": maximum,
            "max_fee_sats": min(maximum, t["max_fee_sats"]), "valid_minutes": min(10080, duration)}
    proxy = api.hass.data[DOMAIN][proxy_id]
    await proxy.async_request_refresh()
    record = (proxy.data or {}).get("latest_session")
    if record and unassigned(api, proxy_id, record["session_id"]):
        rate_state = api.hass.states.get(t["conversion_rate_entity"])
        if rate_state is None:
            raise WalletError("Conversion sensor unavailable")
        # Includes only the current/latest retained account, not arbitrary history.
        initial_session(api, proxy_id, record["session_id"], decimal(rate_state.state), maximum)
        data["initial_session_id"] = record["session_id"]
    result = await api.budgets.create(data, "authenticated_wallet_portal",
                                     portal_identity=identity, portal_template=source)
    return view(api, scoped(api, identity, result["terms"]["budget_id"]))


async def handle(api, identity, data):
    if data["action"] == "registration_offer":
        return await offer(api, identity)
    row = scoped(api, identity, data.get("budget_id"))
    if data["action"] == "registration_read":
        return view(api, row)
    source = template(api)
    if row.get("portal_template_hash") != sha(source["invitation"]["payload"]):
        raise WalletError("Operator terms changed. No replacement budget was approved")
    if row.get("portal_registration_context") != registration_context(api, row["proxy_config_entry_id"], row):
        raise WalletError("Driver registration changed during setup; no ownership changed")
    if data["action"] == "registration_receive":
        from .portal import owned
        if not owned(api, row, identity):
            raise WalletError("A verified spending approval is required")
        included = row["terms"].get("included_session")
        route = api.ongoing_credits.routes.get(
            "ongoing:" + row["proxy_config_entry_id"] + "|" + included["session_id"]) if included else None
        same_route = route and route["recipient"] == api.ongoing_credits.verified_registration(row)
        if included and not same_route and not unassigned(api, row["proxy_config_entry_id"], included["session_id"], row):
            raise WalletError("The included session now has an owner")
        result = await api.auto_credits.register(row, data)
        if included and not same_route:
            proxy = api.hass.data[DOMAIN][row["proxy_config_entry_id"]]
            record = next((r for r in api.ongoing_credits.records(proxy)
                           if r["session_id"] == included["session_id"]), None)
            if not record:
                raise WalletError("Included session is unavailable")
            await api.ongoing_credits.assign(record, api.ongoing_credits.verified_registration(row))
        return result
    if data["action"] != "registration_accept":
        raise WalletError("Unsupported registration action")
    receipt = data.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("driver_identity") != identity:
        raise WalletError("Wallet approval must match the authenticated driver")
    included = row["terms"].get("included_session")
    if included and not unassigned(api, row["proxy_config_entry_id"], included["session_id"], row):
        raise WalletError("The included session now has an owner. Nothing was reassigned")
    if not api.budgets.prices(row)["valid"]:
        raise WalletError("Current tariffs unavailable")
    await api.budgets.accept(row, receipt, "authenticated_wallet_portal")
    # Existing same-wallet private capability is used only for receiving proof.
    # It is never displayed publicly and is not needed for portal collection.
    token = api.budgets.link_token(row["terms"]["budget_id"])
    return api.budgets.driver_view(row) | {
        "private_link_fragment": f"#budget={row['terms']['budget_id']}&token={token}"}
