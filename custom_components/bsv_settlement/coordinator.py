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
from .const import DOMAIN
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

    async def load(self):
        self.saved = await self.store.async_load() or self.saved

    async def persist(self):
        await self.store.async_save(self.saved)

    async def _async_update_data(self):
        async with self.lock:
            try:
                health = await self.api.call("GET", "/v1/health")
                # A timed-out prepare is replayed with the same ID and frozen payload.
                for session in self.saved["sessions"].values():
                    if session.get("payload"):
                        sid = session["settlement_id"]
                        if not session.get("remote"):
                            session["remote"] = await self.api.call(
                                "PUT", f"/v1/settlements/{sid}", session["payload"])
                        elif session["remote"]["state"] not in (
                            "mock_confirmed", "no_payment_due", "declined"
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
                            "state": state, "mode": "mock",
                        })
                        self.known_status[session["session_id"]] = state
                return {"health": health, "sessions": copy.deepcopy(self.saved["sessions"]),
                        "latest": self.saved["latest"]}
            except WalletError as exc:
                raise UpdateFailed(str(exc)) from exc

    async def execute(self, action, data):
        try:
            async with self.lock:
                session_id = data.get("session_id")
                if action == "bind_session":
                    timestamp(data["started_at"])
                    bound = await self.api.call("GET", "/v1/wallet-bindings/" + data["driver_binding_id"])
                    if bound["verification_status"] != "synthetic_mock_only":
                        raise ValueError("Only synthetic bindings are supported")
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
            return {"mode": "mock", "refreshed": True}
        except (WalletError, ValueError, KeyError, TypeError) as exc:
            raise HomeAssistantError(str(exc)) from exc
