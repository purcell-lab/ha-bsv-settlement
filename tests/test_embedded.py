"""Actual SDK and HA tests; all keys are ephemeral test fixtures, no network."""
import json
import socket
from types import MappingProxyType
from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant.exceptions import HomeAssistantError
from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.embedded import EmbeddedWalletAPI
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.config_flow import BSVSettlementConfigFlow
from custom_components.bsv_settlement.sensor import SettlementSensor
from test_poc import session


def make_entry(**changes):
    return ConfigEntry(
        version=1, minor_version=1, domain="bsv_settlement", title="Embedded",
        data={"backend": "embedded_testnet", "network": "testnet",
              "acknowledge_key_custody": True, **changes},
        source="user", unique_id="embedded-operator-testnet",
        options={}, discovery_keys=MappingProxyType({}), subentries_data=[])


async def make_hass(path, entry=None):
    hass = HomeAssistant(str(path))
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    if entry is not None:
        hass.config_entries._entries[entry.entry_id] = entry
    return hass


@pytest.mark.asyncio
async def test_embedded_identity_restart_permissions_selftest_and_no_network(tmp_path):
    entry = make_entry()
    hass = await make_hass(tmp_path, entry)
    try:
        api = EmbeddedWalletAPI(hass, entry)
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
            await api.load()
            identity = api.status()["operator_public_key"]
            result = await api.self_test()
            assert result["identity_signature_verified"]
            assert result["synthetic_transaction_signed"]
            assert result["synthetic_script_verified"]
            assert result["txid"] is None and result["broadcast"] is False
            assert result["network_checked"] is False
            assert "secret" not in json.dumps(result)
            assert api.identity["secret_hex"] not in json.dumps(api.status())
            restored = EmbeddedWalletAPI(hass, entry)
            await restored.load()
            assert restored.status()["operator_public_key"] == identity
            assert restored.status()["last_self_test"] == result
            assert restored.status()["balance_sats"] is None
        key_path = tmp_path / ".storage" / f"bsv_settlement.operator_key.{entry.entry_id}"
        assert key_path.stat().st_mode & 0o777 == 0o600
        key_path.unlink()
        with pytest.raises(WalletError, match="restore"):
            await EmbeddedWalletAPI(hass, entry).load()
        assert not key_path.exists()  # Never silently rotate a missing key.
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"network": "mainnet"}, {"acknowledge_key_custody": False},
    {"operator_public_key": "unexpected-existing-key"},
])
async def test_unsafe_configuration_fails_closed(tmp_path, changes):
    hass = await make_hass(tmp_path)
    try:
        with pytest.raises(WalletError):
            await EmbeddedWalletAPI(hass, make_entry(**changes)).load()
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("import_wh,export_wh,direction", [
    (3000, 2000, "operator_to_driver"),
    (2000, 0, "driver_to_operator"),
    (2000, 1000, "none"),
])
async def test_standalone_ledger_drafts_and_payment_block(tmp_path, import_wh, export_wh, direction):
    entry = make_entry()
    hass = await make_hass(tmp_path, entry)
    try:
        api = EmbeddedWalletAPI(hass, entry)
        await api.load()
        coord = SettlementCoordinator(hass, entry, api)
        await coord.load()
        await async_setup(hass, {})
        hass.data["bsv_settlement"][entry.entry_id] = coord
        data = session()
        base = {"config_entry_id": entry.entry_id, "session_id": data["session_id"]}
        await coord.execute("bind_session", {**base, "started_at": data["started_at"],
                                            "driver_binding_id": "driver-external"})
        interval = {**data["intervals"][0], "import_wh": import_wh, "export_wh": export_wh}
        await coord.execute("add_interval", {**base, "interval": interval})
        args = {**base, "ended_at": interval["end"],
                "final_import_wh": import_wh, "final_export_wh": export_wh}
        record = await coord.execute("prepare_session", args)
        assert record["direction"] == direction
        assert record["txid"] is None and record["receipt_id"] is None
        assert record["amount_sats"] is None  # No invented exchange rate.
        assert record["state"] == ("no_payment_due" if direction == "none" else "blocked_live_settlement")
        assert record == await coord.execute("prepare_session", args)
        with pytest.raises(HomeAssistantError, match="disabled"):
            await coord.execute("request_payment", base)
        with pytest.raises(WalletError, match="disabled"):
            await api.call("POST", f"/v1/settlements/{record['settlement_id']}/request-payment", {})
        changed = {**coord.saved["sessions"][data["session_id"]]["payload"], "net_amount_minor": 2}
        with pytest.raises(WalletError, match="cannot be changed"):
            await api.call("PUT", f"/v1/settlements/{record['settlement_id']}", changed)
        response = await hass.services.async_call(
            "bsv_settlement", "wallet_self_test", {"config_entry_id": entry.entry_id},
            blocking=True, return_response=True)
        assert response["broadcast"] is False
        sensor = SettlementSensor(coord, entry, "operator_wallet_status", "Wallet", None)
        assert sensor.native_value == "ready_broadcast_disabled"
        assert sensor.extra_state_attributes["operator_public_key"] == api.identity["public_key"]
        assert "secret_hex" not in sensor.extra_state_attributes
        restored = EmbeddedWalletAPI(hass, entry)
        await restored.load()
        assert record == await restored.call("GET", f"/v1/settlements/{record['settlement_id']}")
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_embedded_config_flow_acknowledgement(tmp_path):
    hass = await make_hass(tmp_path)
    try:
        flow = BSVSettlementConfigFlow()
        flow.hass = hass
        flow.context = {}
        result = await flow.async_step_user({"backend": "embedded_testnet"})
        assert result["step_id"] == "embedded"
        refused = await flow.async_step_embedded({"acknowledge_key_custody": False})
        assert refused["errors"]["base"] == "acknowledgement_required"
        result = await flow.async_step_embedded({"acknowledge_key_custody": True})
        assert result["type"] == "create_entry"
        assert result["data"]["network"] == "testnet"
        assert not any("secret" in key for key in result["data"])
    finally:
        await hass.async_stop(force=True)
