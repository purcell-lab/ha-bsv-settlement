"""Persist session input and reconcile settlement state across HA restarts."""
import asyncio
import copy
from datetime import timedelta
from uuid import uuid4

from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import HomeAssistantError
import logging

from .api import WalletError
from .const import DOMAIN, SESSION_REVIEW_SERVICES, BUDGET_SERVICES, COLLECTION_RECOVERY_SERVICES, CLOSURE_SERVICES, CREDIT_RECOVERY_SERVICES
from .ledger import freeze, timestamp, validate_interval

_LOGGER = logging.getLogger(__name__)


class SettlementCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, api):
        super().__init__(hass, _LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=15))
        self.api = api
        self.lock = asyncio.Lock()
        self.store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self.saved = {"sessions": {}, "latest": None}
        self.known_status = {}
        self.mode = getattr(api, "mode", "mock")

    async def load(self):
        self.saved = await self.store.async_load() or self.saved

    async def persist(self):
        await self.store.async_save(self.saved)

    async def _async_update_data(self):
        async with self.lock:
            try:
                if self.mode == "embedded_mainnet":
                    await self.api.ongoing_credits.tick()
                    await self.api.auto_credits.tick()
                    await self.api.refresh_balance_if_due(reconcile_payment=True)
                health = await self.api.call("GET", "/v1/health")
                # A timed-out prepare is replayed with the same ID and frozen payload.
                for session in self.saved["sessions"].values():
                    if session.get("payload"):
                        sid = session["settlement_id"]
                        if not session.get("remote"):
                            session["remote"] = await self.api.call(
                                "PUT", f"/v1/settlements/{sid}", session["payload"])
                        elif session["remote"]["state"] not in (
                            "mock_confirmed", "no_payment_due", "declined", "blocked_live_settlement"
                        ):
                            session["remote"] = await self.api.call("GET", f"/v1/settlements/{sid}")
                await self.persist()
                for session in self.saved["sessions"].values():
                    remote = session.get("remote", {})
                    state = remote.get("state")
                    if state and self.known_status.get(session["session_id"]) != state:
                        self.hass.bus.async_fire("bsv_settlement_status_changed", {
                            "session_id": session["session_id"],
                            "settlement_id": session["settlement_id"],
                            "state": state, "mode": self.mode,
                        })
                        self.known_status[session["session_id"]] = state
                return {"health": health, "sessions": copy.deepcopy(self.saved["sessions"]),
                        "latest": self.saved["latest"]}
            except WalletError as exc:
                raise UpdateFailed(str(exc)) from exc

    async def execute(self, action, data, approving_user_id=None):
        try:
            async with self.lock:
                session_id = data.get("session_id")
                if action == "get_credit_receipt_link":
                    if self.mode != "embedded_mainnet":
                        raise WalletError("Select the mainnet operator wallet")
                    from .credit_receipt_link import retrieve
                    # No refresh/tick: link retrieval cannot advance payments.
                    return retrieve(self.api, data, approving_user_id)
                if action in CREDIT_RECOVERY_SERVICES:
                    if self.mode != "embedded_mainnet":
                        raise WalletError("Select the mainnet operator wallet")
                    try:
                        method = (self.api.credit_recovery.prepare if action == "prepare_operator_credit_recovery"
                                  else self.api.credit_recovery.broadcast)
                        return await method(data, approving_user_id)
                    finally:
                        self.async_set_updated_data({**(self.data or {}), "health": self.api.status()})
                if action in CLOSURE_SERVICES:
                    if self.mode != "embedded_mainnet":
                        raise WalletError("Select the mainnet operator wallet")
                    try:
                        return await self.api.closures.execute(action, data, approving_user_id)
                    finally:
                        self.async_set_updated_data({**(self.data or {}), "health": self.api.status()})
                if action in COLLECTION_RECOVERY_SERVICES:
                    if self.mode != "embedded_mainnet":
                        raise WalletError("Select the mainnet operator wallet")
                    from .collection_recovery import execute
                    result = await execute(self.api.collections, action, data, approving_user_id)
                    self.async_set_updated_data({**(self.data or {}), "health": self.api.status()})
                    return result
                if action == "configure_automatic_credit":
                    if self.mode != "embedded_mainnet":
                        raise WalletError("Select the mainnet operator wallet")
                    result = await self.api.auto_credits.configure(data["enabled"], approving_user_id)
                    self.async_set_updated_data({**(self.data or {}), "health": self.api.status()})
                    return result
                if action == "configure_ongoing_credit":
                    if self.mode != "embedded_mainnet":
                        raise WalletError("Select the mainnet operator wallet")
                    result = await self.api.ongoing_credits.configure(data, approving_user_id)
                    self.async_set_updated_data({**(self.data or {}), "health": self.api.status()})
                    return result
                if action in BUDGET_SERVICES:
                    if self.mode != "embedded_mainnet":
                        raise WalletError("Select the mainnet operator wallet for budget consent")
                    return await self.api.budgets.execute(action, data, approving_user_id)
                if action in SESSION_REVIEW_SERVICES:
                    if self.mode != "embedded_mainnet":
                        raise WalletError("Select the separate mainnet operator wallet for session review")
                    try:
                        result = await self.api.reviews.execute(action, data, approving_user_id)
                    finally:
                        self.async_set_updated_data({
                            **(self.data or {}), "health": self.api.status()})
                    return result
                wallet_actions = ("wallet_status", "wallet_self_test", "wallet_refresh_chain",
                                  "prepare_operator_payment", "broadcast_operator_payment",
                                  "cancel_operator_payment")
                if action in wallet_actions:
                    if not self.mode.startswith("embedded_"):
                        raise WalletError("Select the embedded operator wallet entry")
                    if action == "wallet_self_test":
                        result = await self.api.self_test()
                    elif action == "wallet_status":
                        result = self.api.status()
                    else:
                        if self.mode != "embedded_mainnet":
                            raise WalletError("Select the separate mainnet operator wallet")
                        if not approving_user_id:
                            raise WalletError("An explicit administrator context is required")
                        if action == "wallet_refresh_chain":
                            result = await self.api.refresh_chain()
                        elif action == "prepare_operator_payment":
                            result = await self.api.prepare_payment(data)
                        elif action == "broadcast_operator_payment":
                            result = await self.api.broadcast_payment(data, approving_user_id)
                        else:
                            result = await self.api.cancel_payment(data)
                    self.async_set_updated_data({
                        **(self.data or {}), "health": self.api.status()})
                    return result
                if action == "bind_session":
                    timestamp(data["started_at"])
                    bound = await self.api.call("GET", "/v1/wallet-bindings/" + data["driver_binding_id"])
                    expected = ("synthetic_mock_only" if self.mode == "mock"
                                else "external_unverified_draft_only")
                    if bound["verification_status"] != expected:
                        raise ValueError("Wallet binding does not match the selected backend")
                    existing = self.saved["sessions"].get(session_id)
                    if existing:
                        if (existing["driver_binding_id"] != data["driver_binding_id"]
                                or existing["started_at"] != data["started_at"]):
                            raise ValueError("Session binding already exists with different contents")
                    else:
                        # This POC deliberately permits one unfinished metering session.
                        if any(not s.get("payload") for s in self.saved["sessions"].values()):
                            raise ValueError("Prepare the existing metering session first")
                        self.saved["sessions"][session_id] = {
                            "session_id": session_id, "started_at": data["started_at"],
                            "driver_binding_id": data["driver_binding_id"], "intervals": []}
                    self.saved["latest"] = session_id
                    await self.persist()
                elif action != "refresh":
                    if session_id not in self.saved["sessions"]:
                        raise ValueError("Bind the session first")
                    session = self.saved["sessions"][session_id]
                    self.saved["latest"] = session_id
                    if action == "add_interval":
                        interval = copy.deepcopy(data["interval"])
                        validate_interval(interval)
                        if session.get("payload"):
                            raise ValueError("Ledger is frozen")
                        if interval not in session["intervals"]:
                            for old in session["intervals"]:
                                if max(timestamp(old["start"]), timestamp(interval["start"])) < min(
                                    timestamp(old["end"]), timestamp(interval["end"])
                                ):
                                    raise ValueError("Interval overlaps existing evidence")
                            if timestamp(interval["start"]) < timestamp(session["started_at"]):
                                raise ValueError("Interval precedes session start")
                            session["intervals"].append(interval)
                        await self.persist()
                    elif action == "prepare_session":
                        payload = freeze(session, data["ended_at"], data["final_import_wh"],
                                         data["final_export_wh"])
                        if self.mode.startswith("embedded_"):
                            payload["operator_binding_id"] = "embedded-operator-" + self.api.network
                        if session.get("payload") and session["payload"] != payload:
                            raise ValueError("Frozen ledger cannot be changed")
                        session.setdefault("settlement_id", str(uuid4()))
                        session["payload"] = payload
                        await self.persist()  # Persist before first external request.
                        sid = session["settlement_id"]
                        session["remote"] = await self.api.call("PUT", f"/v1/settlements/{sid}", payload)
                        if session["remote"]["state"] == "expired":
                            await self.api.call("POST", f"/v1/settlements/{sid}/quotes",
                                                {"replaces_quote_id": session["remote"]["quote"]["quote_id"]})
                            session["remote"] = await self.api.call("GET", f"/v1/settlements/{sid}")
                        await self.persist()
                    elif action == "request_payment":
                        if self.mode.startswith("embedded_"):
                            raise WalletError("Automatic session payments are disabled; use the guarded operator-payment workflow")
                        if not session.get("remote"):
                            raise ValueError("Prepare the session first")
                        sid = session["settlement_id"]
                        session["remote"] = await self.api.call(
                            "POST", f"/v1/settlements/{sid}/request-payment",
                            {"quote_id": session["remote"]["quote"]["quote_id"]})
                        await self.persist()
            await self.async_request_refresh()
            if session_id:
                return copy.deepcopy(self.saved["sessions"][session_id].get("remote", {"session_id": session_id}))
            return {"mode": self.mode, "refreshed": True}
        except (WalletError, ValueError, KeyError, TypeError) as exc:
            raise HomeAssistantError(str(exc)) from exc
