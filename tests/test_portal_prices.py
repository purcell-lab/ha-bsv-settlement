"""Anonymous tariffs are public, read-only and independent of driver records."""
import copy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from custom_components.bsv_settlement.portal import current_prices
from custom_components.bsv_settlement.session_review import now
from test_portal import fixture, post, ORIGIN
from test_budget import no_network

pytestmark = pytest.mark.asyncio


def rates(hass):
    attrs = {"unit_of_measurement": "$/kWh", "estimate": False,
             "start_time": (now()-timedelta(minutes=5)).isoformat(),
             "end_time": (now()+timedelta(minutes=5)).isoformat()}
    hass.states.async_set("sensor.demo_import_price", "0.285", attrs)
    hass.states.async_set("sensor.demo_export_price", "-0.052", attrs)
    return attrs


async def test_public_prices_no_cookie_private_data_or_payment_effects(tmp_path):
    hass, api, _, _, _, view, _, client = await fixture(tmp_path)
    rates(hass)
    # Public tariffs cannot depend on registration or historical wallet data.
    api.saved["session_budgets"] = {}
    before, posts = copy.deepcopy(api.saved), list(api.chain.posts)
    try:
        response = await post(client, view, "prices")
        data = await response.json()
        assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
        assert not response.cookies and not view.state.sessions
        assert set(data) == {"checked_at", "valid", "import", "export"}
        assert data["import"]["aud_per_kwh"] == "0.285"
        assert data["export"]["aud_per_kwh"] == "-0.052"
        for item in (data["import"], data["export"]):
            assert set(item) == {"available", "aud_per_kwh", "start", "end", "estimate"}
        assert (await post(client, view, "sessions")).status == 401
        assert api.saved == before and api.chain.posts == posts
    finally:
        await client.close()


@pytest.mark.parametrize("case", ["no_proxy", "multiple_proxies", "missing_source", "stale", "wrong_unit", "unavailable"])
async def test_public_prices_do_not_guess_site_or_invent_values(tmp_path, case):
    hass, api, _, _, _, view, _, client = await fixture(tmp_path)
    attrs = rates(hass)
    proxy = hass.data["bsv_settlement"]["proxy-entry"]
    try:
        if case == "no_proxy":
            hass.data["bsv_settlement"].pop("proxy-entry")
        elif case == "multiple_proxies":
            hass.data["bsv_settlement"]["other"] = SimpleNamespace(mode="sensor_proxy", sources=proxy.sources)
        elif case == "missing_source":
            proxy.sources = {}
        elif case == "stale":
            hass.states.async_set("sensor.demo_import_price", "0.285",
                attrs | {"end_time": (now()-timedelta(minutes=2)).isoformat()})
        elif case == "wrong_unit":
            hass.states.async_set("sensor.demo_import_price", "0.285", attrs | {"unit_of_measurement": "c/kWh"})
        else:
            hass.states.async_set("sensor.demo_import_price", "unavailable", attrs)
        data = current_prices(api)
        assert not data["valid"] and data["import"] == {"available": False}
    finally:
        await client.close()


async def test_public_prices_preserve_origin_and_input_guards(tmp_path):
    _, _, _, _, _, view, _, client = await fixture(tmp_path)
    try:
        for headers in ({}, {"Origin": "https://other.example.com"},
                        {"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"}):
            r = await client.post(view.url, json={"action": "prices"}, headers=headers)
            assert r.status == 403
    finally:
        await client.close()
