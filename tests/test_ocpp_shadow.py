"""Synthetic OCPP entity observations; no live hardware or funds."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntries, ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.bsv_settlement.ocpp_shadow_ledger import (
    ImportShadowLedger, energy, MAX_EVENTS, MAX_SPANS)
from custom_components.bsv_settlement.ocpp_shadow import OCPPShadowCoordinator, source_binding, METRICS
from custom_components.bsv_settlement.config_flow import BSVSettlementConfigFlow
from custom_components.bsv_settlement.sensor import OCPPShadowSensor
from custom_components.bsv_settlement import async_setup, async_setup_entry, async_unload_entry
from custom_components.bsv_settlement.const import SERVICES
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.session_review import SessionReviews

BINDING = {"connector": "synthetic-single-connector"}
START = datetime(2026, 10, 4, 0, 0, tzinfo=timezone.utc)


def stamp(second):
    return (START + timedelta(seconds=second)).isoformat()


def snapshot(second, value="100", unit="kWh", tx="42", status="Charging", **attrs):
    return {"import": {"value": value, "unit": unit, "ha_updated_at": stamp(second),
                       "context": "Sample.Periodic", **attrs},
            "transaction": {"value": tx}, "status": {"value": status}}


def observe(ledger, second, **kwargs):
    ledger.observe(snapshot(second, **kwargs), stamp(second + .1))


def started():
    ledger = ImportShadowLedger(BINDING)
    observe(ledger, 0)  # activation snapshot excluded
    observe(ledger, 60)
    observe(ledger, 120, value="100.25")
    return ledger


def test_import_observed_after_activation_not_backfilled_or_billed():
    ledger = started()
    s = ledger.summary()
    assert s["current_span"]["observed_import_kwh"] == "0.25"
    assert s["current_span"]["sample_count"] == 2
    assert s["current_span"]["source_timestamp"] is None
    assert s["export_kwh"] is s["net_cost_aud"] is None
    assert not any(s[k] for k in ("billing_eligible", "settlement_owner", "payment_control", "charger_control"))
    assert "latest_session" not in s


@pytest.mark.parametrize("value,unit", [("100250", "Wh"), ("100.25", "kWh"), (".10025", "MWh")])
def test_unit_changes_before_delta(value, unit):
    ledger = started()
    observe(ledger, 180, value=value, unit=unit)
    assert Decimal(ledger.summary()["current_span"]["observed_import_kwh"]) == Decimal(".25")


@pytest.mark.parametrize("value,unit", [
    ("NaN", "kWh"), ("Infinity", "kWh"), ("-1", "kWh"), ("1", "J"),
    ("unknown", "kWh"), ("1e999999", "kWh"), (True, "kWh"), ("", "kWh")])
def test_invalid_meters_cannot_make_energy(value, unit):
    with pytest.raises(ValueError):
        energy(value, unit)
    ledger = started()
    observe(ledger, 180, value=value, unit=unit)
    assert ledger.summary()["current_span"] is None
    assert ledger.summary()["previous_span"]["end_reason"] == "invalid_or_stale_meter"


@pytest.mark.parametrize("status", ["Finishing", "Available", "Occupied", "unknown", "unavailable"])
def test_status_boundary_is_not_final_bill(status):
    ledger = started()
    observe(ledger, 180, status=status, value="100.5")
    s = ledger.summary()
    assert s["current_span"] is None
    assert s["previous_span"]["observed_import_kwh"] == "0.25"
    assert "ended_at" not in s["previous_span"]
    assert s["previous_span"]["billing_eligible"] is False


@pytest.mark.parametrize("status", ["SuspendedEV", "SuspendedEVSE", "Charging"])
def test_suspend_resume_same_transaction_keeps_span(status):
    ledger = started()
    before = ledger.summary()["current_span"]["span_id"]
    observe(ledger, 180, status=status, value="100.25")
    assert ledger.summary()["current_span"]["span_id"] == before


def test_transaction_change_excludes_boundary_and_never_bridges():
    ledger = started()
    old = ledger.summary()["current_span"]["span_id"]
    observe(ledger, 180, tx="43", value="110")
    assert ledger.summary()["current_span"] is None
    observe(ledger, 240, tx="43", value="110.1")
    observe(ledger, 300, tx="43", value="110.3")
    s = ledger.summary()
    assert s["current_span"]["span_id"] != old
    assert s["current_span"]["observed_import_kwh"] == "0.2"
    assert Decimal(s["previous_span"]["observed_import_kwh"]) == Decimal(".25")


def test_duplicate_and_conflicting_timestamp():
    ledger = started()
    saved = deepcopy(ledger.data)
    observe(ledger, 120, value="100.25")
    assert ledger.data == saved
    observe(ledger, 120, value="999")
    assert ledger.summary()["current_span"] is None
    assert "out_of_order_or_conflicting_meter" in ledger.summary()["quality_flags"]


@pytest.mark.parametrize("attrs", [{"context": None}, {"restored": True}])
def test_restored_and_unverified_sample_not_counted(attrs):
    ledger = started()
    observe(ledger, 180, value="101", **attrs)
    assert ledger.summary()["current_span"] is None


@pytest.mark.parametrize("now", [stamp(400), stamp(100)])
def test_stale_or_future_timestamp(now):
    ledger = started()
    ledger.observe(snapshot(180, value="101"), now)
    assert ledger.summary()["current_span"] is None


@pytest.mark.parametrize("second,value,reason", [
    (180, "1", "counter_decreased"), (400, "101", "meter_gap")])
def test_gap_and_reset_exclude_delta_start_partial_baseline(second, value, reason):
    ledger = started()
    observe(ledger, second, value=value)
    s = ledger.summary()
    assert s["previous_span"]["end_reason"] == reason
    assert s["current_span"]["observed_import_kwh"] == "0"


def test_restart_preserves_history_but_never_bridges_missing_energy():
    original = started()
    restarted = ImportShadowLedger(BINDING, deepcopy(original.data))
    observe(restarted, 200, value="110")
    assert restarted.summary()["current_span"] is None
    assert restarted.summary()["previous_span"]["end_reason"] == "restart_gap"
    observe(restarted, 260, value="111")
    observe(restarted, 320, value="111.1")
    assert restarted.summary()["current_span"]["observed_import_kwh"] == "0.1"
    assert restarted.summary()["current_span"]["span_id"] != original.summary()["current_span"]["span_id"]


@pytest.mark.parametrize("change", [
    {"schema": 999}, {"binding": {}}, {"spans": [None]}, {"sequence": -1},
    {"events": [None]}, {"journal_trimmed": None}, {"current": {"span_id": "broken"}}])
def test_invalid_store_rejected(change):
    saved = {**started().data, **change}
    with pytest.raises((ValueError, KeyError, TypeError)):
        ImportShadowLedger(BINDING, saved)


def test_bounded_retention():
    ledger = ImportShadowLedger(BINDING)
    observe(ledger, 0)
    for n in range(MAX_EVENTS + 4):
        observe(ledger, 60 * (n + 1), value=str(100 + n / 10))
    assert len(ledger.data["events"]) == MAX_EVENTS
    assert ledger.summary()["journal_trimmed"]
    for n in range(MAX_SPANS + 3):
        second = 100000 + n * 120
        observe(ledger, second, tx=str(n + 100))
        observe(ledger, second + 60, tx=str(n + 100))
    assert len(ledger.data["spans"]) == MAX_SPANS
    assert ledger.summary()["spans_trimmed"]


def config_entry(domain="ocpp", data=None):
    return ConfigEntry(version=1, minor_version=1, domain=domain, title="Synthetic shadow",
                       data=data or {}, source="user", unique_id=None, options={},
                       discovery_keys=MappingProxyType({}), subentries_data=[])


async def setup_sources(hass):
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    if hasattr(dr, "async_setup"):
        dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    native = config_entry()
    hass.config_entries._entries[native.entry_id] = native
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=native.entry_id, identifiers={("ocpp", "synthetic")})
    sources = {}
    for key, metric in METRICS.items():
        entity = er.async_get(hass).async_get_or_create(
            "sensor", "ocpp", f"ocpp.synthetic.{metric}.sensor",
            config_entry=native, device_id=device.id, suggested_object_id="shadow_test_" + metric)
        sources[key] = entity.entity_id
        value = {"import": "100", "status": "Charging", "transaction": "42"}[key]
        hass.states.async_set(entity.entity_id, value, {
            "unit_of_measurement": "kWh", "context": "Sample.Periodic"})
    return sources


@pytest.mark.asyncio
async def test_binding_config_flow_and_rejection(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        sources = await setup_sources(hass)
        binding = source_binding(hass, sources)
        assert len(binding) == 3
        flow = BSVSettlementConfigFlow()
        flow.hass, flow.context = hass, {}
        data = {k + "_entity": v for k, v in sources.items()}
        result = await flow.async_step_ocpp_shadow(data)
        assert result["step_id"] == "ocpp_shadow_metadata"
        result = await flow.async_step_ocpp_shadow_metadata({})
        assert result["type"] == "create_entry"
        assert result["options"] == {"metadata_binding": {}}
        assert result["data"]["backend"] == "ocpp_import_shadow"
        assert result["data"]["source_binding"] == binding
        wrong = {**sources, "import": sources["transaction"]}
        with pytest.raises(ValueError):
            source_binding(hass, wrong)
        er.async_get(hass).async_update_entity(sources["status"], disabled_by=er.RegistryEntryDisabler.USER)
        bad = await flow.async_step_ocpp_shadow(data)
        assert bad["errors"]["base"] == "invalid_ocpp_shadow_sources"
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_coordinator_store_sensors_and_no_actions(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    coord = None
    try:
        sources = await setup_sources(hass)
        data = {**{k + "_entity": v for k, v in sources.items()},
                "backend": "ocpp_import_shadow", "source_binding": source_binding(hass, sources)}
        entry = config_entry("bsv_settlement", data)
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        coord.async_set_updated_data(await coord._async_update_data())
        sensor = OCPPShadowSensor(coord, entry, "shadow_status", "Status", None)
        assert sensor.native_value == "waiting"
        assert sensor.extra_state_attributes["payment_control"] is False
        assert not hasattr(coord, "api")
        # Exercise the real HA event subscription, not only the pure ledger.
        hass.states.async_set(sources["import"], "100.1", {
            "unit_of_measurement": "kWh", "context": "Sample.Periodic"})
        await hass.async_block_till_done()
        hass.states.async_set(sources["import"], "100.3", {
            "unit_of_measurement": "kWh", "context": "Sample.Periodic"})
        await hass.async_block_till_done()
        energy_sensor = OCPPShadowSensor(coord, entry, "observed_import_kwh", "Energy", "kWh")
        assert energy_sensor.native_value == Decimal(".2")
        for action in set(SERVICES) - {"refresh"}:
            with pytest.raises(HomeAssistantError, match="read-only"):
                await coord.execute(action, {})
        await coord.close()
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        assert coord.ledger.data["schema"] == 3
        er.async_get(hass).async_update_entity(sources["import"], new_unique_id="ocpp.other.energy_active_import_register.sensor")
        coord.observe()
        assert coord.summary()["state"] == "incompatible"
    finally:
        if coord:
            await coord.close()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_entry_lifecycle_forwards_only_sensor_and_unloads(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        sources = await setup_sources(hass)
        data = {**{k + "_entity": v for k, v in sources.items()},
                "backend": "ocpp_import_shadow", "source_binding": source_binding(hass, sources)}
        entry = config_entry("bsv_settlement", data)
        entry._async_set_state(hass, ConfigEntryState.SETUP_IN_PROGRESS, None)
        await async_setup(hass, {})
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        assert await async_setup_entry(hass, entry)
        assert hass.config_entries.async_forward_entry_setups.call_args.args[1] == ["sensor"]
        coord = hass.data["bsv_settlement"][entry.entry_id]
        assert coord.cancel_listener is not None
        assert await async_unload_entry(hass, entry)
        assert coord.cancel_listener is None
        assert entry.entry_id not in hass.data["bsv_settlement"]
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["wrong_metric", "other_connector", "other_device", "missing_state"])
async def test_config_refuses_unusable_or_cross_connector_sources(tmp_path, change):
    hass = HomeAssistant(str(tmp_path))
    try:
        sources = await setup_sources(hass)
        registry = er.async_get(hass)
        if change == "wrong_metric":
            registry.async_update_entity(sources["import"],
                new_unique_id="ocpp.synthetic.energy_active_export_register.sensor")
        elif change == "other_connector":
            registry.async_update_entity(sources["status"],
                new_unique_id="ocpp.synthetic.conn2.status_connector.sensor")
        elif change == "other_device":
            row = registry.async_get(sources["status"])
            device = dr.async_get(hass).async_get_or_create(
                config_entry_id=row.config_entry_id, identifiers={("ocpp", "another")})
            registry.async_update_entity(sources["status"], device_id=device.id)
        else:
            hass.states.async_remove(sources["import"])
        flow = BSVSettlementConfigFlow()
        flow.hass, flow.context = hass, {}
        result = await flow.async_step_ocpp_shadow({k + "_entity": v for k, v in sources.items()})
        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_ocpp_shadow_sources"
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_rejected_store_is_not_saved_or_subscribed(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        sources = await setup_sources(hass)
        entry = config_entry("bsv_settlement", {
            **{k + "_entity": v for k, v in sources.items()},
            "backend": "ocpp_import_shadow", "source_binding": source_binding(hass, sources)})
        await async_setup(hass, {})
        with patch("custom_components.bsv_settlement.ocpp_shadow.ShadowStore") as store:
            store.return_value.async_load = AsyncMock(return_value={"schema": 999})
            store.return_value.async_save = AsyncMock()
            with pytest.raises(ValueError):
                await async_setup_entry(hass, entry)
            store.return_value.async_save.assert_not_called()
        assert entry.entry_id not in hass.data["bsv_settlement"]
    finally:
        await hass.async_stop(force=True)


def shadow_with_export_span():
    """A loaded observer whose summary carries graded export evidence."""
    span = {"span_id": "ocpp-export-shadow-x", "session_id": "any-span",
            "estimate_kwh": "1.307", "grade": "unreliable", "billing_eligible": False}
    data = {"mode": "ocpp_import_shadow", "latest_session": None,
            "export_shadow": {"last_span": span, "current_span": None}}
    return SimpleNamespace(mode="ocpp_import_shadow", async_request_refresh=AsyncMock(),
                           data=data, archive=[span], sources={"import": "sensor.synthetic"})


async def gate_session_review(api, hass):
    reviews = object.__new__(SessionReviews)
    reviews.hass = hass
    await reviews.source("shadow", "any-span")


async def gate_collection(api, hass):
    from custom_components.bsv_settlement.collection import DriverCollections
    await DriverCollections(api).source({
        "proxy_config_entry_id": "shadow", "terms": {"session_id": "any-span"}})


async def gate_session_closure(api, hass):
    from custom_components.bsv_settlement.session_closure import ClosedSessions
    await ClosedSessions(api).inspect({"proxy_config_entry_id": "shadow", "session_id": "any-span"})


async def gate_budget_create(api, hass):
    from custom_components.bsv_settlement.budget import SessionBudgets
    await SessionBudgets(api).create({"proxy_config_entry_id": "shadow"}, "admin")


async def gate_ongoing_credit(api, hass):
    from custom_components.bsv_settlement.ongoing_credit import OngoingCredits
    credits = object.__new__(OngoingCredits)
    credits.api = api
    await credits.configure({"enabled": True, "confirm_ongoing_mainnet_credits": True,
                             "proxy_config_entry_id": "shadow"}, "admin")


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", [gate_session_review, gate_collection, gate_session_closure,
                                  gate_budget_create, gate_ongoing_credit])
async def test_payment_source_rejects_shadow_before_reading_records(gate):
    shadow = shadow_with_export_span()
    hass = SimpleNamespace(data={"bsv_settlement": {"shadow": shadow}})
    api = SimpleNamespace(hass=hass, saved={"ongoing_credit_policy": {}},
                          auto_credits=SimpleNamespace(policy={"enabled": True}))
    # Test the existing financial source boundaries, not a duplicate shadow guard:
    # import or export shadow evidence never becomes a payment record.
    with pytest.raises(WalletError, match="recorder"):
        await gate(api, hass)
    shadow.async_request_refresh.assert_not_called()
    assert api.saved["ongoing_credit_policy"] == {}
