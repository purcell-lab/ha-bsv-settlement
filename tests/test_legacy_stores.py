"""#114: guarded removal of stores left by deleted mock/testnet entries. No network."""
import json
import logging
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

pytest.importorskip("homeassistant")
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.bsv_settlement import async_setup
from test_upgrade_rollback import MAINNET, MOCK, PROXY, TESTNET, materialize, no_external_network  # noqa: F401

pytestmark = pytest.mark.asyncio
ADMIN = Context(user_id="admin")


async def setup(tmp_path):
    identities = materialize(tmp_path, entries=())
    hass = HomeAssistant(str(tmp_path))
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    hass.auth = SimpleNamespace(async_get_user=AsyncMock(
        side_effect=lambda user_id: SimpleNamespace(is_admin=user_id == "admin")))
    await async_setup(hass, {})
    return hass, identities


def files(tmp_path):
    return {p.name: p.read_bytes() for p in (Path(tmp_path) / ".storage").iterdir()}


async def refused_by_schema(call):
    # HA may substitute its own voluptuous implementation; match the error by name.
    with pytest.raises(Exception) as caught:
        await call
    assert "Invalid" in type(caught.value).__name__


async def purge(hass, entry_id, context=ADMIN, **extra):
    return await hass.services.async_call(
        "bsv_settlement", "purge_removed_backend_stores", {"entry_id": entry_id, "confirm": True, **extra},
        blocking=True, return_response=True, context=context)


@pytest_asyncio.fixture
async def env(tmp_path):
    hass, identities = await setup(tmp_path)
    yield hass, identities
    await hass.async_stop(force=True)


async def test_removes_exactly_the_three_testnet_stores_once(env, tmp_path, caplog):
    hass, identities = env
    before = files(tmp_path)
    caplog.set_level(logging.DEBUG)
    result = await purge(hass, TESTNET)
    keys = [f"bsv_settlement.{TESTNET}", f"bsv_settlement.embedded.{TESTNET}",
            f"bsv_settlement.operator_key.{TESTNET}"]
    assert result == {"entry_id": TESTNET, "removed": keys}
    after = files(tmp_path)
    assert set(before) - set(after) == set(keys)
    assert after == {k: v for k, v in before.items() if k not in keys}  # Nothing else touched.
    assert identities["testnet"]["secret_hex"] not in caplog.text
    assert await purge(hass, TESTNET) == {"entry_id": TESTNET, "removed": []}  # Idempotent.
    assert files(tmp_path) == after


async def test_mock_coordinator_store_only(env, tmp_path):
    hass, _ = env
    assert (await purge(hass, MOCK))["removed"] == [f"bsv_settlement.{MOCK}"]


async def test_testnet_key_without_ledger_is_removed(env, tmp_path):
    hass, _ = env
    for name in (f"bsv_settlement.{TESTNET}", f"bsv_settlement.embedded.{TESTNET}"):
        (Path(tmp_path) / ".storage" / name).unlink()
    assert (await purge(hass, TESTNET))["removed"] == [f"bsv_settlement.operator_key.{TESTNET}"]


async def test_refuses_non_admin_and_no_user(env, tmp_path):
    hass, _ = env
    before = files(tmp_path)
    for context in (None, Context(user_id="nonadmin")):
        with pytest.raises(HomeAssistantError, match="administrator"):
            await purge(hass, TESTNET, context=context)
    assert files(tmp_path) == before


async def test_refuses_unconfirmed(env, tmp_path):
    hass, _ = env
    before = files(tmp_path)
    for data in ({"entry_id": TESTNET}, {"entry_id": TESTNET, "confirm": False}):
        await refused_by_schema(hass.services.async_call(
            "bsv_settlement", "purge_removed_backend_stores", data,
            blocking=True, return_response=True, context=ADMIN))
    await refused_by_schema(purge(hass, "../core.config_entries"))
    assert files(tmp_path) == before


@pytest.mark.parametrize("entry_id", [TESTNET, MOCK, PROXY, MAINNET])
async def test_refuses_existing_entry(env, tmp_path, entry_id):
    hass, _ = env
    entry = ConfigEntry(
        version=1, minor_version=1, domain="bsv_settlement", title="Legacy",
        data={"backend": "embedded_testnet"}, source="user", unique_id=None, options={},
        discovery_keys=MappingProxyType({}), subentries_data=[], entry_id=entry_id)
    hass.config_entries._entries[entry_id] = entry
    before = files(tmp_path)
    with pytest.raises(HomeAssistantError, match="still exists"):
        await purge(hass, entry_id)
    assert files(tmp_path) == before


async def test_refuses_mainnet_and_other_backends(env, tmp_path):
    hass, _ = env
    storage = Path(tmp_path) / ".storage"
    before = files(tmp_path)
    for entry_id, reason in ((MAINNET, "ledger_checkpoint"), (PROXY, "proxy")):
        with pytest.raises(HomeAssistantError, match=reason):
            await purge(hass, entry_id)
    # Even without its witness, a mainnet key is refused.
    (storage / f"bsv_settlement.ledger_checkpoint.{MAINNET}").unlink()
    with pytest.raises(HomeAssistantError, match="not a testnet key"):
        await purge(hass, MAINNET)
    # A mainnet coordinator store left alone is refused too.
    mainnet_drafts = json.loads((storage / f"bsv_settlement.{TESTNET}").read_text())
    for session in mainnet_drafts["data"]["sessions"].values():
        session["payload"]["operator_binding_id"] = "embedded-operator-mainnet"
    (storage / "bsv_settlement.01ORPHANMAINNETDRAFTS00000").write_text(json.dumps(mainnet_drafts))
    with pytest.raises(HomeAssistantError, match="mainnet"):
        await purge(hass, "01ORPHANMAINNETDRAFTS00000")
    # Draft-only (no payload) sessions without wallet stores need the removed mock binding.
    for session in mainnet_drafts["data"]["sessions"].values():
        session.pop("payload"), session.pop("remote", None)
    (storage / "bsv_settlement.01ORPHANMAINNETDRAFTS00000").write_text(json.dumps(mainnet_drafts))
    with pytest.raises(HomeAssistantError, match="mainnet"):
        await purge(hass, "01ORPHANMAINNETDRAFTS00000")
    # A testnet-labelled key whose secret does not derive that identity is refused.
    key_path = storage / f"bsv_settlement.operator_key.{TESTNET}"
    key = json.loads(key_path.read_text())
    key["data"]["address"] = "1BoatSLRHtKNngkdXEeobR76b53LETtpyT"
    key_path.write_text(json.dumps(key))
    with pytest.raises(HomeAssistantError, match="not a testnet key"):
        await purge(hass, TESTNET)
    after = files(tmp_path)
    del after["bsv_settlement.01ORPHANMAINNETDRAFTS00000"]
    assert {k: v for k, v in after.items() if k != key_path.name} == {
        k: v for k, v in before.items()
        if k not in (key_path.name, f"bsv_settlement.ledger_checkpoint.{MAINNET}")}


async def test_refuses_unrecognised_store(env, tmp_path):
    hass, _ = env
    path = Path(tmp_path) / ".storage" / f"bsv_settlement.embedded.{TESTNET}"
    path.write_text("{not json")
    before = files(tmp_path)
    with pytest.raises(HomeAssistantError, match="not a recognised store"):
        await purge(hass, TESTNET)
    assert files(tmp_path) == before
