"""Versioned driver mandates. Message approval is not wallet transaction permission."""
import asyncio
import copy
from datetime import timedelta, datetime
import hashlib
import hmac
import json
import re
import secrets
import base64
from uuid import uuid4

from bsv import PrivateKey, PublicKey
from .api import WalletError
from .const import DOMAIN
from .session_review import decimal, now

PROTOCOL = [2, "ha ev session budget"]
SPENDING_PROTOCOL = [2, "ev session spending"]
SPENDING_SCOPE = "one_session_capped_spending_no_charger_authority"
SPENDING_STATE = "spending_authorised_wallet_permission_required"
LEGACY_STATE = "consent_verified_not_payment_authority"
PRICING = ("Net AUD account = interval charging kWh times charging rate minus interval "
           "export kWh times export (V2G) rate. A positive charging rate debits your wallet; "
           "a negative charging rate credits it. A positive export (V2G) rate credits your "
           "wallet; a negative export (V2G) rate debits it. A zero rate has no energy cost. "
           "Combine all intervals into one final net account. Round AUD to cents, "
           "then convert at the fixed sat/AUD rate and round half-up to whole satoshis. "
           "DC-meter time allocation is provisional, not a certified bill.")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def message_hash(value):
    """BRC-100 message signatures use one SHA-256, not Bitcoin hash256."""
    return hashlib.sha256(value).digest()

def payment_authority(terms):
    """Explicit signed mandate, limited to a single final net driver debit."""
    result = {
        "trigger": "after_bound_session_ends",
        "collection_mode": "automatic_once_when_wallet_available",
        "direction": "driver_to_operator_if_net_account_positive",
        "recipient_address": terms["operator_address"],
        "operator_identity": terms["operator_identity"],
        "session_id": terms["session_id"],
        "max_payments": 1,
        "max_total_sats_including_fees": terms["max_total_sats"],
        "max_fee_sats": terms["max_fee_sats"],
        "satoshis_per_aud": terms["satoshis_per_aud"],
        "expires_at": terms["expires_at"],
        "revocable_before_submission": True,
        "wallet_transaction_permission_required": True,
        "operator_credit_requires_separate_authority": True,
        "funds_reserved": False,
        "charger_control": False,
    }
    if terms.get("version") == 3:
        result.update(trigger="each_closed_session_opened_after_receiving_registration",
            max_payments=None, aggregate_limit=True,
            terminates_on_new_driver_registration=True, credits_replenish_budget=False,
            receiving_expires_at=terms["expires_at"])
        if terms.get("included_session"):
            result.update(trigger="explicit_current_session_and_future_sessions_after_registration",
                          included_session_id=terms["included_session"]["session_id"])
    return result


def signature_protocol(terms):
    return SPENDING_PROTOCOL if terms.get("version") in (2, 3) else PROTOCOL


def approval_payload(invitation, identity):
    terms = json.loads(invitation["payload"])
    if terms.get("version") in (2, 3):
        from .weekly import SCOPE
        if terms.get("scope") != (SCOPE if terms["version"] == 3 else SPENDING_SCOPE) or terms.get("payment_authority") != payment_authority(terms):
            raise WalletError("Invalid spending mandate terms")
        return canonical({
            "action": "authorise_multi_session_aggregate_spending" if terms["version"] == 3 else "authorise_one_session_spending",
            "budget_id": terms["budget_id"],
            "driver_identity": identity,
            "invitation_hash": sha(invitation["payload"]),
            "payment_authority": terms["payment_authority"],
            "version": terms["version"],
        })
    if terms.get("version") != 1 or terms.get("scope") != "one_session_consent_only_no_payment_or_charger_authority":
        raise WalletError("Unsupported approval version")
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

    def summary(self):
        """Non-capability summaries for authenticated operator dashboards."""
        rows = []
        for row in [r for r in self.api.saved["session_budgets"].values()
                    if not r.get("weekly_parent_id")][-20:]:
            t = row["terms"]
            rows.append({
                "budget_id": t["budget_id"], "session_id": self.api.collections.session_id(row),
                "state": self.public(row)["state"], "version": t["version"],
                "approved": bool(row.get("receipt")), "expires_at": t["expires_at"],
                "created_at": t["created_at"],
                "satoshis_per_aud": t["satoshis_per_aud"],
                "receiving_registered_at": (row.get("credit_destination") or {}).get("registered_at"),
                "credit_terms": bool(t.get("credit_receiving")),
                "reviewed_closed_account": bool(t.get("closed_session_review")),
            })
            if t.get("version") == 3:
                from .weekly import summary
                rows[-1]["multi_session"] = summary(self.api, row)
        return rows

    def public(self, row):
        result = copy.deepcopy(row)
        for key in ("proxy_config_entry_id", "session_key", "created_by", "accepted_by",
                    "driver_token_hash", "driver_link_scheme"):
            result.pop(key, None)
        if row["state"] not in ("revoked", "charge_waived") and now() >= datetime.fromisoformat(row["terms"]["expires_at"]):
            result["state"] = "expired"
        if hasattr(self.api, "collections") and (collection := self.api.collections.get(row)):
            result["collection"] = self.api.collections.public(collection)
        if hasattr(self.api, "auto_credits"):
            result["automatic_credit"] = self.api.auto_credits.status(row)
        return result

    def link_token(self, budget_id):
        """Domain-separated capability; only its hash is persisted."""
        message = canonical({"purpose": "ha-bsv-settlement:driver-link:v1",
                             "entry_id": self.api.entry.entry_id, "budget_id": budget_id})
        raw = hmac.new(bytes.fromhex(self.api.identity["secret_hex"]),
                       message.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def admin_status(self, row):
        """Only the administrator service may redisplay a pending capability."""
        result = self.public(row)
        if row["terms"].get("version") == 3 and not row.get("receipt"):
            from .enrolment import admin_status
            result["public_registration"] = admin_status(self.api, row)
        if row["terms"].get("version") == 3:
            from .weekly import summary
            result["multi_session"] = summary(self.api, row)
        if (result["state"] == "awaiting_driver_consent" and not row.get("receipt") and
                row.get("driver_link_scheme") == "hmac-sha256-v1"):
            token = self.link_token(row["terms"]["budget_id"])
            if secrets.compare_digest(sha(token), row.get("driver_token_hash", "")):
                result["driver_link_fragment"] = f"#budget={row['terms']['budget_id']}&token={token}"
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

    def driver_access(self, budget_id, token, allow_terminal=False):
        row = self.api.saved["session_budgets"].get(budget_id)
        if (row is None or not isinstance(token, str) or len(token) != 43
                or not secrets.compare_digest(row.get("driver_token_hash", ""), sha(token))):
            raise WalletError("Invalid driver link")
        if not allow_terminal and self.public(row)["state"] in ("revoked", "expired", "charge_waived"):
            raise WalletError("Driver link is expired or revoked")
        return row

    def driver_view(self, row):
        session_id = self.api.collections.session_id(row)
        proxy = self.api.hass.data.get(DOMAIN, {}).get(row["proxy_config_entry_id"])
        data = getattr(proxy, "data", None) or {}
        record = next((s for s in [data.get("latest_session"), data.get("previous_session"),
                                  *getattr(proxy, "archive", [])]
                       if s and s.get("session_id") == session_id), None)
        session = ({k: copy.deepcopy(record.get(k)) for k in (
            "session_id", "running_state", "ended_at", "import_kwh", "export_kwh", "net_cost_aud")}
            if record and not data.get("issues") else None)
        closure = self.api.saved.get("closed_sessions", {}).get(
            row["proxy_config_entry_id"] + "|" + session_id) if session_id else None
        from .weekly import weekly_terms, summary
        return {"invitation": copy.deepcopy(row["invitation"]), "state": self.public(row)["state"],
                "multi_session": summary(self.api,row) if weekly_terms(row["terms"]) else None,
                "closure": ({k: copy.deepcopy(closure.get(k)) for k in (
                    "state", "amount_sats", "reason", "received_funds")} if closure else None),
                "prices": self.prices(row),
                "session": session,
                "ongoing_credits": self.api.ongoing_credits.driver_rows(row),
                "ongoing_credit_enabled": bool(self.api.ongoing_credits.policy.get("enabled")
                    and self.api.auto_credits.policy.get("enabled")),
                "driver_identity": (row.get("receipt") or {}).get("driver_identity"),
                "binding": copy.deepcopy(row.get("binding")),
                "credit_destination_registered": bool(row.get("credit_destination")),
                "automatic_credit_enabled": self.api.auto_credits.policy.get("enabled", False)
                    and row["state"] != "charge_waived"
                    and not row["terms"].get("closed_session_review")}

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
            return self.admin_status(row)
        if action in ("open_public_registration", "close_public_registration"):
            from .enrolment import manage
            return await manage(self.api, row, action, data, user_id)
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
        if row.get("weekly_parent_id"):
            raise WalletError("Derived session tickets cannot receive standalone consent")
        from .session_closure import ensure_open
        if self.api.collections.session_id(row):
            ensure_open(self.api, self.api.collections.key(row))
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
            if type(receipt["version"]) is not int or receipt["version"] != row["terms"]["version"] or receipt["budget_id"] != row["terms"]["budget_id"]:
                raise ValueError()
            expected = approval_payload(row["invitation"], identity)
            if receipt["payload"] != expected:
                raise ValueError()
            # BRC-43 protocol invoice; "anyone" is the public counterparty key 1.
            invoice = f"2-{signature_protocol(row['terms'])[1]}-{row['terms']['budget_id']}"
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
        row.update(state=SPENDING_STATE if row["terms"]["version"] in (2,3) else LEGACY_STATE,
                   receipt=copy.deepcopy(receipt), accepted_at=now().isoformat(), accepted_by=accepted_by)
        await self.api.store.async_save(self.api.saved)
        return self.public(row)

    async def bind(self, row, data):
        if self.public(row)["state"] not in (LEGACY_STATE, SPENDING_STATE):
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

    async def create(self, data, user_id, *, closed_review=None):
        multi=data.get("multi_session",False)
        if data.get("initial_session_id") and not multi:
            raise WalletError("Including a current session requires a fresh multi-session approval")
        if type(multi) is not bool or multi and (data.get("session_id") or closed_review):
            raise WalletError("Multi-session consent applies only to future sessions")
        if multi and not self.api.auto_credits.policy.get("enabled"):
            raise WalletError("Enable automatic credits before creating multi-session receiving and spending consent")
        from .session_closure import ensure_open, reviewed_snapshot
        from .session_review import digest
        if data.get("session_id"):
            ensure_open(self.api, data["proxy_config_entry_id"] + "|" + data["session_id"])
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
        if record and record.get("ended_at") and closed_review is None:
            raise WalletError("Choose an open session; do not backdate budget consent")
        if closed_review is not None and (
                record is None or digest(reviewed_snapshot(record, closed_review["accepted_flags"]))
                != digest(closed_review["account"])):
            raise WalletError("The reviewed closed account changed")
        if observations.get("issues"):
            raise WalletError("Resolve recorder issues before inviting the driver")
        reservation = str(uuid4())
        key = f"{data['proxy_config_entry_id']}:{'multi_session' if multi else data.get('session_id') or 'next_session'}"
        state = self.api.hass.states.get(data["conversion_rate_entity"])
        if state is None or state.attributes.get("unit_of_measurement") != "sat/AUD":
            raise WalletError("Select a positive conversion-rate sensor in sat/AUD")
        rate = decimal(state.state)
        if closed_review is not None and rate != decimal(closed_review["satoshis_per_aud"]):
            raise WalletError("The reviewed conversion rate changed")
        if not 0 < rate <= 100000000:
            raise WalletError("Invalid conversion rate")
        maximum, fee = data.get("max_total_sats", 1000), data.get("max_fee_sats", 1000)
        if type(maximum) is not int or not 1 <= maximum <= 100000 or type(fee) is not int or not 0 <= fee <= maximum or fee > 1000:
            raise WalletError("Fee cap must not exceed the total budget or 1,000 sat")
        minutes = data.get("valid_minutes", 10080 if multi else 720)
        if type(minutes) is not int or not 1 <= minutes <= (10080 if multi else 1440):
            raise WalletError("Expiry must be within seven days for multi-session or 24 hours for single-session consent")
        included = None
        if data.get("initial_session_id"):
            from .weekly import initial_session
            included = initial_session(self.api, data["proxy_config_entry_id"],
                                       data["initial_session_id"], rate, maximum)
        from .weekly import superseded_registration
        old = next((r for r in self.api.saved["session_budgets"].values()
                    if r["session_key"] == key and not r.get("binding")
                    and self.public(r)["state"] not in ("revoked", "expired")
                    and not (multi and superseded_registration(self.api, r))), None)
        replacement = any(k in data for k in (
            "replace_pending_budget_id", "expected_invitation_hash", "confirm_replace_pending"))
        if old:
            if old.get("receipt") or old["state"] != "awaiting_driver_consent":
                raise WalletError("This session already has a signed approval. Its limits cannot be edited or replaced here")
            if self.api.collections.get(old) or self.api.auto_credits.get(old):
                raise WalletError("A settlement record already exists. Review it; do not replace this invitation")
            t = old["terms"]
            same = (t["max_total_sats"] == maximum and t["max_fee_sats"] == fee
                    and round((datetime.fromisoformat(t["expires_at"]) -
                               datetime.fromisoformat(t["created_at"])).total_seconds() / 60) == minutes
                    and t["operator_name"] == data.get("operator_name", "Charging operator")
                    and t["operator_contact"] == data.get("operator_contact", "")
                    and t["conversion_rate_entity"] == data["conversion_rate_entity"]
                    and t.get("included_session") == included)
            if not replacement:
                if not same:
                    raise WalletError("An unapproved invitation already exists. Confirm replacement to apply changed limits or operator details")
                return self.admin_status(old) | {"invitation_reused": True}
            if (data.get("replace_pending_budget_id") != t["budget_id"]
                    or data.get("expected_invitation_hash") != sha(old["invitation"]["payload"])
                    or data.get("confirm_replace_pending") is not True):
                raise WalletError("The pending invitation changed. Reload before confirming replacement")
        elif replacement:
            raise WalletError("The pending invitation is no longer replaceable. Reload its status")
        budget_id = str(uuid4())
        sources = proxy.sources
        terms = {
            "version": 2, "budget_id": budget_id,
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
            "scope": SPENDING_SCOPE,
        }
        if multi:
            from .weekly import SCOPE
            terms.update(version=3,scope=SCOPE,session_mode="multi_session",
                session_id="multi:"+budget_id,transaction_id="Multiple future sessions",
                account_scope="Future sessions opened after receiving registration on this charger. "
                "One aggregate spending limit includes all driver payments and fees. "
                "Ends at expiry or a newer driver registration. Operator credits do not replenish the allowance.")
            if included:
                terms["included_session"] = included
                terms["transaction_id"] = "Current and future sessions"
                terms["account_scope"] += (
                    f" Also explicitly includes current session {included['transaction_id']}, "
                    "including energy recorded before this approval. "
                    "Its charge and network fee use the same aggregate limit, not an extra allowance.")
        terms["payment_authority"] = payment_authority(terms)
        if closed_review is not None:
            terms["closed_session_review"] = copy.deepcopy(closed_review)
            terms["account_scope"] = (
                f"Payment for the completed session only: {closed_review['amount_sats']} sat energy charge. "
                f"Frozen account AUD {closed_review['account']['net_amount_aud']}; "
                "network fee is additional within the total spending limit. "
                "No approval to start another session. "
                f"Metering warnings: {', '.join(closed_review['account'].get('quality_flags', [])) or 'none'}. "
                f"Operator review reason: {closed_review['reason']}")
        if closed_review is None:
            terms["credit_receiving"] = {
                "protocolID": [2, "3241645161d8"],
                "derivationPrefix": base64.b64encode(secrets.token_bytes(16)).decode(),
                "derivationSuffix": base64.b64encode(secrets.token_bytes(16)).decode(),
            }
        payload = canonical(terms)
        operator = PrivateKey(bytes.fromhex(self.api.identity["secret_hex"]))
        invitation = {"version": 1, "payload": payload,
                      "signature": operator.sign(payload.encode(), hasher=message_hash).hex()}
        token = self.link_token(budget_id)
        row = {"terms": terms, "invitation": invitation, "state": "awaiting_driver_consent",
               "session_key": key, "proxy_config_entry_id": data["proxy_config_entry_id"],
               "created_by": user_id, "receipt": None, "driver_token_hash": sha(token),
               "driver_link_scheme": "hmac-sha256-v1"}
        previous_latest = self.api.saved.get("latest_session_budget")
        if old:
            old["state"] = "revoked"
        self.api.saved["session_budgets"][budget_id] = row
        self.api.saved["latest_session_budget"] = budget_id
        try:
            await self.api.store.async_save(self.api.saved)
        except (Exception, asyncio.CancelledError):
            self.api.saved["session_budgets"].pop(budget_id, None)
            if old:
                old["state"] = "awaiting_driver_consent"
            if previous_latest is None:
                self.api.saved.pop("latest_session_budget", None)
            else:
                self.api.saved["latest_session_budget"] = previous_latest
            raise
        return self.public(row) | {"driver_link_fragment": f"#budget={budget_id}&token={token}"}
