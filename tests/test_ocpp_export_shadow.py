"""Synthetic derived OCPP export observations; no live charger, wallet or funds."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import MappingProxyType

import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.bsv_settlement.ocpp_export_shadow_ledger import (
    DEFAULT_GRADING, ExportShadowLedger, grade, grading_rules, validate_section)
from custom_components.bsv_settlement.ocpp_shadow_ledger import ImportShadowLedger, migrate
from custom_components.bsv_settlement.ocpp_shadow import (
    EXPORT, EXPORT_FIELDS, GRADING_FIELDS, METRICS, REFERENCE_FIELD, OCPPShadowCoordinator,
    ShadowStore, export_binding, reference_binding, reference_kwh, source_binding, suggest_export)
from custom_components.bsv_settlement.config_flow import (
    BSVSettlementConfigFlow, OCPPShadowOptionsFlow, shadow_export_options)
from custom_components.bsv_settlement.sensor import OCPPExportShadowSensor

BINDING = {"register": {"unique_id": "ocpp.charger.energy_active_export_register.sensor"}}
START = datetime(2026, 10, 4, 0, 0, tzinfo=timezone.utc)
DERIVED = "derived_from_negative_import"
FINANCIAL = ("billing_eligible", "settlement_owner", "payment_control", "charger_control")
# Live stepped V2G run: (seconds, signed import W). Negative import is discharge.
V2G = [(0, 16527), (60, -249), (120, -269), (180, -359), (240, -229), (300, -7048),
       (361, -7269), (421, -7267), (481, -7332), (541, -7601), (601, -18819),
       (661, -21839), (721, -7)]
INVERTER_KWH = Decimal("1.250")


def stamp(second):
    return (START + timedelta(seconds=second)).isoformat()


def simulate(readings, start=Decimal("10"), deadband=100):
    """Mirror the fork: trapezoid of clamped export power, min/max bounds."""
    rows, register, lower, upper, steps, previous = [], start, start, start, 0, None
    for second, watts in readings:
        export = Decimal(max(0, -watts))
        if previous is not None:
            hours = Decimal(second - previous[0]) / 3600 / 1000
            register += (previous[1] + export) / 2 * hours
            lower += min(previous[1], export) * hours
            upper += max(previous[1], export) * hours
            steps += 1
        flow = "idle" if abs(watts) < deadband else "export" if watts < 0 else "import"
        rows.append({"second": second, "register": register, "lower": lower, "upper": upper,
                     "steps": steps, "flow": flow})
        previous = (second, export)
    return rows


def snap(row, tx="42", status="Charging", source=DERIVED, bounds=True, charger=True,
         unit="kWh", reference=None, flow_at=None, **attrs):
    attributes = {"method": "trapezoidal_integration", "step_intervals": row["steps"],
                  "last_interval_s": 60, "max_sample_gap_s": 120, **attrs}
    if source is not None:
        attributes.update(source=source, estimated=True)
    if bounds:
        attributes.update(energy_lower_bound_kwh=float(row["lower"]),
                          energy_upper_bound_kwh=str(row["upper"]))
    if charger:
        attributes["last_sample_timestamp"] = stamp(row["second"] - 0.5)
    value = row["register"] * 1000 if unit == "Wh" else row["register"]
    result = {
        "status": {"value": status}, "transaction": {"value": tx},
        "export": {"value": str(value), "unit": unit, "ha_updated_at": stamp(row["second"]),
                   "attributes": attributes},
        "flow": {"value": row["flow"], "ha_updated_at": flow_at or stamp(row["second"])},
        "provenance_flags": ["context_defaulted_by_integration", "transport_unencrypted"]}
    if reference is not None:
        result["reference"] = {"kwh": str(reference)}
    return result


def feed(ledger, rows, **kwargs):
    for row in rows:
        ledger.observe(snap(row, **kwargs), stamp(row["second"] + .1))


def end(ledger, second, **kwargs):
    row = {"second": second, "register": Decimal(0), "lower": 0, "upper": 0, "steps": 0,
           "flow": "idle"}
    ledger.observe(snap(row, status="Available", **kwargs), stamp(second + .1))
    return ledger.summary()["last_span"]


def warmed(readings=V2G, **kwargs):
    """Observer started one sample earlier, so the first replay sample is a baseline."""
    rows = simulate([(readings[0][0] - 60, 16000), *readings])
    ledger = ExportShadowLedger(binding=BINDING, **kwargs)
    return ledger, rows


def steady(seconds=1200, watts=7200, wobble=50):
    return [(t, -(watts + (wobble if n % 2 else -wobble))) for n, t in enumerate(range(0, seconds + 1, 60))]


def test_live_v2g_replay_graded_unreliable_with_bounds_bracketing_inverter():
    ledger, rows = warmed()
    feed(ledger, rows)
    current = ledger.summary()["current_span"]
    assert current["grade"] is None  # provisional while open
    span = end(ledger, 781)
    estimate = Decimal(span["estimate_kwh"])
    assert estimate.quantize(Decimal("0.001")) == Decimal("1.307")
    assert Decimal(span["lower_kwh"]) < INVERTER_KWH < Decimal(span["upper_kwh"])
    assert (estimate - INVERTER_KWH) / INVERTER_KWH > Decimal("0.045")  # +4.5% live
    spread = (Decimal(span["upper_kwh"]) - Decimal(span["lower_kwh"])) / estimate
    assert Decimal(span["spread"]) == spread.quantize(Decimal("0.000001"))
    assert spread > Decimal("0.15") and span["grade"] == "unreliable"
    assert span["source_label"] == "estimated"
    assert span["sample_count"] == 13 and span["step_intervals"] == 12
    assert span["flow_direction_counts"] == {"import": 1, "export": 11, "idle": 1, "unknown": 0}
    # Stepping and the final -7 W noise sample raise no anomaly.
    assert span["anomaly_flags"] == []
    assert span["first_source_timestamp"] == stamp(-0.5)
    assert span["last_source_timestamp"] == stamp(720.5)
    assert span["first_meter_ha_updated_at"] == stamp(0)
    assert span["max_gap_s"] == "61"
    assert span["end_reason"] == "identity_or_lifecycle_boundary"
    assert span["grading"] == DEFAULT_GRADING
    assert {"context_defaulted_by_integration", "transport_unencrypted"} <= set(
        span["provenance_flags"])
    assert span["billing_eligible"] is False and span["settlement_owner"] is None
    summary = ledger.summary()
    assert summary["latest_grade"] == "unreliable"
    assert summary["grade_counts"]["unreliable"] == 1
    assert not any(summary[k] for k in FINANCIAL)
    assert summary["export_credit_aud"] is summary["net_cost_aud"] is None


def test_steady_export_within_tolerance_and_thresholds_configurable():
    ledger, rows = warmed(steady())
    feed(ledger, rows)
    span = end(ledger, 1300)
    assert span["grade"] == "within_tolerance"
    assert Decimal(span["spread"]) < Decimal("0.05")
    assert Decimal(span["estimate_kwh"]) == Decimal("2.4")
    strict = {"min_energy_kwh": "0.1", "tolerance_pct": "1", "wide_pct": "2"}
    ledger, rows = warmed(steady(), grading=strict)
    feed(ledger, rows)
    assert end(ledger, 1300)["grade"] == "wide"
    assert grade(span, grading_rules({"tolerance_pct": "0.1", "wide_pct": "0.5"}))[0] == "unreliable"


def test_tiny_span_insufficient_energy_and_noise_never_flags():
    ledger, rows = warmed([(0, -249), (60, -249), (120, -58), (180, -7)])
    feed(ledger, rows)
    span = end(ledger, 240)
    assert Decimal(span["estimate_kwh"]) < Decimal("0.1")
    assert span["grade"] == "insufficient_energy" and span["spread"] is None
    assert span["anomaly_flags"] == []
    ledger, rows = warmed([(0, 3000), (60, 3000), (120, 3000)])
    feed(ledger, rows)
    span = end(ledger, 180)
    assert span["estimate_kwh"] == "0" and span["grade"] == "insufficient_energy"


def test_missing_bounds_ungraded():
    ledger, rows = warmed(steady())
    feed(ledger, rows, bounds=False)
    span = end(ledger, 1300)
    assert span["grade"] == "ungraded" and span["lower_kwh"] is span["upper_kwh"] is None
    assert "bounds_unavailable" in span["quality_flags"]
    ledger, rows = warmed(steady())
    feed(ledger, rows[:5])
    feed(ledger, rows[5:6], bounds=False)
    feed(ledger, rows[6:])
    assert end(ledger, 1300)["grade"] == "ungraded"  # one gap in bounds spoils the span


def test_native_register_without_derivation_attrs_is_unverified():
    ledger, rows = warmed(steady())
    feed(ledger, rows, source=None, bounds=False)
    span = end(ledger, 1300)
    assert span["source_label"] == "native_unverified"
    assert span["grade"] == "ungraded"
    assert "native_export_unverified" in span["quality_flags"]
    ledger, rows = warmed(steady())
    feed(ledger, rows[:4])
    feed(ledger, rows[4:], source="native")
    assert ledger.summary()["last_span"]["end_reason"] == "source_label_changed"


def test_register_decrease_closes_span_with_anomaly():
    ledger, rows = warmed(steady())
    feed(ledger, rows[:6])
    reset = dict(rows[6], register=Decimal("1"), lower=Decimal("1"), upper=Decimal("1"))
    feed(ledger, [reset])
    summary = ledger.summary()
    assert summary["last_span"]["end_reason"] == "register_decrease"
    assert "register_decrease" in summary["last_span"]["anomaly_flags"]
    assert summary["current_span"]["estimate_kwh"] == "0"
    assert summary["current_span"]["register_start_kwh"] == "1"
    assert any(e["kind"] == "excluded_delta" and e["reason"] == "register_decrease"
               for e in ledger.data["events"])


def test_gap_never_bridged_and_fork_gap_attribute_respected():
    ledger, rows = warmed(steady())
    feed(ledger, rows[:5])
    jumped = [dict(r, second=r["second"] + 400) for r in rows[5:8]]
    feed(ledger, jumped)
    summary = ledger.summary()
    assert summary["last_span"]["end_reason"] == "meter_gap"
    gap_delta = rows[5]["register"] - rows[4]["register"]
    assert Decimal(summary["current_span"]["estimate_kwh"]) == rows[7]["register"] - rows[5]["register"]
    assert Decimal(summary["last_span"]["estimate_kwh"]) == rows[4]["register"] - rows[1]["register"]
    assert gap_delta > 0
    # 240 s: closed under the 180 s floor, kept when the fork reports 300 s gaps.
    for max_gap, closed in ((120, True), (300, False)):
        ledger, rows = warmed(steady())
        feed(ledger, rows[:5], max_sample_gap_s=max_gap)
        feed(ledger, [dict(r, second=r["second"] + 180) for r in rows[5:7]],
             max_sample_gap_s=max_gap)
        assert (ledger.summary()["last_span"] is not None) is closed


def test_stale_register_closes_without_new_sample():
    ledger, rows = warmed(steady())
    feed(ledger, rows[:4])
    ledger.observe(snap(rows[3]), stamp(rows[3]["second"] + 200))
    assert ledger.summary()["last_span"]["end_reason"] == "meter_gap"
    assert ledger.summary()["current_span"] is None


def test_restart_closes_and_never_bridges():
    ledger, rows = warmed(steady())
    feed(ledger, rows[:6])
    restarted = ExportShadowLedger(deepcopy(ledger.data), BINDING)
    feed(restarted, rows[8:9])
    summary = restarted.summary()
    assert summary["current_span"] is None
    assert summary["last_span"]["end_reason"] == "restart_gap"
    feed(restarted, rows[9:11])
    current = restarted.summary()["current_span"]
    assert Decimal(current["estimate_kwh"]) == rows[10]["register"] - rows[9]["register"]
    assert current["span_id"] != ledger.summary()["current_span"]["span_id"]


def test_transaction_change_and_suspension():
    ledger, rows = warmed(steady())
    feed(ledger, rows[:4])
    feed(ledger, rows[4:5], status="SuspendedEV")
    assert ledger.summary()["last_span"] is None
    feed(ledger, rows[5:6], tx="43")
    assert ledger.summary()["last_span"]["end_reason"] == "identity_or_lifecycle_boundary"
    assert ledger.summary()["current_span"] is None  # boundary sample excluded
    feed(ledger, rows[6:8], tx="43")
    current = ledger.summary()["current_span"]
    assert current["native_transaction_id"] == "43"
    assert Decimal(current["estimate_kwh"]) == rows[7]["register"] - rows[6]["register"]


def test_flat_register_with_export_flow_flagged():
    rows = simulate(steady(600))
    for row in rows[4:]:
        row.update(register=rows[3]["register"], lower=rows[3]["lower"], upper=rows[3]["upper"])
    ledger = ExportShadowLedger(binding=BINDING)
    feed(ledger, rows)
    span = end(ledger, 700)
    assert "export_flow_without_register_advance" in span["anomaly_flags"]
    # A single flat sample at the import -> export transition is not flagged.
    rows = simulate([(0, 5000), (60, 5000), (120, -50), (180, -5000)])
    rows[2]["flow"] = "export"
    ledger = ExportShadowLedger(binding=BINDING)
    feed(ledger, rows)
    assert end(ledger, 240)["anomaly_flags"] == []


def test_register_advance_during_import_flagged():
    rows = simulate([(t, 7000) for t in range(0, 301, 60)])
    for n, row in enumerate(rows):
        row["register"] += Decimal("0.01") * n
        row["lower"] += Decimal("0.01") * n
        row["upper"] += Decimal("0.01") * n
    ledger = ExportShadowLedger(binding=BINDING)
    feed(ledger, rows)
    span = end(ledger, 400)
    assert "register_advance_during_import" in span["anomaly_flags"]
    assert span["flow_direction_counts"]["import"] == 5
    # Export -> import transition: the trapezoid tail is not an anomaly.
    ledger = ExportShadowLedger(binding=BINDING)
    feed(ledger, simulate([(0, -7000), (60, -7000), (120, -7000), (180, 7000), (240, 7000)]))
    assert end(ledger, 300)["anomaly_flags"] == []


def test_flow_update_arriving_after_register_is_paired():
    rows = simulate([(0, 7000), (60, -7000), (120, -7000)])
    ledger = ExportShadowLedger(binding=BINDING)
    feed(ledger, rows[:1])
    late = snap(rows[1])
    late["flow"] = {"value": "import", "ha_updated_at": stamp(0)}
    ledger.observe(late, stamp(60.1))
    late["flow"] = {"value": "export", "ha_updated_at": stamp(60.4)}
    ledger.observe(late, stamp(60.5))
    # A flow change belonging to the NEXT sample is not paired back.
    early = deepcopy(late)
    early["flow"] = {"value": "import", "ha_updated_at": stamp(119)}
    ledger.observe(early, stamp(119.1))
    feed(ledger, rows[2:])
    span = end(ledger, 180)
    assert span["flow_direction_counts"] == {"import": 0, "export": 2, "idle": 0, "unknown": 0}


def test_unit_conversion_and_invalid_register():
    ledger, rows = warmed(steady())
    feed(ledger, rows[:3])
    feed(ledger, rows[3:6], unit="Wh")
    assert Decimal(ledger.summary()["current_span"]["estimate_kwh"]) == rows[5]["register"] - rows[1]["register"]
    feed(ledger, rows[6:7], unit="J")
    assert ledger.summary()["last_span"]["end_reason"] == "invalid_register_or_unit"
    restored = snap(rows[7])
    restored["export"]["restored"] = True
    ledger.observe(restored, stamp(rows[7]["second"] + .1))
    assert ledger.summary()["flags"] == ["invalid_register_or_unit"]


def test_reference_divergence_and_unavailable_reference():
    ledger, rows = warmed()
    reference = [Decimal("1000") + INVERTER_KWH * n / (len(rows) - 2) for n in range(len(rows))]
    ledger = ExportShadowLedger(binding=BINDING, reference={"unique_id": "sigen.discharge"})
    for row, ref in zip(rows, reference):
        ledger.observe(snap(row, reference=ref), stamp(row["second"] + .1))
    span = end(ledger, 781)
    delta = reference[-1] - reference[1]
    assert Decimal(span["reference_delta_kwh"]) == delta
    assert Decimal(span["reference_divergence_kwh"]) == Decimal(span["estimate_kwh"]) - delta
    assert Decimal(span["reference_divergence_ratio"]) > 0
    assert span["reference_status"] == "ok" and span["grade"] == "unreliable"
    ledger = ExportShadowLedger(binding=BINDING, reference={"unique_id": "sigen.discharge"})
    feed(ledger, rows[:5], reference=1000)
    feed(ledger, rows[5:6])
    feed(ledger, rows[6:], reference=1001)
    span = end(ledger, 781)
    assert span["reference_status"] == "unavailable" and span["reference_delta_kwh"] is None
    plain = ExportShadowLedger(binding=BINDING)
    feed(plain, rows)
    assert end(plain, 781)["reference_status"] == "not_bound"


@pytest.mark.parametrize("values", [
    {"min_energy_kwh": "0"}, {"tolerance_pct": "15"}, {"wide_pct": "101"},
    {"tolerance_pct": "-1"}, {"min_energy_kwh": "NaN"}, {"wide_pct": True}, {"other": "1"}])
def test_grading_validation(values):
    with pytest.raises(ValueError):
        grading_rules(values)


def test_unbound_ledger_and_binding_change_never_observe():
    ledger = ExportShadowLedger()
    feed(ledger, simulate(steady(300)))
    assert ledger.summary()["state"] == "not_bound"
    assert ledger.data["current"] is None and ledger.data["spans"] == []
    bound, rows = warmed(steady())
    feed(bound, rows[:4])
    bound.observe({**snap(rows[4]), "binding_valid": False}, stamp(rows[4]["second"] + .1))
    assert bound.summary()["last_span"]["end_reason"] == "export_binding_changed"
    rebound = ExportShadowLedger(deepcopy(bound.data), {"register": {"unique_id": "other"}})
    feed(rebound, rows[5:6])
    assert any(e["kind"] == "binding_changed" for e in rebound.data["events"])
    assert rebound.data["binding"] == {"register": {"unique_id": "other"}}
    assert len(rebound.data["spans"]) == 1  # retained history


def import_v2_data():
    ledger = ImportShadowLedger({"connector": "synthetic"})
    for n, second in enumerate((0, 60, 120)):
        ledger.observe({"import": {"value": str(100 + n / 10), "unit": "kWh",
                                   "ha_updated_at": stamp(second), "context": "Sample.Periodic"},
                        "transaction": {"value": "42"}, "status": {"value": "Charging"}},
                       stamp(second + .1))
    data = deepcopy(ledger.data)
    data["schema"] = 2
    del data["export"]
    return data


def test_store_v2_to_v3_lossless_and_future_or_malformed_refused():
    old = import_v2_data()
    migrated = migrate(deepcopy(old))
    assert migrated["schema"] == 3 and migrated["migrated_from_schema"] == 2
    assert migrated["export"] is None
    assert {k: migrated[k] for k in old if k != "schema"} == {k: v for k, v in old.items() if k != "schema"}
    restored = ImportShadowLedger({"connector": "synthetic"}, deepcopy(old))
    assert restored.data == migrated
    with pytest.raises(ValueError):
        ImportShadowLedger({"connector": "synthetic"}, {**old, "schema": 4})
    ledger, rows = warmed()
    feed(ledger, rows)
    end(ledger, 781)
    feed(ledger, simulate([(900, -7000), (960, -7000)]))
    section = deepcopy(ledger.data)
    assert validate_section(section) == ledger.data
    assert ImportShadowLedger({"connector": "synthetic"}, {**migrated, "export": section}).data[
        "export"] == section
    for change in ({"billing_eligible": True}, {"settlement_owner": "ocpp"},
                   {"grade": "excellent"}, {"estimate_kwh": "NaN"},
                   {"flow_direction_counts": {"export": 1}}):
        broken = deepcopy(section)
        broken["spans"][0].update(change)
        with pytest.raises((ValueError, KeyError, TypeError)):
            ImportShadowLedger({"connector": "synthetic"}, {**migrated, "export": broken})
        with pytest.raises((ValueError, KeyError, TypeError)):
            ExportShadowLedger(broken, BINDING)
    with pytest.raises(ValueError):
        ExportShadowLedger({**section, "unexpected": 1}, BINDING)
    with pytest.raises(ValueError):
        ImportShadowLedger({"connector": "synthetic"}, {**migrated, "export": []})


# --- Home Assistant runtime -------------------------------------------------

def config_entry(domain="ocpp", data=None, options=None):
    return ConfigEntry(version=1, minor_version=1, domain=domain, title="Synthetic shadow",
                       data=data or {}, source="user", unique_id=None, options=options or {},
                       discovery_keys=MappingProxyType({}), subentries_data=[])


async def setup_charger(hass, cpid="charger"):
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    if hasattr(dr, "async_setup"):
        dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    native = config_entry()
    hass.config_entries._entries[native.entry_id] = native
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=native.entry_id, identifiers={("ocpp", cpid)})
    registry = er.async_get(hass)
    sources, exported = {}, {}
    for key, metric in METRICS.items():
        row = registry.async_get_or_create(
            "sensor", "ocpp", f"ocpp.{cpid}.{metric}.sensor", config_entry=native,
            device_id=device.id, suggested_object_id=f"{cpid}_{metric}")
        sources[key] = row.entity_id
        hass.states.async_set(row.entity_id, {
            "import": "100", "status": "Charging", "transaction": "42"}[key], {
            "unit_of_measurement": "kWh", "context": "Sample.Periodic",
            "context_source": "defaulted"})
    for key, slug in EXPORT.items():
        row = registry.async_get_or_create(
            "sensor", "ocpp", f"ocpp.{cpid}.{slug}.sensor", config_entry=native,
            device_id=device.id, suggested_object_id=f"{cpid}_{slug}")
        exported[key] = row.entity_id
    sigen = registry.async_get_or_create(
        "sensor", "sigen", "garage_sigen_dc_total_discharging", suggested_object_id=(
            "garage_sigen_inverter_dc_charger_total_discharging_capacity"))
    hass.states.async_set(sigen.entity_id, "1.000000", {"unit_of_measurement": "MWh"})
    return native, sources, exported, sigen.entity_id


def shadow_data(hass, sources):
    return {**{k + "_entity": v for k, v in sources.items()},
            "backend": "ocpp_import_shadow", "source_binding": source_binding(hass, sources)}


def export_input(exported, reference=None, **grading):
    result = {EXPORT_FIELDS[k]: v for k, v in exported.items()}
    if reference:
        result[REFERENCE_FIELD] = reference
    result.update({GRADING_FIELDS[k]: v for k, v in grading.items()})
    return result


@pytest.mark.asyncio
async def test_export_binding_prefill_and_options_round_trip(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        native, sources, exported, sigen = await setup_charger(hass)
        binding = source_binding(hass, sources)
        assert suggest_export(hass, binding) == exported
        bound = export_binding(hass, binding, exported)
        assert bound["register"]["unique_id"] == "ocpp.charger.energy_active_export_register.sensor"
        assert export_binding(hass, binding, {"register": None, "flow": None}) == {}
        entry = config_entry("bsv_settlement", shadow_data(hass, sources),
                             {"metadata_binding": {}})
        hass.config_entries._entries[entry.entry_id] = entry
        options = BSVSettlementConfigFlow.async_get_options_flow(entry)
        options.hass, options.handler = hass, entry.entry_id
        form = await options.async_step_init({})
        assert form["step_id"] == "export"
        suggested = {str(k): k.description["suggested_value"] for k in form["data_schema"].schema
                     if k.description}
        assert suggested == {**{EXPORT_FIELDS[k]: v for k, v in exported.items()},
                             REFERENCE_FIELD: None}  # reference is never guessed
        defaults = {str(k): k.default() for k in form["data_schema"].schema
                    if str(k) in GRADING_FIELDS.values()}
        assert defaults == {"export_min_energy_kwh": 0.1, "export_tolerance_pct": 5.0,
                            "export_wide_pct": 15.0}
        saved = await options.async_step_export(export_input(
            exported, sigen, min_energy_kwh=0.2, tolerance_pct=4, wide_pct=12))
        assert saved["type"] == "create_entry"
        data = saved["data"]
        assert data["export_binding"] == bound
        assert data["export_reference_binding"] == {
            "entity_id": sigen, "unique_id": "garage_sigen_dc_total_discharging",
            "platform": "sigen"}
        assert data["export_grading"] == {"min_energy_kwh": "0.2", "tolerance_pct": "4",
                                          "wide_pct": "12"}
        assert data["metadata_binding"] == {}
        assert entry.data["source_binding"] == binding  # measurands untouched
        hass.config_entries.async_update_entry(entry, options=data)
        await options.async_step_init({})
        reopened = await options.async_step_export()
        assert {str(k): k.description["suggested_value"] for k in reopened["data_schema"].schema
                if k.description}[REFERENCE_FIELD] == sigen
        cleared = await options.async_step_export({})
        assert cleared["data"]["export_binding"] == {}
        assert cleared["data"]["export_reference_binding"] is None
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change,error", [
    ("other_charger", "invalid_ocpp_export_sources"),
    ("other_entry", "invalid_ocpp_export_sources"),
    ("register_only", "invalid_ocpp_export_sources"),
    ("swapped", "invalid_ocpp_export_sources"),
    ("disabled", "invalid_ocpp_export_sources"),
    ("measurand", "invalid_ocpp_export_sources"),
    ("reference_ocpp", "invalid_ocpp_export_reference"),
    ("reference_unitless", "invalid_ocpp_export_reference"),
    ("reference_without_export", "invalid_ocpp_export_reference"),
    ("grading", "invalid_ocpp_export_grading")])
async def test_cross_charger_or_invalid_export_options_rejected(tmp_path, change, error):
    hass = HomeAssistant(str(tmp_path))
    try:
        native, sources, exported, sigen = await setup_charger(hass)
        registry = er.async_get(hass)
        selected, reference, grading = dict(exported), sigen, {}
        if change == "other_charger":
            registry.async_update_entity(
                exported["register"],
                new_unique_id="ocpp.charger2.energy_active_export_register.sensor")
        elif change == "other_entry":
            other = config_entry()
            hass.config_entries._entries[other.entry_id] = other
            registry.async_update_entity(exported["flow"], config_entry_id=other.entry_id)
        elif change == "register_only":
            selected = {"register": exported["register"]}
            reference = None
        elif change == "swapped":
            selected = {**exported, "register": exported["flow"], "flow": exported["register"]}
        elif change == "disabled":
            registry.async_update_entity(
                exported["register"], disabled_by=er.RegistryEntryDisabler.USER)
        elif change == "measurand":
            selected = {**exported, "register": sources["import"]}
        elif change == "reference_ocpp":
            reference = exported["session"]
        elif change == "reference_unitless":
            hass.states.async_set(sigen, "1", {"unit_of_measurement": "%"})
        elif change == "reference_without_export":
            selected = {}
        else:
            grading = {"tolerance_pct": 20, "wide_pct": 15}
        entry = config_entry("bsv_settlement", shadow_data(hass, sources))
        hass.config_entries._entries[entry.entry_id] = entry
        flow = OCPPShadowOptionsFlow()
        flow.hass, flow.handler = hass, entry.entry_id
        await flow.async_step_init({})
        result = await flow.async_step_export(export_input(selected, reference, **grading))
        assert result["type"] == "form" and result["step_id"] == "export"
        assert result["errors"]["base"] == error
        with pytest.raises(ValueError, match=error):
            shadow_export_options(hass, entry.data["source_binding"],
                                  export_input(selected, reference, **grading))
    finally:
        await hass.async_stop(force=True)


def test_reference_kwh_unit_conversion():
    class State:
        def __init__(self, value, unit):
            self.state, self.attributes = value, {"unit_of_measurement": unit}
    assert Decimal(reference_kwh(State("1.25", "MWh"))) == Decimal("1250")
    assert Decimal(reference_kwh(State("1250", "Wh"))) == Decimal("1.25")
    assert reference_kwh(State("unknown", "MWh")) is None
    assert reference_kwh(State("1", "%")) is None
    assert reference_kwh(None) is None


def register_attrs(row, second):
    return {"unit_of_measurement": "kWh", "state_class": "total_increasing",
            "source": DERIVED, "method": "trapezoidal_integration", "estimated": True,
            "energy_lower_bound_kwh": float(row["lower"]),
            "energy_upper_bound_kwh": float(row["upper"]), "step_intervals": row["steps"],
            "last_interval_s": 60, "max_sample_gap_s": 120,
            "last_sample_timestamp": stamp(second), "context_source": "defaulted"}


async def coordinator_with_export(hass, sources, exported, sigen, store_entry=None):
    binding = source_binding(hass, sources)
    options = {"metadata_binding": {}, **shadow_export_options(
        hass, binding, export_input(exported, sigen))}
    entry = store_entry or config_entry("bsv_settlement", shadow_data(hass, sources), options)
    coord = OCPPShadowCoordinator(hass, entry)
    await coord.load()
    return entry, coord


@pytest.mark.asyncio
async def test_coordinator_replay_reference_mwh_sensors_and_no_financial_paths(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    coord = None
    try:
        native, sources, exported, sigen = await setup_charger(hass)
        entry, coord = await coordinator_with_export(hass, sources, exported, sigen)
        rows = simulate(V2G)
        reference = [Decimal("1.000000") + INVERTER_KWH / 1000 * n / (len(rows) - 1)
                     for n in range(len(rows))]
        for n, (row, ref) in enumerate(zip(rows, reference)):
            hass.states.async_set(sigen, str(ref), {"unit_of_measurement": "MWh"})
            hass.states.async_set(exported["flow"], row["flow"],
                                  {"source": "derived_from_signed_import", "deadband_kw": 0.1})
            hass.states.async_set(exported["register"], str(row["register"]),
                                  register_attrs(row, row["second"]))
            await hass.async_block_till_done()
        hass.states.async_set(sources["status"], "Finishing")
        await hass.async_block_till_done()
        summary = coord.summary()
        export = summary["export_shadow"]
        span = export["last_span"]
        assert span["grade"] == "unreliable"
        assert Decimal(span["estimate_kwh"]).quantize(Decimal("0.001")) == Decimal("1.307")
        assert Decimal(span["reference_delta_kwh"]) == INVERTER_KWH
        ratio = Decimal(span["reference_divergence_ratio"])
        assert Decimal("0.04") < ratio < Decimal("0.05")
        assert span["flow_direction_counts"]["export"] == 11
        assert {"context_defaulted_by_integration", "metadata_not_bound"} & set(
            span["provenance_flags"]) == {"context_defaulted_by_integration"}
        # The import shadow and every financial flag are unchanged by export evidence.
        assert summary["export_kwh"] is summary["net_cost_aud"] is None
        assert not any(summary[k] for k in FINANCIAL)
        assert not any(export[k] for k in FINANCIAL)
        last = OCPPExportShadowSensor(coord, entry, "export_last_span", "Last", "kWh")
        quality = OCPPExportShadowSensor(coord, entry, "export_quality", "Quality", None)
        coord.async_set_updated_data(summary)
        assert last.native_value == Decimal(span["estimate_kwh"])
        assert last.extra_state_attributes["grade"] == "unreliable"
        assert last.extra_state_attributes["billing_eligible"] is False
        assert last.extra_state_attributes["settlement_owner"] is None
        assert last.extra_state_attributes["reference_divergence_kwh"] == span[
            "reference_divergence_kwh"]
        assert last.entity_category == "diagnostic"
        assert quality.native_value == "unreliable"
        assert quality.extra_state_attributes["grade_counts"]["unreliable"] == 1
        with pytest.raises(HomeAssistantError, match="read-only"):
            await coord.execute("prepare_operator_payment", {})
        # Export binding drift degrades export only; import keeps observing.
        er.async_get(hass).async_update_entity(
            exported["flow"], new_unique_id="ocpp.other.flow_direction.sensor")
        coord.observe()
        drifted = coord.summary()
        assert drifted["state"] != "incompatible"
        assert "export_binding_changed" in drifted["export_shadow"]["flags"]
        await coord.close()
        saved = await ShadowStore(hass, entry.entry_id, entry.data["source_binding"]).async_load()
        assert saved["schema"] == 3 and saved["export"]["spans"][0]["grade"] == "unreliable"
        assert json.dumps(saved)  # serialisable
    finally:
        if coord:
            await coord.close()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_unbound_entry_has_not_bound_sensors(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    coord = None
    try:
        native, sources, exported, sigen = await setup_charger(hass)
        entry = config_entry("bsv_settlement", shadow_data(hass, sources))
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        coord.async_set_updated_data(coord.summary())
        assert coord.export is None and coord.ledger.data["export"] is None
        quality = OCPPExportShadowSensor(coord, entry, "export_quality", "Quality", None)
        last = OCPPExportShadowSensor(coord, entry, "export_last_span", "Last", "kWh")
        assert quality.native_value == "not_bound" and last.native_value is None
        assert quality.extra_state_attributes["billing_eligible"] is False
    finally:
        if coord:
            await coord.close()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_v2_store_file_migrated_and_future_or_malformed_not_rewritten(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        native, sources, exported, sigen = await setup_charger(hass)
        data = shadow_data(hass, sources)
        old = import_v2_data()
        old["binding"] = data["source_binding"]
        storage = tmp_path / ".storage"
        storage.mkdir(exist_ok=True)
        entry = config_entry("bsv_settlement", data)
        path = storage / f"bsv_settlement.ocpp_shadow.{entry.entry_id}"
        path.write_text(json.dumps({"version": 2, "minor_version": 1,
                                    "key": path.name, "data": old}))
        binding = source_binding(hass, sources)
        options = {"metadata_binding": {}, **shadow_export_options(
            hass, binding, export_input(exported))}
        entry = config_entry("bsv_settlement", data, options)
        entry_path = storage / f"bsv_settlement.ocpp_shadow.{entry.entry_id}"
        entry_path.write_text(path.read_text())
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        assert coord.ledger.data["events"][:len(old["events"])] == old["events"]
        assert coord.ledger.data["spans"][:len(old["spans"])] == old["spans"]
        assert coord.ledger.data["migrated_from_schema"] == 2
        assert coord.export.data is coord.ledger.data["export"]
        await coord.close()
        on_disk = json.loads(entry_path.read_text())
        assert on_disk["version"] == 3 and on_disk["data"]["schema"] == 3
        assert on_disk["data"]["export"]["binding"] == options["export_binding"]
        good = on_disk["data"]

        broken = deepcopy(good)
        broken["export"]["events"] = [None]
        for bad in ({"version": 4, "minor_version": 1, "key": "x", "data": good},
                    {"version": 3, "minor_version": 1, "key": "x", "data": broken},
                    {"version": 2, "minor_version": 1, "key": "x", "data": good}):
            other = config_entry("bsv_settlement", data, options)
            bad_path = storage / f"bsv_settlement.ocpp_shadow.{other.entry_id}"
            bad_path.write_text(json.dumps({**bad, "key": bad_path.name}))
            before = bad_path.read_text()
            with pytest.raises((ValueError, HomeAssistantError)):
                await OCPPShadowCoordinator(hass, other).load()
            assert bad_path.read_text() == before
    finally:
        await hass.async_stop(force=True)


def test_export_shadow_never_reaches_reference_binding_from_ocpp():
    # Pure guard: a reference with no export binding is refused before any lookup.
    with pytest.raises(ValueError):
        reference_binding(None, {}, {}, "sensor.anything")
    assert reference_binding(None, {}, {}, None) is None
