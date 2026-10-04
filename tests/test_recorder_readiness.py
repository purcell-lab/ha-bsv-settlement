"""Synthetic recorder readiness and legacy reconciliation; no live HA, charger or funds."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant, State
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.config_flow import (
    OCPPShadowOptionsFlow, RECONCILE_FIELDS, shadow_export_options, shadow_reconcile_options)
from custom_components.bsv_settlement.const import DOMAIN, SERVICES
from custom_components.bsv_settlement.ocpp_shadow import (
    EXPORT, EXPORT_FIELDS, METRICS, OCPPShadowCoordinator, source_binding)
from custom_components.bsv_settlement.proxy import ProxyCoordinator, SOURCE_KEYS, normalize
from custom_components.bsv_settlement.recorder import (
    LEVELS, OPERATOR_VALIDATION_AVAILABLE, Evidence, LegacySigenRecorder, OCPPShadowRecorder,
    RecorderStatus, Validation, assess, freshness_limit, resolve_link)
from custom_components.bsv_settlement.recorder_reconciliation import (
    DEFAULT_TOLERANCES, MAX_RESULTS, ReconciliationLedger, legacy_window, migrate,
    reconcile_span, tolerance_rules, validate)
from custom_components.bsv_settlement.sensor import RecorderReadinessSensor

START = datetime(2026, 10, 4, 0, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "bsv_settlement"
D = Decimal


def at(second, base=START):
    return base + timedelta(seconds=second)


def sigen(end, kw=D("7"), start=D("1234.56"), every=5, begin=-120, flow=(0, 1800), base=START):
    """Sigen-like counter: ~5 s cadence, 10 Wh steps, rows only on change, final report."""
    rows, last = [], None
    for second in range(begin, end + 1, every):
        active = min(max(second, flow[0]), flow[1]) - flow[0]
        value = (start + kw * D(active) / 3600).quantize(D("0.01"), ROUND_HALF_UP)
        if value != last:
            rows.append((at(second, base), value))
            last = value
    rows.append((at(end, base), last))
    return rows


def import_span(first=12.3, last=1792.3, kwh=None, samples=30, base=START,
                reason="identity_or_lifecycle_boundary", span_id="ocpp-shadow-a"):
    true = D("7") * D(str(last - first)) / 3600
    return {"span_id": span_id, "native_transaction_id": "42",
            "first_meter_ha_updated_at": at(first, base).isoformat(),
            "last_meter_ha_updated_at": at(last, base).isoformat(),
            "observed_import_kwh": str(true + D("0.02")) if kwh is None else kwh,
            "sample_count": samples, "end_reason": reason,
            "observation_ended_at": at(last + 5, base).isoformat()}


def export_span(grade="unreliable", estimate="1.307", lower="0.94", upper="1.67"):
    return {"span_id": "ocpp-export-shadow-a", "native_transaction_id": "42",
            "first_meter_ha_updated_at": at(0).isoformat(),
            "last_meter_ha_updated_at": at(720).isoformat(), "estimate_kwh": estimate,
            "lower_kwh": lower, "upper_kwh": upper, "grade": grade, "sample_count": 13,
            "end_reason": "identity_or_lifecycle_boundary",
            "observation_ended_at": at(721).isoformat()}


def compare(span, readings, direction="import", **tolerances):
    return reconcile_span(span, direction, readings, tolerances or None, "proxy-entry",
                          at(4000).isoformat())


FRESH = Evidence("sensor.x", True, True, True, START, True)


# --- Readiness ladder --------------------------------------------------------

def test_ladder_never_validated_merely_because_entities_exist():
    now = at(10)
    rungs = [
        (Evidence("sensor.x"), "unavailable"),
        (Evidence("sensor.x", True), "entity_enabled"),
        (Evidence("sensor.x", True, True), "entity_enabled"),
        (Evidence("sensor.x", True, True, True, START), "numeric_reading"),
        (FRESH, "fresh_attributable_sample"),
    ]
    for evidence, level in rungs:
        capability = assess("import", evidence, now, 5)
        assert capability.level == level
        assert "directional_accounting_not_validated" in capability.limitations
    # A within-tolerance reconciliation alone is not enough; operator flag is required.
    assert assess("import", FRESH, now, 5, Validation(True, False)).level == (
        "fresh_attributable_sample")
    assert assess("import", FRESH, now, 5, Validation(False, True)).level == (
        "fresh_attributable_sample")
    assert OPERATOR_VALIDATION_AVAILABLE is False
    # The gate exists for future work, and only lifts fresh attributable evidence.
    assert assess("import", FRESH, now, 5, Validation(True, True)).level == LEVELS[-1]
    stale = Evidence("sensor.x", True, True, True, at(-3600), True)
    assert assess("import", stale, now, 5, Validation(True, True)).level == "numeric_reading"


def test_stale_and_future_samples_downgrade():
    assert freshness_limit(5) == 35 and freshness_limit(60) == 180
    fresh = assess("import", FRESH, at(35), 5)
    assert fresh.level == "fresh_attributable_sample" and fresh.sample_age_s == 35
    stale = assess("import", FRESH, at(36), 5)
    assert stale.level == "numeric_reading" and "stale_sample" in stale.limitations
    assert assess("import", FRESH, at(180), 60).level == "fresh_attributable_sample"
    assert assess("import", FRESH, at(181), 60).level == "numeric_reading"
    assert "stale_sample" in assess("import", FRESH, at(-10), 60).limitations


def test_estimated_export_capped_with_limitation():
    estimated = Evidence("sensor.export", True, True, True, START, True, True)
    capability = assess("export", estimated, at(5), 60, Validation(True, True))
    assert capability.level == "fresh_attributable_sample"
    assert "export_estimated" in capability.limitations
    status = RecorderStatus("ocpp", "x", "shadow", "shadow_only", (
        assess("import", FRESH, at(5), 60), capability))
    assert status.overall_level == "fresh_attributable_sample"
    assert status.as_dict()["billing_eligible"] is False
    unbound = RecorderStatus("ocpp", "x", "shadow", "shadow_only", (
        assess("import", FRESH, at(5), 60), assess("export", Evidence(None), at(5), 60)))
    assert unbound.overall_level == "unavailable"


# --- Aligned-window reconciliation ------------------------------------------

def test_aligned_import_window_within_tolerance():
    readings = sigen(1900)
    result = compare(import_span(), readings)
    assert result["explanation"] == "aligned" and result["outcome"] == "within_tolerance"
    assert result["within_tolerance"] is True
    legacy = D(result["legacy_kwh"])
    true = D("7") * 1780 / 3600
    assert abs(legacy - true) <= D("0.01")  # 10 Wh resolution bounds the estimate
    assert D(result["legacy_lower_kwh"]) <= true <= D(result["legacy_upper_kwh"])
    assert abs(D(result["difference_kwh"]) - (D(result["ocpp_kwh"]) - legacy)) < D("1e-6")
    assert D(result["allowance_kwh"]) == (D("0.01") * legacy).quantize(D("0.000001"))
    assert result["tolerance"] == DEFAULT_TOLERANCES
    assert result["window_s"] == "1780"
    assert result["billing_eligible"] is False and result["settlement_owner"] == "legacy_sigen"
    assert [D(g) <= 5 for g in result["legacy_edge_gaps_s"]] == [True, True]
    outside = compare(import_span(kwh=str(true + D("0.2"))), readings)
    assert outside["explanation"] == "difference_exceeds_tolerance"
    assert outside["outcome"] == "outside_tolerance" and not outside["within_tolerance"]
    assert D(outside["difference_pct"]) > 1


def test_misaligned_partial_and_coarse_windows_unresolved():
    readings = sigen(1900)
    gap = [r for r in readings if not at(1700) < r[0] < at(1900)]
    assert compare(import_span(), gap)["explanation"] == "misaligned_window"
    # A long bracket where the counter did not move is exact, not misaligned.
    idle = sigen(1900, flow=(0, 1500))
    span = import_span(first=12.3, last=1600, kwh=str(D("7") * D("1487.7") / 3600 + D("0.01")))
    assert compare(span, idle)["explanation"] == "aligned"
    for partial in (import_span(reason="meter_gap"), import_span(reason="restart_gap"),
                    import_span(samples=1), import_span(first=10, last=10)):
        result = compare(partial, readings)
        assert result["explanation"] == "ocpp_span_partial"
        assert result["outcome"] == "unresolved" and not result["within_tolerance"]
    coarse = sigen(1900, every=50, begin=10)
    assert compare(import_span(), coarse)["explanation"] == "legacy_resolution_limited"
    tiny = compare(import_span(first=12.3, last=30.3), readings)
    assert tiny["explanation"] == "insufficient_energy" and tiny["outcome"] == "unresolved"


def test_legacy_missing_or_invalid_is_unresolved_never_ok():
    readings = sigen(1900)
    for missing in (None, [], [r for r in readings if r[0] > at(100)],
                    [r for r in readings if r[0] < at(1700)]):
        result = compare(import_span(), missing)
        assert result["explanation"] == "legacy_missing"
        assert result["outcome"] == "unresolved" and result["within_tolerance"] is False
        assert result["legacy_kwh"] is None and result["difference_kwh"] is None
    decreased = sorted([*readings, (at(900.5), D("1200"))])
    assert compare(import_span(), decreased)["explanation"] == "legacy_counter_invalid"
    invalid = sorted([*readings, (at(900.5), None)], key=lambda r: r[0])
    assert compare(import_span(), invalid)["explanation"] == "legacy_counter_invalid"
    assert compare({**import_span(), "observed_import_kwh": "NaN"}, readings)[
        "explanation"] == "ocpp_missing"


def test_export_bounds_containing_legacy():
    readings = sigen(800, kw=D("6.25"), flow=(0, 720))  # legacy 1.250 kWh
    aligned = compare(export_span(), readings, "export")  # +4.6 % within default 5 %
    assert aligned["explanation"] == "aligned" and aligned["ocpp_grade"] == "unreliable"
    strict = {**DEFAULT_TOLERANCES, "export_pct": "2"}
    loose = compare(export_span(), readings, "export", **strict)
    assert loose["explanation"] == "ocpp_bounds_contain_legacy"
    assert loose["outcome"] == "unresolved"  # wide bounds are consistent, not validating
    assert (loose["ocpp_lower_kwh"], loose["ocpp_upper_kwh"]) == ("0.94", "1.67")
    tight = compare(export_span("within_tolerance", "1.30", "1.24", "1.31"), readings,
                    "export", **strict)
    assert tight["explanation"] == "ocpp_bounds_contain_legacy"
    assert tight["outcome"] == "within_tolerance"
    wrong = compare(export_span("within_tolerance", "1.50", "1.48", "1.52"), readings,
                    "export", **strict)
    assert wrong["explanation"] == "difference_exceeds_tolerance"
    no_bounds = compare(export_span("ungraded", "1.50", None, None), readings, "export")
    assert no_bounds["explanation"] == "difference_exceeds_tolerance"


@pytest.mark.parametrize("values", [
    {"import_pct": "0"}, {"export_pct": "101"}, {"import_floor_kwh": "-1"},
    {"min_energy_kwh": "0"}, {"bogus": "1"}, {"import_pct": "NaN"}])
def test_tolerance_validation(values):
    with pytest.raises(ValueError):
        tolerance_rules(values)


def test_legacy_window_bounds_bracket_true_energy():
    readings = sigen(1900)
    # Includes windows opening in the idle stretch before flow (change-only rows).
    for first, last in ((0.1, 1799.9), (2.5, 1000), (12.3, 1792.3), (-30, 1000), (-90, 1850)):
        result = legacy_window(readings, at(first), at(last))
        true = D("7") * D(str(min(last, 1800) - max(first, 0))) / 3600
        assert result["status"] == "ok" and not result["misaligned"]
        assert result["lower"] <= true <= result["upper"]
        assert abs(result["estimate"] - true) <= D("0.01")


def test_ledger_watermark_retention_and_refusal():
    ledger = ReconciliationLedger(started_at=at(0).isoformat())
    spans = [import_span(first=n * 2000, last=n * 2000 + 1780, span_id=f"s{n}")
             for n in range(MAX_RESULTS + 5)]
    old = {**import_span(first=-5000, last=-3000), "span_id": "before-link"}
    assert [s["span_id"] for s in ledger.candidates("import", [old, *spans[:2]])] == ["s0", "s1"]
    for span in spans:
        ledger.record("import", span, compare(span, None))
    assert len(ledger.data["results"]) == MAX_RESULTS and ledger.data["results_trimmed"]
    assert ledger.candidates("import", spans) == []
    assert ledger.data["totals"]["unresolved"] == MAX_RESULTS + 5
    ledger.skip("export", {**export_span(), "observation_ended_at": at(10).isoformat()})
    assert ledger.data["totals"]["skipped_not_linked"] == 1
    summary = ledger.summary()
    assert summary["unresolved_count"] == MAX_RESULTS and summary["aligned_count"] == 0
    assert summary["last_import"]["span_id"] == spans[-1]["span_id"]
    data = json.loads(json.dumps(ledger.data))
    assert ReconciliationLedger(data).data == data and validate(deepcopy(data)) == data
    for change in ({"billing_eligible": True}, {"settlement_owner": "ocpp"},
                   {"outcome": "within_tolerance"}, {"explanation": "ok"},
                   {"ocpp_kwh": "NaN"}, {"window_start": "yesterday"}):
        broken = deepcopy(data)
        broken["results"][0].update(change)
        with pytest.raises((ValueError, KeyError, TypeError)):
            ReconciliationLedger(broken)
    for broken in ({**data, "schema": 2}, {**data, "extra": 1}, [], {**data, "results": [1]},
                   {**data, "results": data["results"] + data["results"]}):
        with pytest.raises((ValueError, KeyError, TypeError)):
            ReconciliationLedger(broken)
    with pytest.raises(ValueError):
        migrate({"schema": 0})


# --- Home Assistant runtime -------------------------------------------------

def config_entry(domain="ocpp", data=None, options=None, title="Synthetic"):
    return ConfigEntry(version=1, minor_version=1, domain=domain, title=title,
                       data=data or {}, source="user", unique_id=None, options=options or {},
                       discovery_keys=MappingProxyType({}), subentries_data=[])


async def setup_site(hass, proxies=1):
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    if hasattr(dr, "async_setup"):
        dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    hass.data.setdefault(DOMAIN, {})
    native = config_entry()
    hass.config_entries._entries[native.entry_id] = native
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=native.entry_id, identifiers={("ocpp", "charger")})
    registry = er.async_get(hass)
    sources, exported = {}, {}
    for key, metric in METRICS.items():
        row = registry.async_get_or_create(
            "sensor", "ocpp", f"ocpp.charger.{metric}.sensor", config_entry=native,
            device_id=device.id, suggested_object_id=f"charger_{metric}")
        sources[key] = row.entity_id
        hass.states.async_set(row.entity_id, {
            "import": "100", "status": "Charging", "transaction": "42"}[key], {
            "unit_of_measurement": "kWh", "context": "Sample.Periodic"})
    for key, slug in EXPORT.items():
        row = registry.async_get_or_create(
            "sensor", "ocpp", f"ocpp.charger.{slug}.sensor", config_entry=native,
            device_id=device.id, suggested_object_id=f"charger_{slug}")
        exported[key] = row.entity_id
        hass.states.async_set(row.entity_id, "10" if key == "register" else "export", {
            "unit_of_measurement": "kWh", "source": "derived_from_negative_import"})
    entries = []
    for n in range(proxies):
        data = {"backend": "sensor_proxy",
                **{k + "_entity": f"sensor.sigen{n}_{k}" for k in SOURCE_KEYS}}
        entry = config_entry(DOMAIN, data, title=f"Sigen charging sessions {n}")
        hass.config_entries._entries[entry.entry_id] = entry
        entries.append(entry)
    return sources, exported, entries


def shadow_entry(hass, sources, options=None):
    data = {**{k + "_entity": v for k, v in sources.items()},
            "backend": "ocpp_import_shadow", "source_binding": source_binding(hass, sources)}
    entry = config_entry(DOMAIN, data, options or {}, "OCPP import shadow")
    hass.config_entries._entries[entry.entry_id] = entry
    return entry


def legacy_proxy(hass, entry, readings):
    """Real ProxyCoordinator with recorded rows (not loaded: no HA recorder needed)."""
    proxy = ProxyCoordinator(hass, entry)
    for stamp, kwh in readings:
        proxy.observations["import"].append(normalize(State(
            proxy.sources["import"], f"{kwh / 1000:.5f}", {"unit_of_measurement": "MWh"},
            last_updated=stamp, last_changed=stamp, last_reported=stamp)))
    hass.states.async_set(proxy.sources["import"], f"{readings[-1][1] / 1000:.5f}",
                          {"unit_of_measurement": "MWh"})
    hass.states.async_set(proxy.sources["export"], "0.50000", {"unit_of_measurement": "MWh"})
    hass.states.async_set(proxy.sources["state"], "Charging")
    hass.data[DOMAIN][entry.entry_id] = proxy
    return proxy


def feed_import_span(coord, base, first=12.3, last=1792.3, every=60):
    """Accepted OCPP import samples (HA times in `base` frame) then a lifecycle end."""
    seconds = [first + n * every for n in range(int((last - first) // every) + 1)]
    if seconds[-1] != last:
        seconds.append(last)
    for second in seconds:
        kwh = D("100") + D("7") * D(str(second - first)) / 3600 + (D("0.02") if second == last
                                                                    else 0)
        coord.ledger.observe({
            "import": {"value": str(kwh), "unit": "kWh", "ha_updated_at": at(second, base).isoformat(),
                       "context": "Sample.Periodic"},
            "transaction": {"value": "42"}, "status": {"value": "Charging"}},
            at(second + 0.1, base).isoformat())
    coord.ledger.observe({"status": {"value": "Available"}, "transaction": {"value": "42"}},
                         at(last + 2, base).isoformat())
    return coord.ledger.data["spans"][-1]


def sensors(coord, entry):
    coord.async_set_updated_data(coord.summary())
    return {key: RecorderReadinessSensor(coord, entry, key, key, None)
            for key in ("session_recorder", "recorder_readiness", "last_reconciliation")}


@pytest.mark.asyncio
async def test_runtime_reconciliation_sensors_and_restart_retention(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    coord = None
    try:
        sources, exported, (proxy_entry,) = await setup_site(hass)
        entry = shadow_entry(hass, sources)  # never configured: auto-detects the one proxy
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        base = dt_util.utcnow() + timedelta(minutes=1)  # after the observer's start barrier
        legacy_proxy(hass, proxy_entry, sigen(1900, base=base))
        span = feed_import_span(coord, base)
        assert span["end_reason"] == "identity_or_lifecycle_boundary"
        # Not settled yet: waits for a legacy reading after the window end.
        assert not coord.recorder.reconcile(coord, at(1800, base).isoformat())
        assert coord.recorder.pending(coord) == 1
        assert coord.recorder.reconcile(coord, at(1830, base).isoformat())
        result = coord.recorder.ledger.last("import")
        assert result["explanation"] == "aligned" and result["legacy_entry_id"] == (
            proxy_entry.entry_id)
        assert result["window_start"] == span["first_meter_ha_updated_at"]
        assert not coord.recorder.reconcile(coord, at(1900, base).isoformat())  # once only
        found = sensors(coord, entry)
        assert found["session_recorder"].native_value == "legacy_sigen"
        attrs = found["session_recorder"].extra_state_attributes
        assert attrs["link_state"] == "auto_detected" and attrs["selector_implemented"] is False
        assert attrs["legacy_entry_id"] == proxy_entry.entry_id
        last = found["last_reconciliation"]
        assert last.native_value == "aligned"
        assert last.extra_state_attributes["within_tolerance"] is True
        assert last.extra_state_attributes["aligned_count"] == 1
        assert last.extra_state_attributes["unresolved_count"] == 0
        assert last.extra_state_attributes["billing_eligible"] is False
        assert last.extra_state_attributes["settlement_owner"] == "legacy_sigen"
        assert last.entity_category == "diagnostic"
        readiness = found["recorder_readiness"]
        # Export entities exist but are not bound: never bidirectional-ready.
        assert readiness.native_value == "unavailable"
        assert readiness.extra_state_attributes["export_level"] == "unavailable"
        assert "export_not_bound" in readiness.extra_state_attributes["export_limitations"]
        legacy = coord.summary()["recorder"]["legacy"]
        assert legacy["role"] == "settlement_source" and legacy["settlement_owner"] is True
        assert coord.summary()["billing_eligible"] is False
        await coord.close()
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        assert coord.recorder.ledger.data["results"] == [result]
        assert sensors(coord, entry)["last_reconciliation"].native_value == "aligned"
        assert not coord.recorder.reconcile(coord, at(4000, base).isoformat())
        # Legacy recorder removed from the site: unresolved after the pending limit.
        hass.data[DOMAIN].pop(proxy_entry.entry_id)
        second = feed_import_span(coord, base + timedelta(hours=1))
        late = at(1830 + 3600, base).isoformat()
        assert not coord.recorder.reconcile(coord, late)
        assert coord.recorder.reconcile(coord, at(1800 + 3600 + 1000, base).isoformat())
        missing = coord.recorder.ledger.last("import")
        assert missing["span_id"] == second["span_id"]
        assert missing["explanation"] == "legacy_missing" and missing["outcome"] == "unresolved"
        assert "legacy_recorder_not_loaded" in missing["legacy_flags"]
    finally:
        if coord:
            await coord.close()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_runtime_readiness_estimated_export_capped_and_stale(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    coord = None
    try:
        sources, exported, (proxy_entry,) = await setup_site(hass)
        options = {"metadata_binding": {}, **shadow_export_options(
            hass, source_binding(hass, sources),
            {EXPORT_FIELDS[k]: v for k, v in exported.items()})}
        entry = shadow_entry(hass, sources, options)
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        recorder = OCPPShadowRecorder(hass, coord)
        before = recorder.status(dt_util.utcnow())
        # Bound, enabled and numeric is not attributable before an accepted sample.
        assert before.capability("import").level == "numeric_reading"
        assert "sample_not_attributable" in before.capability("import").limitations
        for n, value in enumerate(("100.1", "100.3")):
            hass.states.async_set(sources["import"], value, {
                "unit_of_measurement": "kWh", "context": "Sample.Periodic"})
            hass.states.async_set(exported["register"], str(10 + n / 10), {
                "unit_of_measurement": "kWh", "source": "derived_from_negative_import",
                "energy_lower_bound_kwh": 10, "energy_upper_bound_kwh": 10 + n / 5})
            await hass.async_block_till_done()
        # Pretend every direction already reconciled within tolerance.
        valid = {"import": Validation(True, False), "export": Validation(True, False)}
        now = dt_util.utcnow()
        status = recorder.status(now, valid)
        assert status.capability("import").level == "fresh_attributable_sample"
        export = status.capability("export")
        assert export.level == "fresh_attributable_sample"
        assert "export_estimated" in export.limitations
        assert status.overall_level == "fresh_attributable_sample"
        assert status.health == "shadow_only" and status.settlement_owner is False
        assert recorder.status(now, {d: Validation(True, True) for d in valid}).capability(
            "export").level == "fresh_attributable_sample"
        live = coord.summary()["recorder"]["ocpp"]["directions"]
        assert all(d["level"] != LEVELS[-1] for d in live.values())
        stale = recorder.status(now + timedelta(minutes=10), valid)
        assert {c.level for c in stale.capabilities} == {"numeric_reading"}
        assert all("stale_sample" in c.limitations for c in stale.capabilities)
        readiness = sensors(coord, entry)["recorder_readiness"]
        assert readiness.native_value == "fresh_attributable_sample"
        assert readiness.extra_state_attributes["import_sample_age_s"] is not None
        assert readiness.extra_state_attributes["operator_validation_available"] is False
        # Legacy facade: fresh counters and a known running state; stale downgrades.
        proxy = legacy_proxy(hass, proxy_entry, sigen(60))
        legacy = LegacySigenRecorder(hass, proxy)
        assert legacy.status(dt_util.utcnow()).overall_level == "fresh_attributable_sample"
        late = legacy.status(dt_util.utcnow() + timedelta(seconds=40))
        assert late.health == "stale" and late.overall_level == "numeric_reading"
        hass.states.async_set(proxy.sources["state"], "unavailable")
        assert "running_state_unknown" in legacy.status(dt_util.utcnow()).capability(
            "import").limitations
        # A changed OCPP identity is incompatible, not ready.
        er.async_get(hass).async_update_entity(
            sources["import"], new_unique_id="ocpp.other.energy_active_import_register.sensor")
        coord.observe()
        assert recorder.status(dt_util.utcnow()).health == "incompatible"
        assert recorder.status(dt_util.utcnow()).capability("import").level == "unavailable"
    finally:
        if coord:
            await coord.close()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("version,data", [
    (2, "valid"), (1, "billing"), (1, "schema"), (0, "valid")])
async def test_reconciliation_store_refused_untouched_and_observation_continues(
        tmp_path, version, data):
    hass = HomeAssistant(str(tmp_path))
    coord = None
    try:
        sources, exported, _ = await setup_site(hass)
        entry = shadow_entry(hass, sources)
        good = ReconciliationLedger(started_at=at(0).isoformat())
        good.record("import", import_span(), compare(import_span(), None))
        stored = deepcopy(good.data)
        if data == "billing":
            stored["results"][0]["billing_eligible"] = True
        elif data == "schema":
            stored["schema"] = 7
        storage = tmp_path / ".storage"
        storage.mkdir(exist_ok=True)
        path = storage / f"bsv_settlement.recorder_reconciliation.{entry.entry_id}"
        path.write_text(json.dumps({"version": version, "minor_version": 1,
                                    "key": path.name, "data": stored}))
        before = path.read_text()
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        assert coord.recorder.state == "store_refused" and coord.recorder.ledger is None
        assert not coord.recorder.reconcile(coord, at(10).isoformat())
        last = sensors(coord, entry)["last_reconciliation"]
        assert last.native_value == "store_refused"
        assert coord.summary()["state"] in ("waiting", "observing")
        await coord.close()
        coord = None
        assert path.read_text() == before
    finally:
        if coord:
            await coord.close()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_valid_store_round_trip(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        sources, exported, _ = await setup_site(hass, proxies=0)
        entry = shadow_entry(hass, sources)
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        assert coord.recorder.state == "ready"
        # Not linked (no legacy recorder): closed spans are skipped, never "ok".
        span = feed_import_span(coord, dt_util.utcnow() + timedelta(minutes=1))
        assert coord.recorder.reconcile(coord, at(10 ** 6).isoformat())
        assert coord.recorder.ledger.data["results"] == []
        assert coord.recorder.ledger.data["totals"]["skipped_not_linked"] == 1
        assert coord.recorder.ledger.data["watermarks"]["import"]["span_id"] == span["span_id"]
        assert sensors(coord, entry)["session_recorder"].native_value == "not_linked"
        await coord.close()
        path = tmp_path / ".storage" / f"bsv_settlement.recorder_reconciliation.{entry.entry_id}"
        on_disk = json.loads(path.read_text())
        assert on_disk["version"] == 1 and on_disk["data"]["schema"] == 1
        assert validate(on_disk["data"])
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_options_link_auto_detect_validation_and_tolerances(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        sources, exported, (proxy_entry,) = await setup_site(hass)
        entry = shadow_entry(hass, sources, {"metadata_binding": {}})
        flow = OCPPShadowOptionsFlow()
        flow.hass, flow.handler = hass, entry.entry_id
        await flow.async_step_init({})
        form = await flow.async_step_export({})
        assert form["type"] == "form" and form["step_id"] == "reconcile"
        defaults = {str(k): k.default() for k in form["data_schema"].schema
                    if hasattr(k, "default") and callable(k.default)}
        assert defaults["legacy_proxy_entry_id"] == proxy_entry.entry_id  # the only one
        assert defaults["reconcile_import_tolerance_pct"] == 1.0
        assert defaults["reconcile_import_floor_kwh"] == 0.02
        values = {RECONCILE_FIELDS[k]: float(v) for k, v in DEFAULT_TOLERANCES.items()}
        bad = await flow.async_step_reconcile({"legacy_proxy_entry_id": "bogus", **values})
        assert bad["errors"]["base"] == "invalid_recorder_link"
        bad = await flow.async_step_reconcile({"legacy_proxy_entry_id": proxy_entry.entry_id,
                                               **values, "reconcile_import_tolerance_pct": 0})
        assert bad["errors"]["base"] == "invalid_reconciliation_tolerances"
        saved = await flow.async_step_reconcile({"legacy_proxy_entry_id": proxy_entry.entry_id,
                                                 **values, "reconcile_export_tolerance_pct": 3})
        assert saved["type"] == "create_entry"
        options = saved["data"]
        assert options["legacy_proxy_entry_id"] == proxy_entry.entry_id
        assert options["reconciliation_tolerances"] == {**DEFAULT_TOLERANCES, "export_pct": "3"}
        assert options["export_binding"] == {} and options["metadata_binding"] == {}
        assert resolve_link(hass, options) == (proxy_entry.entry_id, "configured")
        assert resolve_link(hass, {}) == (proxy_entry.entry_id, "auto_detected")
        unlinked = shadow_reconcile_options(hass, {"legacy_proxy_entry_id": "none"})
        assert unlinked["legacy_proxy_entry_id"] is None
        assert resolve_link(hass, unlinked) == (None, "not_linked")
        # A wallet or OCPP entry is not a legacy recorder.
        with pytest.raises(ValueError, match="invalid_recorder_link"):
            shadow_reconcile_options(hass, {"legacy_proxy_entry_id": entry.entry_id})
        # Two legacy recorders: no auto-detection and no preselection.
        second = config_entry(DOMAIN, {**proxy_entry.data, "state_entity": "sensor.other"},
                              title="Second recorder")
        hass.config_entries._entries[second.entry_id] = second
        assert resolve_link(hass, {}) == (None, "ambiguous")
        form = await flow.async_step_reconcile()
        link_key = next(k for k in form["data_schema"].schema
                        if str(k) == "legacy_proxy_entry_id")
        assert not callable(getattr(link_key, "default", None)) or link_key.default() in (
            proxy_entry.entry_id,)  # stored choice only, never a guess between two
        hass.config_entries._entries.pop(proxy_entry.entry_id)
        assert resolve_link(hass, options) == (None, "invalid")
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_no_legacy_recorder_keeps_existing_options_flow(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        sources, exported, _ = await setup_site(hass, proxies=0)
        entry = shadow_entry(hass, sources, {"metadata_binding": {},
                                             "legacy_proxy_entry_id": None})
        flow = OCPPShadowOptionsFlow()
        flow.hass, flow.handler = hass, entry.entry_id
        await flow.async_step_init({})
        saved = await flow.async_step_export({})
        assert saved["type"] == "create_entry"
        assert saved["data"]["legacy_proxy_entry_id"] is None  # earlier choice kept
        assert resolve_link(hass, {}) == (None, "no_legacy_recorder")
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_no_services_registered_beyond_existing(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        await async_setup(hass, {})
        assert set(hass.services.async_services_for_domain(DOMAIN)) == set(SERVICES)
        for name in ("recorder.py", "recorder_reconciliation.py"):
            source = (COMPONENT / name).read_text()
            for forbidden in ("async_register", "services.async_call", "async_call(",
                              "select.", "switch."):
                assert forbidden not in source
        assert not any("recorder" in name or "select" in name or "switch" in name
                       for name in SERVICES)
    finally:
        await hass.async_stop(force=True)


def recorder_shadow():
    """OCPP shadow carrying aligned reconciliation and readiness evidence."""
    span = import_span()
    aligned = compare(span, sigen(1900))
    assert aligned["within_tolerance"]
    data = {"mode": "ocpp_import_shadow", "latest_session": None,
            "recorder": {"settlement_recorder": "legacy_sigen",
                         "ocpp": {"overall_level": "fresh_attributable_sample"},
                         "reconciliation": {"last_result": aligned}}}
    return SimpleNamespace(mode="ocpp_import_shadow", async_request_refresh=AsyncMock(),
                           data=data, archive=[span], sources={"import": "sensor.synthetic"})


async def gate_session_review(api, hass):
    from custom_components.bsv_settlement.session_review import SessionReviews
    reviews = object.__new__(SessionReviews)
    reviews.hass = hass
    await reviews.source("shadow", "ocpp-shadow-a")


async def gate_collection(api, hass):
    from custom_components.bsv_settlement.collection import DriverCollections
    await DriverCollections(api).source({
        "proxy_config_entry_id": "shadow", "terms": {"session_id": "ocpp-shadow-a"}})


async def gate_session_closure(api, hass):
    from custom_components.bsv_settlement.session_closure import ClosedSessions
    await ClosedSessions(api).inspect({"proxy_config_entry_id": "shadow",
                                       "session_id": "ocpp-shadow-a"})


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
async def test_financial_gates_unchanged_by_reconciliation(gate):
    shadow = recorder_shadow()
    hass = SimpleNamespace(data={"bsv_settlement": {"shadow": shadow}})
    api = SimpleNamespace(hass=hass, saved={"ongoing_credit_policy": {}},
                          auto_credits=SimpleNamespace(policy={"enabled": True}))
    with pytest.raises(WalletError, match="recorder"):
        await gate(api, hass)
    shadow.async_request_refresh.assert_not_called()
    assert api.saved["ongoing_credit_policy"] == {}


def test_financial_modules_still_gate_on_sensor_proxy_only():
    for name in ("budget", "collection", "ongoing_credit", "session_closure", "session_review",
                 "weekly", "portal"):
        source = (COMPONENT / f"{name}.py").read_text()
        assert "sensor_proxy" in source
        assert "recorder_reconciliation" not in source and "from .recorder" not in source
