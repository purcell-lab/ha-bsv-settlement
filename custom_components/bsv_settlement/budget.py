"""Signed session-budget consent. Never grants spending or charger authority."""
import copy
from datetime import timedelta, datetime
import hashlib
import json
import re
import secrets
from uuid import uuid4

from bsv import PrivateKey, PublicKey
from .api import WalletError
from .const import DOMAIN
from .session_review import decimal, now

PROTOCOL = [2, "ha ev session budget"]
PRICING = ("Net AUD account = interval import kWh times import price minus interval "
           "export kWh times feed-in price. Prices may be negative. Round AUD to cents, "
           "then convert at the fixed sat/AUD rate and round half-up to whole satoshis. "
           "DC-meter time allocation is provisional, not a certified bill.")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def message_hash(value):
    """BRC-100 message signatures use one SHA-256, not Bitcoin hash256."""
    return hashlib.sha256(value).digest()


def approval_payload(invitation, identity):
    terms = json.loads(invitation["payload"])
    return canonical({
        "action": "approve_session_budget_consent_only",
        "budget_id": terms["budget_id"],
        "driver_identity": identity,
        "invitation_hash": sha(invitation["payload"]),
        "no_spending_authority": True,
        "version": 1,
    })


class SessionBudgets:
    def __init__(self, api):
        self.api = api
        api.saved.setdefault("session_budgets", {})

    def public(self, row):
        result = copy.deepcopy(row)
        for key in ("proxy_config_entry_id", "session_key", "created_by", "accepted_by", "driver_token_hash"):
            result.pop(key, None)
        if row["state"] != "revoked" and now() >= datetime.fromisoformat(row["terms"]["expires_at"]):
            result["state"] = "expired"
        return result

    def prices(self, row):
        """Current indicative Amber rates, never a fixed session tariff."""
        result = {"checked_at": now().isoformat(), "valid": True}
        for name, field in (("import", "import_price_entity"), ("export", "export_price_entity")):
            state = self.api.hass.states.get(row["terms"][field])
            item = {"available": False}
            try:
                if state is None or state.attributes.get("unit_of_measurement") not in ("$/kWh", "AUD/kWh"):
                    raise ValueError()
                value = decimal(state.state)
                if abs(value) > 1000000:
                    raise ValueError()
                start, end = (state.attributes.get(x) for x in ("start_time", "end_time"))
                start = start if isinstance(start, datetime) else datetime.fromisoformat(start)
                end = end if isinstance(end, datetime) else datetime.fromisoformat(end)
                if (start.tzinfo is None or end.tzinfo is None or end <= start
                        or not start <= now() <= end + timedelta(seconds=90)):
                    raise ValueError()
                item = {"available": True, "aud_per_kwh": str(value),
                        "start": start.isoformat(), "end": end.isoformat(),
                        "estimate": state.attributes.get("estimate") is not False}
            except (ValueError, TypeError, WalletError):
                result["valid"] = False
            result[name] = item
        return result

    def driver_access(self, budget_id, token):
        row = self.api.saved["session_budgets"].get(budget_id)
        if (row is None or not isinstance(token, str) or len(token) != 43
                or not secrets.compare_digest(row.get("driver_token_hash", ""), sha(token))):
            raise WalletError("Invalid driver link")
        if self.public(row)["state"] in ("revoked", "expired"):
            raise WalletError("Driver link is expired or revoked")
        return row

    def driver_view(self, row):
        return {"invitation": copy.deepcopy(row["invitation"]), "state": self.public(row)["state"],
                "prices": self.prices(row),
                "driver_identity": (row.get("receipt") or {}).get("driver_identity"),
                "binding": copy.deepcopy(row.get("binding"))}

    async def execute(self, action, data, user_id):
        if not user_id:
            raise WalletError("An explicit administrator context is required")
        if action == "create_session_budget":
            return await self.create(data, user_id)
        budget_id = data.get("budget_id") or (
            self.api.saved.get("latest_session_budget") if action == "session_budget_status" else None)
        row = self.api.saved["session_budgets"].get(budget_id)
        if not row:
            raise WalletError("Budget invitation not found")
        if action == "session_budget_status":
            return self.public(row)
        if action == "revoke_session_budget":
            row["state"] = "revoked"
            await self.api.store.async_save(self.api.saved)
            return self.public(row)
        if action == "bind_session_budget":
            return await self.bind(row, data)
        if action != "accept_session_budget":
            raise WalletError("Unsupported budget action")
        return await self.accept(row, data["receipt"], user_id)

    async def accept(self, row, receipt, accepted_by):
        if self.public(row)["state"] in ("revoked", "expired"):
            raise WalletError("Budget invitation is expired or revoked")
        try:
            if len(canonical(receipt)) > 10000:
                raise ValueError()
            if set(receipt) != {"version", "budget_id", "driver_identity", "payload", "signature"}:
                raise ValueError()
            identity = receipt["driver_identity"]
            if not re.fullmatch(r"(02|03)[0-9a-f]{64}", identity):
                raise ValueError()
            if receipt["version"] != 1 or receipt["budget_id"] != row["terms"]["budget_id"]:
                raise ValueError()
            expected = approval_payload(row["invitation"], identity)
            if receipt["payload"] != expected:
                raise ValueError()
            # BRC-43 protocol invoice; "anyone" is the public counterparty key 1.
            invoice = f"2-{PROTOCOL[1]}-{row['terms']['budget_id']}"
            key = PublicKey(bytes.fromhex(identity)).derive_child(PrivateKey(1), invoice)
            signature = bytes.fromhex(receipt["signature"])
            if not 8 <= len(signature) <= 72 or not key.verify(signature, expected.encode("utf-8"), hasher=message_hash):
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise WalletError("Invalid session-budget signature or terms") from None
        if row.get("receipt"):
            if row["receipt"]["payload"] != receipt["payload"]:
                raise WalletError("This invitation is already bound to a different driver")
            await self.api.store.async_save(self.api.saved)
            return self.public(row)
        row.update(state="consent_verified_not_payment_authority",
                   receipt=copy.deepcopy(receipt), accepted_at=now().isoformat(), accepted_by=accepted_by)
        await self.api.store.async_save(self.api.saved)
        return self.public(row)

    async def bind(self, row, data):
        if self.public(row)["state"] != "consent_verified_not_payment_authority":
            raise WalletError("An unexpired, verified consent is required")
        if row["terms"].get("session_mode") != "next_session_reservation":
            raise WalletError("This consent already names a recorded session")
        if data.get("confirm_driver_present") is not True:
            raise WalletError("Confirm this is the consenting driver's session")
        proxy = self.api.hass.data.get(DOMAIN, {}).get(row["proxy_config_entry_id"])
        if proxy is None or proxy.mode != "sensor_proxy":
            raise WalletError("The original session recorder is unavailable")
        await proxy.async_request_refresh()
        records = [(proxy.data or {}).get("latest_session"), (proxy.data or {}).get("previous_session"), *proxy.archive]
        record = next((r for r in records if r and r["session_id"] == data["session_id"]), None)
        if record is None:
            raise WalletError("Session not found on the authorised recorder")
        if not datetime.fromisoformat(row["accepted_at"]) <= datetime.fromisoformat(record["opened_at"]) < datetime.fromisoformat(row["terms"]["expires_at"]):
            raise WalletError("Session must open after consent and before expiry")
        binding = {"session_id": record["session_id"], "transaction_id": record["ocpp_transaction_id"]}
        if row.get("binding"):
            if row["binding"] != binding:
                raise WalletError("Consent is already bound to another session")
            return self.public(row)
        for other in self.api.saved["session_budgets"].values():
            if other is not row and other.get("binding") == binding and other["proxy_config_entry_id"] == row["proxy_config_entry_id"]:
                raise WalletError("Session already has a bound consent")
        row["binding"] = binding
        await self.api.store.async_save(self.api.saved)
        return self.public(row)

    async def create(self, data, user_id):
        proxy = self.api.hass.data.get(DOMAIN, {}).get(data["proxy_config_entry_id"])
        if proxy is None or proxy.mode != "sensor_proxy":
            raise WalletError("Select the loaded session recorder")
        await proxy.async_request_refresh()
        observations = proxy.data or {}
        candidates = [observations.get("latest_session"), observations.get("previous_session"), *proxy.archive]
        pre_session = not data.get("session_id")
        record = next((r for r in candidates if r and r["session_id"] == data.get("session_id")), None)
        if record is None and not pre_session:
            raise WalletError("Session is not in retained recorder history")
        if record and record.get("ended_at"):
            raise WalletError("Choose an open session; do not backdate budget consent")
        if observations.get("issues"):
            raise WalletError("Resolve recorder issues before inviting the driver")
        reservation = str(uuid4())
        key = f"{data['proxy_config_entry_id']}:{data.get('session_id') or 'next_session'}"
        for old in self.api.saved["session_budgets"].values():
            if old["session_key"] == key and not old.get("binding") and self.public(old)["state"] not in ("revoked", "expired"):
                return self.public(old)
        state = self.api.hass.states.get(data["conversion_rate_entity"])
        if state is None or state.attributes.get("unit_of_measurement") != "sat/AUD":
            raise WalletError("Select a positive conversion-rate sensor in sat/AUD")
        rate = decimal(state.state)
        if not 0 < rate <= 100000000:
            raise WalletError("Invalid conversion rate")
        maximum, fee = data.get("max_total_sats", 1000), data.get("max_fee_sats", 10)
        if type(maximum) is not int or not 1 <= maximum <= 100000 or type(fee) is not int or not 0 <= fee < maximum or fee > 1000:
            raise WalletError("Budget must exceed its fee allowance and stay within demonstration limits")
        minutes = data.get("valid_minutes", 720)
        if type(minutes) is not int or not 1 <= minutes <= 1440:
            raise WalletError("Expiry must be between one minute and 24 hours")
        budget_id = str(uuid4())
        sources = proxy.sources
        terms = {
            "version": 1, "budget_id": budget_id,
            "session_id": "reservation:" + reservation if pre_session else record["session_id"],
            "transaction_id": "Assigned after charging session opens" if pre_session else record["ocpp_transaction_id"],
            "session_mode": "next_session_reservation" if pre_session else "existing_session",
            "network": "BSV mainnet",
            "operator_name": data.get("operator_name", "Charging operator"),
            "operator_contact": data.get("operator_contact", ""),
            "operator_identity": self.api.identity["public_key"],
            "operator_address": self.api.identity["address"],
            "max_total_sats": maximum, "max_fee_sats": fee,
            "satoshis_per_aud": str(rate), "conversion_rate_entity": data["conversion_rate_entity"],
            "pricing_rule": PRICING, "created_at": now().isoformat(),
            "import_price_entity": sources["import_price"],
            "export_price_entity": sources["export_price"],
            "account_scope": ("One future session on this charger, opened after consent and before expiry. "
                              "Operator must confirm the driver and bind the transaction ID. No automatic selection."
                              if pre_session else "Entire named session, including energy already recorded before consent."),
            "expires_at": (now() + timedelta(minutes=minutes)).isoformat(),
            "scope": "one_session_consent_only_no_payment_or_charger_authority",
        }
        payload = canonical(terms)
        operator = PrivateKey(bytes.fromhex(self.api.identity["secret_hex"]))
        invitation = {"version": 1, "payload": payload,
                      "signature": operator.sign(payload.encode(), hasher=message_hash).hex()}
        token = secrets.token_urlsafe(32)
        row = {"terms": terms, "invitation": invitation, "state": "awaiting_driver_consent",
               "session_key": key, "proxy_config_entry_id": data["proxy_config_entry_id"],
               "created_by": user_id, "receipt": None, "driver_token_hash": sha(token)}
        self.api.saved["session_budgets"][budget_id] = row
        self.api.saved["latest_session_budget"] = budget_id
        await self.api.store.async_save(self.api.saved)
        return self.public(row) | {"driver_link_fragment": f"#budget={budget_id}&token={token}"}
