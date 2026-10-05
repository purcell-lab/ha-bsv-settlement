"""Opt-in manifest tests, isolated from live wallets and global HA metadata."""
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from homeassistant.components.frontend import Manifest
from homeassistant.exceptions import HomeAssistantError

from custom_components.bsv_settlement.grouped_wallet_test import (
    DECLARATION, KEY, SERVICE, GroupedWalletTest, GroupedWalletTestView, exact_origin,
)

ORIGIN = "https://charging.example"


def fixture(saved=None):
    manifest = Manifest({"name": "Home Assistant", "icons": [{"src": "/icon.png"}],
                         "start_url": "/?homescreen=1"})
    store = SimpleNamespace(async_load=AsyncMock(return_value=saved), async_save=AsyncMock())
    controller = GroupedWalletTest(store, manifest, manifest.update_key, lambda: ORIGIN)
    return controller, store, manifest


@pytest.mark.asyncio
async def test_default_off_and_no_write():
    c, store, m = fixture()
    await c.load()
    assert c.status()["enabled"] is False
    assert "metanet" not in m.manifest
    store.async_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_optin_preserves_pwa_metadata_and_disable_does_not_revoke_wallet():
    c, store, m = fixture()
    before = copy.deepcopy(m.manifest)
    await c.configure(enabled=True, origin=ORIGIN, confirm_shared_origin=True)
    assert m.manifest == before | {"metanet": DECLARATION}
    assert c.status()["enabled"]
    assert c.status()["spending_permission"] == "not_verified"
    await c.configure(enabled=False)
    assert m.manifest == before | {"metanet": None}
    assert c.status()["enabled"] is False
    assert store.async_save.await_count == 2


@pytest.mark.asyncio
async def test_reload_applies_saved_optin_only_at_approved_origin():
    c, _, m = fixture({"enabled": True, "origin": ORIGIN})
    await c.load()
    assert c.status()["enabled"] and m.manifest["metanet"] == DECLARATION
    c.external_url = lambda: "https://other.example"
    assert not c.status()["enabled"]
    c2, _, m2 = fixture({"enabled": True, "origin": "https://other.example"})
    with pytest.raises(HomeAssistantError):
        await c2.load()
    assert "metanet" not in m2.manifest


@pytest.mark.asyncio
@pytest.mark.parametrize("namespace", ["metanet", "babbage"])
async def test_other_declarations_are_never_overwritten(namespace):
    c, store, m = fixture()
    m.update_key(namespace, {"other": "application"})
    before = copy.deepcopy(m.manifest)
    with pytest.raises(HomeAssistantError):
        await c.configure(enabled=True, origin=ORIGIN, confirm_shared_origin=True)
    assert m.manifest == before
    store.async_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_save_does_not_publish():
    c, store, m = fixture()
    store.async_save.side_effect = OSError("storage unavailable")
    with pytest.raises(OSError):
        await c.configure(enabled=True, origin=ORIGIN, confirm_shared_origin=True)
    assert "metanet" not in m.manifest


@pytest.mark.asyncio
async def test_explicit_confirmation_required_and_external_change_is_not_overwritten():
    c, _, m = fixture()
    with pytest.raises(HomeAssistantError):
        await c.configure(enabled=True, origin=ORIGIN)
    await c.configure(enabled=True, origin=ORIGIN, confirm_shared_origin=True)
    m.update_key("metanet", {"other": "replacement"})
    assert not c.status()["enabled"]
    await c.configure(enabled=False)
    assert m.manifest["metanet"] == {"other": "replacement"}


@pytest.mark.parametrize("value", [
    "http://charging.example", ORIGIN+"/", ORIGIN+"/driver", ORIGIN+"?a=b",
    "https://user:pass@charging.example", "https://charging.example:443",
    "https://charging.example:invalid", None, "not-a-url",
])
def test_origin_is_exact_https_host(value):
    with pytest.raises(HomeAssistantError):
        exact_origin(value)


@pytest.mark.asyncio
async def test_public_status_no_wallet_or_private_data():
    c, _, _ = fixture()
    app = web.Application()
    app.router.add_get("/test", GroupedWalletTestView(c).get)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/test")
        assert response.headers["Cache-Control"] == "no-store"
        assert await response.json() == {
            "enabled": False, "origin": None, "monthly_limit_sats": 30000,
            "spending_permission": "not_verified"}


@pytest.mark.asyncio
async def test_registered_service_requires_admin_and_is_idempotent(tmp_path, monkeypatch):
    from homeassistant.core import HomeAssistant, Context
    import homeassistant.components.frontend as frontend
    from custom_components.bsv_settlement import grouped_wallet_test as module

    hass = HomeAssistant(str(tmp_path))
    hass.config.external_url = ORIGIN
    views = []
    hass.http = SimpleNamespace(register_view=views.append)
    c, store, manifest = fixture()
    monkeypatch.setattr(module, "Store", lambda *args: store)
    monkeypatch.setattr(frontend, "MANIFEST_JSON", manifest)
    monkeypatch.setattr(frontend, "add_manifest_json_key", manifest.update_key)
    hass.auth = SimpleNamespace(async_get_user=AsyncMock(
        return_value=SimpleNamespace(is_admin=False)))
    await module.install(hass)
    await module.install(hass)
    assert len(views) == 1
    assert not hass.data[KEY].status()["enabled"]
    data = {"enabled": True, "origin": ORIGIN, "confirm_shared_origin": True}
    for context in (Context(), Context(user_id="non-admin")):
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call("bsv_settlement", SERVICE, data,
                                           blocking=True, context=context)
    store.async_save.assert_not_awaited()
    hass.auth.async_get_user.return_value.is_admin = True
    result = await hass.services.async_call(
        "bsv_settlement", SERVICE, data, blocking=True, return_response=True,
        context=Context(user_id="admin"))
    assert result["enabled"]
    assert result["spending_permission"] == "not_verified"
    assert manifest.manifest["metanet"] == DECLARATION


@pytest.mark.asyncio
async def test_bad_saved_configuration_does_not_break_install(tmp_path, monkeypatch):
    from homeassistant.core import HomeAssistant
    import homeassistant.components.frontend as frontend
    from custom_components.bsv_settlement import grouped_wallet_test as module

    hass = HomeAssistant(str(tmp_path))
    hass.config.external_url = ORIGIN
    hass.http = SimpleNamespace(register_view=lambda view: None)
    _, store, manifest = fixture({"enabled": True, "origin": "https://other.example"})
    monkeypatch.setattr(module, "Store", lambda *args: store)
    monkeypatch.setattr(frontend, "MANIFEST_JSON", manifest)
    monkeypatch.setattr(frontend, "add_manifest_json_key", manifest.update_key)
    await module.install(hass)
    assert not hass.data[KEY].status()["enabled"]
    assert "metanet" not in manifest.manifest
    store.async_save.assert_not_awaited()
