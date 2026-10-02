"""Proxy observations never authorise wallet or charger actions."""
from copy import deepcopy
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant, State
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components.bsv_settlement.proxy_ledger import build_records, select_prices, instant
from custom_components.bsv_settlement.proxy import ProxyCoordinator, SOURCE_KEYS, normalize
from custom_components.bsv_settlement.sensor import ProxySensor
from custom_components.bsv_settlement.config_flow import BSVSettlementConfigFlow


def stamp(minute):
    return f"2026-10-02T10:{minute:02d}:00+10:00"


def row(minute, value, unit=None, **attrs):
    return {"t": stamp(minute), "value": str(value),
            **({"unit": unit} if unit else {}), **attrs}


def fixture():
    return {
        "state": [row(0, "Idle"), row(1, "Occupied"), row(2, "Charging"),
                  row(5, "Discharging"), row(8, "Occupied"), row(9, "Charging"),
                  row(10, "Ended")],
        "import": [row(0, "1", "MWh"), row(5, "1.001", "MWh"), row(10, "1.002", "MWh")],
        "export": [row(0, "2", "MWh"), row(8, "2.0005", "MWh")],
        "import_price": [row(0, ".10", "$/kWh", start=stamp(0), end=stamp(15), estimate=False)],
        "export_price": [row(0, "-.02", "$/kWh", start=stamp(0), end=stamp(15), estimate=False)],
    }


def test_proxy_direction_changes_pauses_and_negative_feed_in():
    records = build_records(fixture(), "sensor.state", stamp(15))
    assert len(records) == 1
    r = records[0]
    assert r["import_kwh"] == 2 and r["export_kwh"] == .5
    assert r["net_cost_aud"] == .21
    assert r["ended_at"] == stamp(10)
    assert r["transaction_id_source"] == "proxy_generated"
    assert not r["billing_eligible"]
    assert records == build_records(fixture(), "sensor.state", stamp(15))


def test_proxy_reset_recovery_is_not_energy():
    h = fixture()
    h["export"].insert(1, row(3, "0", "MWh"))
    h["export"].insert(2, row(4, "2", "MWh"))
    r = build_records(h, "sensor.state", stamp(15))[0]
    assert r["export_kwh"] == .5
    assert "export:counter_decrease_quarantined" in r["quality_flags"]
    h["export"] = h["export"][:2]
    r = build_records(h, "sensor.state", stamp(15))[0]
    assert r["export_kwh"] is None and r["net_cost_aud"] is None


def test_proxy_missing_price_unknown_counter_and_units_fail_closed():
    h = fixture()
    h["import_price"] = []
    r = build_records(h, "sensor.state", stamp(15))[0]
    assert r["net_cost_aud"] is None and Decimal(r["unpriced_import_wh"]) == 2000
    h = fixture()
    h["import"][-1]["value"] = "unknown"
    assert build_records(h, "sensor.state", stamp(15))[0]["net_cost_aud"] is None
    h = fixture()
    h["import"][0]["unit"] = "kWh"
    assert build_records(h, "sensor.state", stamp(15))[0]["import_kwh"] is None


def test_proxy_price_effective_period_not_arrival_and_final_preferred():
    h = fixture()
    h["import_price"] = [
        row(2, ".10", "$/kWh", start=stamp(0), end=stamp(5), estimate=False),
        row(6, ".30", "$/kWh", start=stamp(5), end=stamp(15), estimate=False),
        row(7, ".80", "$/kWh", start=stamp(5), end=stamp(15), estimate=True),
    ]
    r = build_records(h, "sensor.state", stamp(15))[0]
    assert r["net_cost_aud"] == .41
    h["import_price"][0]["end"] = stamp(7)
    assert select_prices(h["import_price"])[1] == ["overlapping_tariff_periods"]
    assert build_records(h, "sensor.state", stamp(15))[0]["net_cost_aud"] is None


def test_proxy_partial_start_and_unknown_state_are_explicit():
    h = fixture()
    h["state"] = h["state"][2:]
    r = build_records(h, "sensor.state", stamp(15))[0]
    assert "history_starts_mid_session" in r["quality_flags"]
    assert r["net_cost_aud"] is None
    h = fixture()
    h["state"].insert(3, row(3, "unavailable"))
    r = build_records(h, "sensor.state", stamp(15))[0]
    assert "unknown_running_state" in r["quality_flags"]
    assert not r["billing_eligible"]


def test_native_datetime_price_attributes_are_normalized():
    state = State("sensor.price", ".1", {
        "unit_of_measurement": "$/kWh", "start_time": instant(stamp(0)),
        "end_time": instant(stamp(5)), "estimate": False})
    prices, issues = select_prices([normalize(state)])
    assert not issues and len(prices) == 1
    assert prices[0]["rate"] == Decimal(".1")


def config_entry():
    return ConfigEntry(version=1, minor_version=1, domain="bsv_settlement",
                       title="Charging sessions", data={"backend": "sensor_proxy", **{
                           k + "_entity": "sensor.proxy_" + k for k in SOURCE_KEYS}},
                       source="user", unique_id="proxy-test", options={},
                       discovery_keys=MappingProxyType({}), subentries_data=[])


@pytest.mark.asyncio
async def test_real_ha_proxy_storage_restart_and_read_only(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path / "ha"))
    dt_util.set_default_time_zone(dt_util.get_time_zone("Australia/Brisbane"))
    entry = config_entry()
    coords = []
    history = fixture()
    raw = {}
    for key, rows in history.items():
        entity = entry.data[key + "_entity"]
        raw[entity] = [
            State(entity, r["value"], {
                **({"unit_of_measurement": r["unit"]} if "unit" in r else {}),
                **{field: r[k] for k, field in (("start", "start_time"), ("end", "end_time"),
                                               ("estimate", "estimate")) if k in r},
            }, last_changed=instant(r["t"]), last_updated=instant(r["t"])) for r in rows
        ]
        last = raw[entity][-1]
        hass.states.async_set(entity, last.state, dict(last.attributes))

    async def get_history(*args, **kwargs):
        return raw
    monkeypatch.setattr("homeassistant.components.recorder.get_instance",
                        lambda hass: SimpleNamespace(async_add_executor_job=get_history))
    try:
        c = ProxyCoordinator(hass, entry)
        coords.append(c)
        await c.load()
        c.async_set_updated_data(await c._async_update_data())
        original = c.data["latest_session"]["ocpp_transaction_id"]
        assert c.data["latest_session"]["net_cost_aud"] == .21
        sensor = ProxySensor(c, entry, "transaction_id", "Transaction", None)
        assert sensor.native_value == original
        with pytest.raises(HomeAssistantError, match="read-only"):
            await c.execute("request_payment", {})
        with pytest.raises(HomeAssistantError, match="read-only"):
            await c.execute("broadcast_operator_payment", {})
        await c.close()
        assert c.cancel_listener is None
        restored = ProxyCoordinator(hass, entry)
        coords.append(restored)
        await restored.load()
        restored.async_set_updated_data(await restored._async_update_data())
        assert restored.data["latest_session"]["ocpp_transaction_id"] == original
        assert restored.data["latest_session"]["net_cost_aud"] == .21
        hass.states.async_set(entry.data["import_price_entity"], "unavailable")
        await hass.async_block_till_done()
        restored.async_set_updated_data(await restored._async_update_data())
        assert restored.data["state"] == "degraded"
        assert restored.data["latest_session"]["net_cost_aud"] is None
    finally:
        for c in coords:
            await c.close()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_proxy_flow_rejects_wrong_units(tmp_path):
    hass = HomeAssistant(str(tmp_path / "ha"))
    try:
        flow = BSVSettlementConfigFlow()
        flow.hass = hass
        flow.context = {}
        result = await flow.async_step_user({"backend": "sensor_proxy"})
        assert result["step_id"] == "proxy"
        data = {k + "_entity": "sensor.fake_" + k for k in SOURCE_KEYS}
        for entity in data.values():
            hass.states.async_set(entity, "0", {"unit_of_measurement": "kWh"})
        result = await flow.async_step_proxy(data)
        assert result["errors"]["base"] == "invalid_proxy_units"
    finally:
        await hass.async_stop(force=True)
