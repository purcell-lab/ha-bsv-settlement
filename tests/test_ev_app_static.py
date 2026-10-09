"""The hosted EV app (apps/ev-app/client) is a static path, like the driver page.

The integration serves the committed build at /bsv_settlement/app/. The app
talks only to the existing same-origin portal API; there is no app server or
Home Assistant token. No network, wallet or chain.
"""
from pathlib import Path
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from homeassistant.components.http.server import _STATIC_CLASSES
from custom_components.bsv_settlement import async_setup

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "custom_components/bsv_settlement/frontend/app"


async def registered_paths(monkeypatch):
    from custom_components.bsv_settlement import grouped_wallet_test
    monkeypatch.setattr(grouped_wallet_test, "install", AsyncMock())
    hass = SimpleNamespace(data={}, bus=SimpleNamespace(async_listen_once=Mock()),
                           http=SimpleNamespace(async_register_static_paths=AsyncMock(), register_view=Mock()),
                           services=SimpleNamespace(async_register=Mock()))
    await async_setup(hass, {})
    (configs,), _ = hass.http.async_register_static_paths.await_args
    return {c.url_path: c for c in configs}


@pytest.mark.asyncio
async def test_app_static_path_is_registered_next_to_the_driver_page(monkeypatch):
    paths = await registered_paths(monkeypatch)
    app = paths["/bsv_settlement/app"]
    assert Path(app.path) == ROOT / "custom_components/bsv_settlement/frontend/app"
    assert app.cache_headers is False
    driver = paths["/bsv_settlement/driver"]
    assert driver.cache_headers is False and Path(driver.path).parent == Path(app.path).parent


@pytest.mark.asyncio
async def test_app_index_and_assets_are_served_byte_identical(monkeypatch):
    config = (await registered_paths(monkeypatch))["/bsv_settlement/app"]
    server = web.Application()
    # The same resource class Home Assistant uses for cache_headers=False.
    server.router.register_resource(_STATIC_CLASSES[config.cache_headers](config.url_path, config.path))
    index = (APP / "index.html").read_text()
    assets = re.findall(r'(?:src|href)="(/bsv_settlement/app/[^"]+)"', index)
    assert "/bsv_settlement/app/app.js" in assets and "/bsv_settlement/app/index.css" in assets
    async with TestClient(TestServer(server)) as client:
        for url in ["/bsv_settlement/app/index.html", *assets, "/bsv_settlement/app/THIRD-PARTY-LICENSES.txt"]:
            response = await client.get(url)
            assert response.status == 200, url
            assert await response.read() == (APP / url.removeprefix("/bsv_settlement/app/")).read_bytes(), url
        assert (await client.get("/bsv_settlement/app/../manifest.json")).status in (403, 404)


def test_shipped_app_build_is_static_and_uses_only_the_portal_api():
    shipped = sorted(p.name for p in APP.iterdir())
    assert shipped == ["THIRD-PARTY-LICENSES.txt", "app.js", "favicon.svg", "index.css", "index.html"]
    bundle = (APP / "app.js").read_text()
    assert "sourceMappingURL" not in bundle
    assert "/api/bsv_settlement/portal" in bundle
    assert "/api/states" not in bundle  # no Home Assistant REST/token path
    assert "@license React" in bundle  # legal comments retained inline
    assert "Open BSV License" in (APP / "THIRD-PARTY-LICENSES.txt").read_text()
