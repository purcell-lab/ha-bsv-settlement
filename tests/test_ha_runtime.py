"""Optional tests against the real Home Assistant Python package, not HA mocks.

The wallet HTTP transport is in-process ASGI; no real wallet or funds.
Install homeassistant + pytest-asyncio to run.
"""
import copy
from types import MappingProxyType

import httpx
import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.sensor import SettlementSensor
from custom_components.bsv_settlement.config_flow import BSVSettlementConfigFlow
from custom_components.bsv_settlement.api import WalletError
from wallet_service.app import create_app
from test_poc import API, APPROVAL, HEADERS, APPROVER, session


class ASGIWallet:
    def __init__(self, client):
        self.client = client

    async def call(self, method, path, data=None):
        response = await self.client.request(method, path, json=data)
        if response.status_code >= 400:
            raise WalletError(f"HTTP {response.status_code}")
        return response.json()


def entry():
    return ConfigEntry(version=1, minor_version=1, domain="bsv_settlement",
                       title="Mock", data={}, source="user", unique_id="mock",
                       options={}, discovery_keys=MappingProxyType({}), subentries_data=[])


@pytest.mark.asyncio
async def test_real_ha_actions_storage_sensor_restart(tmp_path):
    hass = HomeAssistant(str(tmp_path / "ha"))
    app = create_app(tmp_path / "service.sqlite3", API, APPROVAL)
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://mock", headers=HEADERS) as client:
            config_entry = entry()
            coord = SettlementCoordinator(hass, config_entry, ASGIWallet(client))
            await coord.load()
            await async_setup(hass, {})
            hass.data["bsv_settlement"][config_entry.entry_id] = coord
            s = session()
            base = {"config_entry_id": config_entry.entry_id, "session_id": s["session_id"]}
            async def action(name, fields):
                return await hass.services.async_call(
                    "bsv_settlement", name, {**base, **fields}, blocking=True, return_response=True)
            await action("bind_session", {"started_at": s["started_at"], "driver_binding_id": "driver-demo-01"})
            await action("add_interval", {"interval": s["intervals"][0]})
            await action("add_interval", {"interval": s["intervals"][0]})
            assert len(coord.saved["sessions"][s["session_id"]]["intervals"]) == 1
            rec = await action("prepare_session", {"ended_at": s["intervals"][0]["end"],
                                                   "final_import_wh": 3000, "final_export_wh": 2000})
            assert rec["net_amount_minor"] == -30
            requested = await action("request_payment", {})
            result = await client.post(f"/v1/mock/settlements/{rec['settlement_id']}/decision",
                                       headers=APPROVER,
                                       json={"approval_request_id": requested["approval_request_id"],
                                             "quote_id": requested["quote"]["quote_id"], "decision": "approve"})
            assert result.status_code == 200
            coord.async_set_updated_data(await coord._async_update_data())
            sensor = SettlementSensor(coord, config_entry, "settlement_status", "Status", None)
            assert sensor.native_value == "mock_received"
            money = SettlementSensor(coord, config_entry, "session_net_amount", "Net", "AUD")
            assert money.native_value == -0.3
            restored = SettlementCoordinator(hass, config_entry, ASGIWallet(client))
            await restored.load()
            assert restored.saved["sessions"][s["session_id"]]["settlement_id"] == rec["settlement_id"]
            data = await restored._async_update_data()
            assert data["sessions"][s["session_id"]]["remote"]["receipt_id"] == result.json()["receipt_id"]
            with pytest.raises(HomeAssistantError):
                await action("add_interval", {"interval": s["intervals"][0]})
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_real_config_flow_form(tmp_path):
    hass = HomeAssistant(str(tmp_path / "ha"))
    try:
        flow = BSVSettlementConfigFlow()
        flow.hass = hass
        flow.context = {}
        result = await flow.async_step_user()
        assert result["type"] == "form"
        assert result["step_id"] == "user"
    finally:
        await hass.async_stop(force=True)
