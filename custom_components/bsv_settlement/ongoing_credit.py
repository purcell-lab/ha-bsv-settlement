"""Operator-authorised credits to the latest verified receiving registration.

This is NOT a driver spending mandate. Routes are fixed per session and share
the existing durable transaction, input-exclusion and reconciliation machinery.
"""
import copy
import json
from datetime import datetime

from bsv import PrivateKey, PublicKey
from .api import WalletError
from .auto_credit import AutomaticCredits, MAX_TOTAL
from .fees import MODE
from .budget import approval_payload, message_hash
from .const import DOMAIN
from .session_review import account_snapshot, decimal, now


class OngoingCredits(AutomaticCredits):
    def __init__(self, api):
        self.api = api
        api.saved.setdefault("ongoing_credit_policy", {"enabled": False})
        api.saved.setdefault("ongoing_credit_routes", {})

    @property
    def policy(self):
        return self.api.saved["ongoing_credit_policy"]

    @property
    def routes(self):
        return self.api.saved["ongoing_credit_routes"]

    def verified_registration(self, row):
        """Expiry of spending consent does not extend or limit operator consent."""
        try:
            terms, receipt, destination = row["terms"], row["receipt"], row["credit_destination"]
            if row["state"] == "revoked" or terms["version"] not in (2,3):
                raise ValueError()
            if (json.loads(row["invitation"]["payload"]) != terms or
                    terms["operator_identity"] != self.api.identity["public_key"] or
                    receipt["payload"] != approval_payload(row["invitation"], receipt["driver_identity"])):
                raise ValueError()
            verifier = PublicKey(bytes.fromhex(receipt["driver_identity"])).derive_child(
                PrivateKey(1), f"2-ev session spending-{terms['budget_id']}")
            if not verifier.verify(bytes.fromhex(receipt["signature"]),
                                   receipt["payload"].encode(), hasher=message_hash):
                raise ValueError()
            payload = self.api.auto_credits.registration_payload(row)
            proof = destination["proof"]
            if (proof["payload"] != payload or
                    not verifier.verify(bytes.fromhex(proof["signature"]), payload.encode(), hasher=message_hash)
                    or destination["address"] != self.api.auto_credits.destination(row).address()):
                raise ValueError()
            return {"budget_id": terms["budget_id"], "address": destination["address"],
                    "driver_identity": receipt["driver_identity"],
                    "registered_at": destination["registered_at"]}
        except (KeyError, TypeError, ValueError):
            raise WalletError("Latest receiving registration is revoked, incomplete or invalid") from None

    def latest(self, before=None):
        rows = [r for r in self.api.saved["session_budgets"].values()
                if r.get("credit_destination") and
                r["proxy_config_entry_id"] == self.policy.get("proxy_config_entry_id") and
                (before is None or datetime.fromisoformat(
                    r["credit_destination"]["registered_at"]) <= before)]
        if not rows:
            raise WalletError("No registered driver wallet is available for this session")
        # Never silently fall back to an earlier driver when the latest is invalid.
        selected=max(rows, key=lambda r:datetime.fromisoformat(r["credit_destination"]["registered_at"]))
        if selected["terms"].get("version")==3:
            expiry=datetime.fromisoformat(selected["terms"]["expires_at"])
            if (before or now())>=expiry:
                raise WalletError("Multi-session receiving approval expired; register a new driver invitation")
        return self.verified_registration(selected)

    async def configure(self, data, user_id):
        if not user_id or type(data.get("enabled")) is not bool:
            raise WalletError("An administrator must authorise ongoing credits")
        if not data["enabled"]:
            self.policy["enabled"] = False
            await self.save()
            return self.summary()
        if data.get("confirm_ongoing_mainnet_credits") is not True:
            raise WalletError("Explicit ongoing operator payment authority is required")
        if self.policy.get("enabled"):
            raise WalletError("Ongoing policy already enabled; stop it before changing its scope")
        if not self.api.auto_credits.policy.get("enabled"):
            raise WalletError("Enable the capped automatic-credit policy first")
        proxy = self.api.hass.data.get(DOMAIN, {}).get(data.get("proxy_config_entry_id"))
        if proxy is None or proxy.mode != "sensor_proxy":
            raise WalletError("Select the loaded session recorder")
        old = copy.deepcopy(self.policy)
        old_routes = copy.deepcopy(self.routes)
        self.api.saved["ongoing_credit_policy"] = {
            "enabled": True, "enabled_at": now().isoformat(), "authorised_by": user_id,
            "proxy_config_entry_id": data["proxy_config_entry_id"],
            "conversion_rate_entity": data["conversion_rate_entity"],
            "initial_session_id": data.get("initial_session_id"),
        }
        try:
            recipient = self.latest()
            if (recipient["budget_id"] != data.get("expected_budget_id") or
                    recipient["address"] != data.get("expected_recipient_address")):
                raise WalletError("Latest registered recipient changed; review the current recipient")
            await proxy.async_request_refresh()
            records = self.records(proxy)
            if data.get("initial_session_id"):
                record = next((r for r in records if r["session_id"] == data["initial_session_id"]), None)
                if record is None:
                    raise WalletError("Explicitly authorised initial session is not retained")
                await self.assign(record, recipient, persist=False)
            await self.save()
        except Exception:
            self.api.saved["ongoing_credit_policy"] = old
            self.api.saved["ongoing_credit_routes"] = old_routes
            raise
        return self.summary()

    @staticmethod
    def records(proxy):
        data = proxy.data or {}
        return [r for r in [data.get("latest_session"), data.get("previous_session"),
                            *proxy.archive] if r]

    async def assign(self, record, recipient, persist=True):
        route_id = "ongoing:" + self.policy["proxy_config_entry_id"] + "|" + record["session_id"]
        from .monthly_ownership import ensure_no_monthly_owner
        ensure_no_monthly_owner(self.api, route_id.removeprefix("ongoing:"))
        if route_id in self.routes:
            return self.routes[route_id]
        state = self.api.hass.states.get(self.policy["conversion_rate_entity"])
        if state is None or state.attributes.get("unit_of_measurement") != "sat/AUD":
            raise WalletError("Conversion sensor is unavailable")
        rate = decimal(state.state)
        if not 0 < rate <= 100000000:
            raise WalletError("Conversion sensor is outside the allowed range")
        route = {
            "route_id": route_id, "session_id": record["session_id"],
            "transaction_id": record["ocpp_transaction_id"],
            "proxy_config_entry_id": self.policy["proxy_config_entry_id"],
            "recipient": copy.deepcopy(recipient), "satoshis_per_aud": str(rate),
            "assigned_at": now().isoformat(), "state": "waiting_for_session_end",
            "policy_enabled_at": self.policy["enabled_at"],
        }
        self.routes[route_id] = route
        if persist:
            await self.save()
        return route

    def wrapper(self, route):
        return {"standing_route_id": route["route_id"],
                "proxy_config_entry_id": route["proxy_config_entry_id"],
                "terms": {"budget_id": route["route_id"], "session_id": route["session_id"],
                          "session_mode": "operator_credit_route",
                          "transaction_id": route["transaction_id"],
                          "satoshis_per_aud": route["satoshis_per_aud"]},
                "credit_destination": {"address": route["recipient"]["address"]}}

    def guard(self, row):
        route = self.routes.get(row["standing_route_id"])
        if not route or row != self.wrapper(route):
            raise WalletError("Ongoing credit route changed")
        if route.get("manual_recovery"):
            self.api.credit_recovery.guard(row)
            return
        if (not self.policy.get("enabled") or not self.api.auto_credits.policy.get("enabled") or
                self.api.entry.data.get("enable_broadcast") is not True):
            raise WalletError("Ongoing operator credits are paused")
        if route["policy_enabled_at"] != self.policy["enabled_at"]:
            raise WalletError("Route belongs to an earlier policy activation; review it separately")
        registered = self.api.saved["session_budgets"][route["recipient"]["budget_id"]]
        if registered["terms"].get("version")==3:
            from .weekly import verify_parent
            verify_parent(self.api,registered)
        if self.verified_registration(registered) != route["recipient"]:
            raise WalletError("Frozen receiving registration changed")

    async def quote_fee(self, row, amount):
        if self.routes[row["standing_route_id"]].get("manual_recovery"):
            return await self.api.credit_recovery.quote_fee(row, amount)
        return await super().quote_fee(row, amount)

    def blocking_pending(self, row):
        if self.routes[row["standing_route_id"]].get("manual_recovery"):
            return self.api.credit_recovery.blocking_pending(row)
        return super().blocking_pending(row)

    async def funding(self, row, amount, fee):
        if self.routes[row["standing_route_id"]].get("manual_recovery"):
            return await self.api.credit_recovery.funding(row, amount, fee)
        return await super().funding(row, amount, fee)

    async def account(self, row):
        self.guard(row)
        self.conflict(row)
        record = await self.api.collections.source(row)
        result = account_snapshot(record)
        if (result["ocpp_transaction_id"] != row["terms"]["transaction_id"] or
                datetime.fromisoformat(result["ended_at"]) > now()):
            raise WalletError("Session transaction changed or its end is in the future")
        return result

    async def tick(self):
        if self.policy.get("enabled"):
            proxy = self.api.hass.data.get(DOMAIN, {}).get(self.policy["proxy_config_entry_id"])
            try:
                if proxy is None:
                    raise WalletError("Session recorder unavailable")
                await proxy.async_request_refresh()
                if (proxy.data or {}).get("issues"):
                    raise WalletError("Resolve recorder issues before assigning a recipient")
                for record in self.records(proxy):
                    from .monthly_ownership import monthly_owner
                    account_key = self.policy["proxy_config_entry_id"] + "|" + record["session_id"]
                    if monthly_owner(self.api, account_key) is not None:
                        continue  # Retain the monthly owner; do not assign a legacy recipient.
                    opened = datetime.fromisoformat(record["opened_at"])
                    if opened < datetime.fromisoformat(self.policy["enabled_at"]):
                        continue  # Only the explicitly assigned initial session is retrospective.
                    route_id = "ongoing:" + self.policy["proxy_config_entry_id"] + "|" + record["session_id"]
                    if route_id not in self.routes:
                        await self.assign(record, self.latest(before=opened))
                self.policy.pop("error", None)
            except WalletError as exc:
                self.policy["error"] = str(exc)
        for route in self.routes.values():
            row = self.wrapper(route)
            item = self.get(row)
            if item and item.get("txid"):
                try:
                    await self.reconcile(item)  # Reconcile even after policy disable/revocation.
                    route.pop("error", None)
                except WalletError as exc:
                    route["error"] = str(exc)
                    item["error"] = str(exc)
                route["state"] = item["state"]
                await self.save()
                continue
            if (route.get("manual_recovery") or not self.policy.get("enabled")
                    or route["state"] == "no_operator_credit"):
                continue
            try:
                record = await self.api.collections.source(row)
                if not record.get("ended_at"):
                    self.guard(row)
                    if item:
                        raise WalletError("Frozen credit account reopened; review it without replacing payment")
                    route["state"] = "waiting_for_session_end"
                    route.pop("error", None)
                    await self.save()
                    continue
                account = await self.account(row)
                if decimal(account["net_amount_aud"]) >= 0:
                    route["state"] = "no_operator_credit"
                else:
                    await self.process(row)
                    route["state"] = self.get(row)["state"]
                route.pop("error", None)
            except WalletError as exc:
                route["error"] = str(exc)
                queued = self.get(row)
                route["state"] = "credit_queued" if queued else "credit_blocked"
                if queued:
                    queued["error"] = str(exc)
            await self.save()

    def route_public(self, route):
        item = self.get(self.wrapper(route))
        return {"credit_id": route["route_id"], "session_id": route["session_id"],
                "transaction_id": route["transaction_id"], "state": route["state"],
                "recipient_address": route["recipient"]["address"],
                "driver_identity": route["recipient"]["driver_identity"],
                "receiving_budget_id": route["recipient"]["budget_id"],
                "satoshis_per_aud": route["satoshis_per_aud"],
                "assigned_at": route["assigned_at"], "error": route.get("error"),
                **(self.public(item) if item else {}),
                "error": route.get("error") or (item or {}).get("error"),
                "recovery": self.api.credit_recovery.public(route) if route.get("manual_recovery") else None}

    def summary(self):
        try:
            recipient = self.latest() if self.policy.get("proxy_config_entry_id") else None
        except WalletError:
            recipient = None
        return {"enabled": self.policy.get("enabled", False),
                "effective": bool(self.policy.get("enabled") and self.api.auto_credits.policy.get("enabled")),
                "enabled_at": self.policy.get("enabled_at"), "recipient": recipient,
                "error": self.policy.get("error"), "max_total_sats": MAX_TOTAL,
                "fee_sats": None, "fee_mode": MODE,
                "routing": "latest_verified_registration_at_session_open",
                "sessions": [self.route_public(r) for r in list(self.routes.values())[-20:]]}

    def driver_rows(self, row):
        return [self.route_public(r) for r in self.routes.values()
                if r["recipient"]["budget_id"] == row["terms"]["budget_id"]][-20:]

    async def driver_receipt(self, row, credit_id):
        route = self.routes.get(credit_id)
        if not route or route["recipient"]["budget_id"] != row["terms"]["budget_id"]:
            raise WalletError("Credit does not belong to this receiving registration")
        item = self.get(self.wrapper(route))
        result = await self.api.auto_credits.receipt_for_item(row, item)
        return result | {"budget_id": row["terms"]["budget_id"], "credit_id": credit_id,
                         "authority": "ongoing_operator_credit_policy"}
