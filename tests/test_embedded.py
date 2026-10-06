"""Shared operator-wallet base, exercised through the mainnet backend.

All keys are ephemeral test fixtures; chain access is a stub and no socket may connect.
"""
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
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.sensor import SettlementSensor
from test_poc import session


def make_entry(**changes):
    return ConfigEntry(
        version=1, minor_version=1, domain="bsv_settlement", title="Mainnet",
        data={"backend": "embedded_mainnet", "network": "mainnet",
              "acknowledge_key_custody": True, "acknowledge_mainnet": True,
              "enable_broadcast": True, **changes},
        source="user", unique_id="embedded-operator-mainnet",
        options={}, discovery_keys=MappingProxyType({}), subentries_data=[])


async def make_hass(path, entry=None):
    hass = HomeAssistant(str(path))
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    if entry is not None:
        hass.config_entries._entries[entry.entry_id] = entry
    return hass


class EmptyChain:
    """Unfunded fictional address; any broadcast is a test failure."""
    posts = []

    async def unspent(self, address):
        return []

    async def unconfirmed_unspent(self, address):
        return []

    async def fee_policy(self):
        return {"fee_unit": "sat/KB", "fee": 40, "mempool_min_fee": 40}

    async def broadcast(self, raw):
        self.posts.append(raw)
        raise AssertionError("must not broadcast")


@pytest.fixture(autouse=True)
def no_ha_network_session(monkeypatch):
    monkeypatch.setattr("custom_components.bsv_settlement.mainnet.async_get_clientsession",
                        lambda hass: None)


async def load_wallet(hass, entry):
    api = MainnetWalletAPI(hass, entry)
    await api.load()
    api.chain = EmptyChain()
    return api


@pytest.mark.asyncio
async def test_identity_restart_permissions_selftest_and_no_network(tmp_path):
    entry = make_entry()
    hass = await make_hass(tmp_path, entry)
    try:
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
            api = await load_wallet(hass, entry)
            identity = api.status()["operator_public_key"]
            result = await api.self_test()
            assert result["identity_signature_verified"]
            assert result["synthetic_transaction_signed"]
            assert result["synthetic_script_verified"]
            assert result["txid"] is None and result["broadcast"] is False
            assert result["network_checked"] is False
            assert "secret" not in json.dumps(result)
            assert api.identity["secret_hex"] not in json.dumps(api.status())
            assert api.chain.posts == []
            restored = await load_wallet(hass, entry)
            assert restored.status()["operator_public_key"] == identity
            assert restored.status()["last_self_test"] == result
            assert restored.status()["balance_sats"] is None
        key_path = tmp_path / ".storage" / f"bsv_settlement.operator_key.{entry.entry_id}"
        assert key_path.stat().st_mode & 0o777 == 0o600
        key_path.unlink()
        with pytest.raises(WalletError, match="restore"):
            await MainnetWalletAPI(hass, entry).load()
        assert not key_path.exists()  # Never silently rotate a missing key.
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"network": "testnet"}, {"backend": "embedded_testnet", "network": "testnet"},
    {"acknowledge_key_custody": False}, {"acknowledge_mainnet": False},
    {"enable_broadcast": False}, {"operator_public_key": "unexpected-existing-key"},
])
async def test_unsafe_configuration_fails_closed(tmp_path, changes):
    hass = await make_hass(tmp_path)
    try:
        with pytest.raises(WalletError):
            await MainnetWalletAPI(hass, make_entry(**changes)).load()
        assert not (tmp_path / ".storage").exists() or not any((tmp_path / ".storage").iterdir())
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [
    {"backend": "embedded_mainnet", "network": "mainnet"},
    {"backend": "embedded_testnet", "network": "testnet"},
    {"network": None},
])
async def test_base_class_is_not_a_backend(tmp_path, data):
    """The former testnet wallet cannot be reached through the shared base."""
    entry = make_entry(**data)
    hass = await make_hass(tmp_path, entry)
    try:
        assert EmbeddedWalletAPI.mode is None and EmbeddedWalletAPI.network is None
        with pytest.raises(WalletError):
            await EmbeddedWalletAPI(hass, entry).load()
        assert not (tmp_path / ".storage").exists() or not any((tmp_path / ".storage").iterdir())
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("import_wh,export_wh,direction", [
    (3000, 2000, "operator_to_driver"),
    (2000, 0, "driver_to_operator"),
    (2000, 1000, "none"),
])
async def test_session_drafts_never_pay_on_mainnet(tmp_path, import_wh, export_wh, direction):
    entry = make_entry()
    hass = await make_hass(tmp_path, entry)
    try:
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
            api = await load_wallet(hass, entry)
            coord = SettlementCoordinator(hass, entry, api)
            await coord.load()
            await async_setup(hass, {})
            hass.data["bsv_settlement"][entry.entry_id] = coord
            assert not hass.services.has_service("bsv_settlement", "request_payment")
            data = session()
            base = {"config_entry_id": entry.entry_id, "session_id": data["session_id"]}
            with pytest.raises(HomeAssistantError):  # The mock-only binding is gone.
                await coord.execute("bind_session", {**base, "started_at": data["started_at"],
                                                    "driver_binding_id": "driver-demo-01"})
            await coord.execute("bind_session", {**base, "started_at": data["started_at"],
                                                "driver_binding_id": "driver-external"})
            interval = {**data["intervals"][0], "import_wh": import_wh, "export_wh": export_wh}
            await coord.execute("add_interval", {**base, "interval": interval})
            args = {**base, "ended_at": interval["end"],
                    "final_import_wh": import_wh, "final_export_wh": export_wh}
            record = await coord.execute("prepare_session", args)
            assert record["mode"] == "embedded_mainnet" and record["network"] == "mainnet"
            assert record["direction"] == direction
            assert record["txid"] is None and record["receipt_id"] is None
            assert record["amount_sats"] is None  # No invented exchange rate.
            assert record["broadcast_enabled"] is False
            assert record["state"] == ("no_payment_due" if direction == "none" else "blocked_live_settlement")
            assert record == await coord.execute("prepare_session", args)
            payload = coord.saved["sessions"][data["session_id"]]["payload"]
            assert payload["operator_binding_id"] == "embedded-operator-mainnet"
            with pytest.raises(HomeAssistantError):
                await coord.execute("request_payment", base)
            with pytest.raises(WalletError, match="disabled"):
                await api.call("POST", f"/v1/settlements/{record['settlement_id']}/request-payment", {})
            changed = {**payload, "net_amount_minor": 2}
            with pytest.raises(WalletError, match="cannot be changed"):
                await api.call("PUT", f"/v1/settlements/{record['settlement_id']}", changed)
            response = await hass.services.async_call(
                "bsv_settlement", "wallet_self_test", {"config_entry_id": entry.entry_id},
                blocking=True, return_response=True)
            assert response["broadcast"] is False and response["txid"] is None
            coord.async_set_updated_data(await coord._async_update_data())
            sensor = SettlementSensor(coord, entry, "operator_wallet_status", "Wallet", None)
            assert sensor.native_value == "broadcast_enabled_approval_required"
            assert sensor.extra_state_attributes["operator_public_key"] == api.identity["public_key"]
            assert "secret_hex" not in sensor.extra_state_attributes
            status = SettlementSensor(coord, entry, "settlement_status", "Status", None)
            assert status.native_value == record["state"]
            assert status.extra_state_attributes["mode"] == "embedded_mainnet"
            assert api.saved["payments"] == {} and api.chain.posts == []
            restored = await load_wallet(hass, entry)
            assert record == await restored.call("GET", f"/v1/settlements/{record['settlement_id']}")
    finally:
        await hass.async_stop(force=True)
