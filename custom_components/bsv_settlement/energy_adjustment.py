"""Explicit 5 kWh-equivalent adjustments, never metered energy or charging consent."""
import copy
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID, uuid4

from .api import WalletError
from .const import DOMAIN
from .session_review import decimal, digest, now

KIND = "manual_energy_adjustment"
MAX_TOTAL = 1000


def current_recipient(api, proxy_id):
    rows = [r for r in api.saved["session_budgets"].values()
            if r.get("credit_destination") and r.get("proxy_config_entry_id") == proxy_id]
    if not rows:
        raise WalletError("Register the current driver's wallet before preparing an adjustment")
    try:
        row = max(rows, key=lambda r: datetime.fromisoformat(r["credit_destination"]["registered_at"]))
        if row["terms"].get("version") == 3 and now() >= datetime.fromisoformat(row["terms"]["expires_at"]):
            raise WalletError("Current driver registration expired")
        return api.ongoing_credits.verified_registration(row)
    except (ValueError, TypeError, KeyError):
        raise WalletError("Current driver registration is invalid; no older driver fallback") from None


def check_recipient(api, review):
    if current_recipient(api, review["proxy_config_entry_id"]) != review["adjustment_recipient"]:
        raise WalletError("Current driver changed; cancel this unsigned adjustment and review again")


def payment_driver(api, review):
    check_recipient(api, review)
    return copy.deepcopy(review["driver"])


async def prepare(reviews, data, user_id):
    api, hass = reviews.api, reviews.hass
    if data["energy_direction"] not in ("import", "export"):
        raise WalletError("Select import or export")
    try:
        UUID(data["request_id"])
    except (ValueError, TypeError, AttributeError):
        raise WalletError("A stable adjustment request UUID is required") from None
    index = api.saved.setdefault("energy_adjustment_requests", {})
    scope = {k: data[k] for k in ("proxy_config_entry_id", "energy_direction", "conversion_rate_entity")}
    if data["request_id"] in index:
        review = reviews.get(index[data["request_id"]])
        if review["adjustment_scope"] != scope:
            raise WalletError("Adjustment request ID was already used with different terms")
        return reviews.public(review)  # Retry returns frozen terms, even after expiry.
    recipient = current_recipient(api, data["proxy_config_entry_id"])
    # A lost response/new tab must not create a second outstanding obligation.
    for old in api.saved["session_reviews"].values():
        if (old.get("account_kind") == KIND and old.get("adjustment_scope") == scope
                and old.get("adjustment_recipient") == recipient):
            status = reviews.public(old)["state"]
            if status not in ("cancelled", "no_payment_due", "credit_provider_confirmed",
                              "driver_payment_provider_confirmed"):
                index[data["request_id"]] = old["review_id"]
                await reviews.save()
                return reviews.public(old)
    proxy = hass.data.get(DOMAIN, {}).get(data["proxy_config_entry_id"])
    if proxy is None or proxy.mode != "sensor_proxy":
        raise WalletError("Select the loaded settlement recorder for its configured prices")
    entity = proxy.sources[data["energy_direction"] + "_price"]
    state = hass.states.get(entity)
    try:
        if state is None or state.attributes.get("unit_of_measurement") not in ("$/kWh", "AUD/kWh"):
            raise ValueError()
        start, end = (state.attributes.get(k) for k in ("start_time", "end_time"))
        start = start if isinstance(start, datetime) else datetime.fromisoformat(start)
        end = end if isinstance(end, datetime) else datetime.fromisoformat(end)
        if start.tzinfo is None or end.tzinfo is None or not start <= now() < end:
            raise ValueError()
        price = decimal(state.state)
        if abs(price) > 1000000:
            raise ValueError()
    except (ValueError, TypeError):
        raise WalletError("Current tariff is unavailable, stale or has invalid units") from None
    rate_state = hass.states.get(data["conversion_rate_entity"])
    if rate_state is None or rate_state.attributes.get("unit_of_measurement") != "sat/AUD":
        raise WalletError("Select a valid sat/AUD conversion sensor")
    rate = decimal(rate_state.state)
    if not 0 < rate <= 100000000:
        raise WalletError("Invalid conversion rate")
    aud = Decimal(5) * price * (1 if data["energy_direction"] == "import" else -1)
    sats = int((abs(aud) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    if aud == 0 or not 1 <= sats < MAX_TOTAL:
        raise WalletError("Adjustment must be at least one sat and below the 1000 sat total limit")
    direction = "driver_to_operator" if aud > 0 else "operator_to_driver"
    review_id = str(uuid4())
    timestamp = now().isoformat()
    reference = "adjustment:" + review_id
    # Null measured energy is deliberate. The 5 kWh is an adjustment basis only.
    account = {"session_id": reference, "ocpp_transaction_id": reference,
               "transaction_id_source": KIND, "opened_at": timestamp, "ended_at": timestamp,
               "import_kwh": None, "export_kwh": None, "net_amount_aud": str(aud),
               "net_cost_aud_unrounded": str(aud), "currency": "AUD",
               "quality_flags": [KIND], "adjustment_kwh": "5",
               "adjustment_direction": data["energy_direction"]}
    driver = {"driver_public_identity": recipient["driver_identity"],
              "driver_receive_address": recipient["address"]}
    terms = {"review_id": review_id, "network": "BSV mainnet", "account_kind": KIND,
             "account": account, "direction": direction, "amount_sats": sats,
             "recipient_address": recipient["address"] if aud < 0 else api.identity["address"],
             "driver": driver, "adjustment_recipient": recipient,
             "conversion_rate_entity": data["conversion_rate_entity"], "satoshis_per_aud": str(rate),
             "price_entity": entity, "price_aud_per_kwh": str(price),
             "price_observed_at": state.last_updated.isoformat(),
             "price_start": start.isoformat(), "price_end": end.isoformat(),
             "price_estimated": state.attributes.get("estimate") is not False,
             "created_at": timestamp, "expires_at": (now() + timedelta(minutes=10)).isoformat(),
             "identity_verification": "registered_wallet_verified",
             "fee_policy": "Separate reviewed payment; maximum 1000 sat operator spend including fee",
             "payment_authority": "manual_adjustment_only_not_charging_consent"}
    review = terms | {"frozen_terms": copy.deepcopy(terms), "terms_hash": digest(terms),
                      "source_hash": digest(account), "adjustment_scope": scope,
                      "proxy_config_entry_id": data["proxy_config_entry_id"], "created_by": user_id,
                      "state": "awaiting_account_approval", "payment_request": None,
                      "credit_draft_id": None, "receipt": None}
    api.saved["session_reviews"][review_id] = review
    api.saved["latest_session_review"] = review_id
    index[data["request_id"]] = review_id
    await reviews.save()
    return reviews.public(review)


def credit_item(api, credit_id):
    """Exact stored adjustment credit, including after a new driver registers."""
    review = api.saved["session_reviews"].get(credit_id.removeprefix("adjustment:"))
    if not review or review.get("account_kind") != KIND:
        raise WalletError("Adjustment credit unavailable")
    item = api.saved["payments"].get(review.get("credit_draft_id"))
    if not item or item.get("budget_id") != credit_id:
        raise WalletError("Adjustment credit unavailable")
    return review, item
