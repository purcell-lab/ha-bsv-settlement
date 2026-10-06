"""Native OCPP lifecycle wired into the live shadow observer: fixtures through real HA.

Shadow only: no wallet, payment, charger or recorder-selection path is exercised
or reachable. Fixtures are the committed sanitised/synthetic replay set.
"""
from datetime import timedelta
from decimal import Decimal
from functools import lru_cache
import json
import logging
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import patch

import pytest

pytest.importorskip("homeassistant")
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.bsv_settlement.ocpp_lifecycle import (
    MAX_SESSIONS, AttributionError, LifecycleTracker, id_tag_ref, ownership_key)
from custom_components.bsv_settlement.ocpp_replay import load, replay
from custom_components.bsv_settlement.ocpp_shadow import (
    EXPORT, METADATA, METRICS, OCPPShadowCoordinator, export_binding, lifecycle_view,
    metadata_binding,
    reference_binding, session_scope, source_binding, validate_lifecycle)
from custom_components.bsv_settlement.ocpp_shadow_ledger import instant
from custom_components.bsv_settlement.records import STORES
from custom_components.bsv_settlement.sensor import OCPPLifecycleSensor

FIXTURES = Path(__file__).resolve().parent / "fixtures/ocpp_lifecycle"
ALL = sorted(p.name for p in FIXTURES.glob("*.json"))
POLL = timedelta(seconds=15)
UNAVAILABLE = ("unavailable", "unknown")
# Fixture key -> OCPP unique-ID slug of the same connector.
SLUGS = {"status": METRICS["status"], "transaction": METRICS["transaction"],
         "import_register": METRICS["import"], "export_register": EXPORT["register"],
         "flow": EXPORT["flow"], "session_export": EXPORT["session"],
         "id_tag": "id_tag", "soc": "soc"}
RAW_TAG = "RAWIDTAG00000001"


def config_entry(domain, data=None, options=None):
    return ConfigEntry(version=1, minor_version=1, domain=domain, title="Synthetic lifecycle",
                       data=data or {}, source="user", unique_id=None, options=options or {},
                       discovery_keys=MappingProxyType({}), subentries_data=[])


class Clock:
    """Controls the observer's clock; HA state times are set per event."""

    def __init__(self):
        self.now = None
        self.module = SimpleNamespace(utcnow=lambda: self.now,
                                      parse_datetime=dt_util.parse_datetime)


async def charger(hass, cpid="charger-1", fixture=None, ref_unit="MWh"):
    """OCPP connector entities in the registry (same config entry and device)."""
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    if hasattr(dr, "async_setup"):
        dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    native = config_entry("ocpp")
    hass.config_entries._entries[native.entry_id] = native
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=native.entry_id, identifiers={("ocpp", cpid)})
    registry = er.async_get(hass)
    ids = {}
    for key, slug in [*SLUGS.items(), *[("meta_" + k, v) for k, v in METADATA.items()]]:
        row = registry.async_get_or_create(
            "sensor", "ocpp", f"ocpp.{cpid}.{slug}.sensor", config_entry=native,
            device_id=device.id, suggested_object_id=f"lc_{slug}")
        ids[key] = row.entity_id
    ref = registry.async_get_or_create("sensor", "fixture", "fixture.reference_discharge",
                                       suggested_object_id="lc_reference_discharge")
    ids["ref_export"] = ref.entity_id
    hass.states.async_set(ref.entity_id, "unknown", {"unit_of_measurement": ref_unit})
    for key, row in ((fixture or {}).get("metadata") or {}).items():
        hass.states.async_set(ids["meta_" + key], row["state"], row.get("attributes") or {})
    return ids


def entry_for(hass, ids, with_reference=True):
    sources = {"import": ids["import_register"], "transaction": ids["transaction"],
               "status": ids["status"]}
    binding = source_binding(hass, sources)
    exported = export_binding(hass, binding, {"register": ids["export_register"],
                                              "flow": ids["flow"],
                                              "session": ids["session_export"]})
    options = {"metadata_binding": metadata_binding(
                   hass, binding, {k: ids["meta_" + k] for k in METADATA}),
               "export_binding": exported}
    if with_reference:
        options["export_reference_binding"] = reference_binding(
            hass, binding, exported, ids["ref_export"])
    data = {**{k + "_entity": v for k, v in sources.items()},
            "backend": "ocpp_import_shadow", "source_binding": binding}
    return config_entry("bsv_settlement", data, options)


class LiveRun:
    """One fixture through OCPPShadowCoordinator, mirroring ocpp_replay.Replay.run."""

    def __init__(self, hass, fixture, ids, entry, clock):
        self.hass, self.fixture, self.ids, self.entry, self.clock = hass, fixture, ids, entry, clock
        self.coord = None
        self.values = {}
        self.restarts = 0

    async def start(self):
        self.coord = OCPPShadowCoordinator(self.hass, self.entry)
        await self.coord.load()

    async def stop(self):
        await self.coord.close()
        self.coord = None

    async def poll(self, at):
        self.clock.now = at
        if self.coord is not None:
            await self.coord._async_update_data()

    async def poll_until(self, clock, until):
        while clock is not None and clock + POLL < until:
            clock += POLL
            await self.poll(clock)
        return clock

    def set(self, key, value, attributes, moment):
        units = self.fixture["units"]
        attrs = dict(attributes)
        if key in units:
            attrs["unit_of_measurement"] = units[key]
        self.hass.states.async_set(self.ids[key], value, attrs, force_update=True,
                                   timestamp=moment.timestamp())

    async def run(self):
        clock = None
        await self.start()
        for event in self.fixture["events"]:
            at, key, value = event[0], event[1], event[2]
            moment = instant(at)
            clock = await self.poll_until(clock, moment)
            self.clock.now = moment
            self.values[key] = value
            if key in self.ids:
                self.set(key, value, event[3] if len(event) == 4 else {}, moment)
                await self.hass.async_block_till_done()
            await self.transition(key, value)
            if key in ("status", "transaction", "import_register", "export_register", "flow",
                       "session_export", "ref_export"):
                clock = moment
            elif clock is None:
                clock = moment
        end = instant(self.fixture["window"]["end"])
        await self.poll_until(clock, end + POLL)
        return self.coord.lifecycle_sessions(end.isoformat())

    async def transition(self, key, value):
        """HA restart: the observer unloads and is rebuilt from its stores (as in replay)."""
        if key not in ("status", "transaction"):
            return
        pair = [self.values.get(k) for k in ("status", "transaction")]
        if self.coord is not None and all(v in UNAVAILABLE for v in pair):
            await self.stop()
        elif self.coord is None and all(v is not None and v not in UNAVAILABLE for v in pair):
            self.restarts += 1
            await self.start()


@lru_cache(maxsize=None)
def replayed(name):
    return replay(FIXTURES / name)


async def live(tmp_path, name):
    fixture = load(FIXTURES / name)
    hass = HomeAssistant(str(tmp_path))
    clock = Clock()
    try:
        clock.now = instant(fixture["events"][0][0]) - timedelta(seconds=1)
        with patch("custom_components.bsv_settlement.ocpp_shadow.dt_util", clock.module):
            ids = await charger(hass, fixture=fixture,
                                ref_unit=fixture["units"].get("ref_export", "MWh"))
            entry = entry_for(hass, ids, "ref_export" in fixture["units"])
            runner = LiveRun(hass, fixture, ids, entry, clock)
            sessions = await runner.run()
            summary = runner.coord.summary()
            store = Path(runner.coord.lifecycle_store.path)
            await runner.stop()
            return fixture, runner, sessions, summary, store.read_text()
    finally:
        await hass.async_stop(force=True)


def same_instant(a, b):
    return (a is None and b is None) or (a is not None and b is not None and instant(a) == instant(b))


# Flags that depend on inputs only the offline replay has: the reference counter
# history (live comparison is the recorder reconciliation's job) and the
# end-of-window marker.
OFFLINE_ONLY = ("reference", "divergence", "open_at_window_end")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ALL)
async def test_live_coordinator_matches_offline_replay(tmp_path, name):
    fixture, runner, sessions, summary, stored = await live(tmp_path, name)
    expected = replayed(name)
    assert runner.restarts == len(expected["ha_restarts"])
    assert [s["transaction_id"] for s in sessions] == [
        s["transaction_id"] for s in expected["sessions"]]
    for got, want in zip(sessions, expected["sessions"]):
        assert got["identity"]["charger_id"] == want["identity"]["charger_id"] == "charger-1"
        assert got["identity"]["connector_id"] == want["identity"]["connector_id"] == 1
        assert same_instant(got["started_at"], want["started_at"])
        assert same_instant(got["ended_at"], want["ended_at"])
        assert got["end_reason"] == want["end_reason"]
        assert got["start_observed"] == want["start_observed"]
        assert len(got["ha_restarts"]) == len(want["ha_restarts"])
        assert got["late_final_import_kwh"] == want["late_final_import_kwh"]
        assert got["untrusted_metadata"]["id_tag_refs"] == want["untrusted_metadata"]["id_tag_refs"]
        for direction in ("import", "export"):
            g, w = got[direction], want[direction]
            assert g["observed_kwh"] == w["observed_kwh"], (direction, got["transaction_id"])
            assert g["span_count"] == w["span_count"]
            assert g["span_end_reasons"] == w["span_end_reasons"]
            assert g.get("observed_lower_kwh") == w.get("observed_lower_kwh")
            assert g.get("observed_upper_kwh") == w.get("observed_upper_kwh")
            assert g.get("grades") == w.get("grades")
        keep = lambda flags: {f for f in flags if not any(o in f for o in OFFLINE_ONLY)}
        assert keep(got["quality_flags"]) == keep(want["quality_flags"])
        assert got["billing_eligible"] is False and got["settlement_owner"] == "legacy_sigen"
        assert got["vehicle_attribution"] == "unresolved"
        assert ownership_key(got) == tuple(got["identity"][k] for k in got["identity"])
    lifecycle = summary["lifecycle"]
    assert lifecycle["billing_eligible"] is False and lifecycle["settlement_owner"] == "legacy_sigen"
    assert lifecycle["selector_implemented"] is False
    assert "IDTAG-" not in stored and "IDTAG-" not in json.dumps(summary)


@pytest.mark.asyncio
async def test_live_late_stop_and_late_final_reading_are_reported(tmp_path):
    _, _, sessions, summary, _ = await live(tmp_path, "synthetic_late_duplicate_stop.json")
    first, second = sessions
    assert "late_or_duplicate_transaction_event" in first["quality_flags"]
    assert Decimal(first["late_final_import_kwh"]) == Decimal("0.05")
    assert lifecycle_view(first)["lifecycle_state"] == "late_final"
    assert summary["lifecycle"]["session_count"] == 2
    assert Decimal(first["import"]["observed_kwh"]) == Decimal("2.28")
    assert Decimal(second["import"]["observed_kwh"]) == Decimal("1.08")


# --- Restart, legacy store and refusal -------------------------------------

async def simple(hass, clock, tag=RAW_TAG):
    ids = await charger(hass)
    for key, value in (("status", "Available"), ("transaction", "0"), ("id_tag", tag),
                       ("soc", "40"), ("import_register", "100")):
        hass.states.async_set(ids[key], value, {"unit_of_measurement": "kWh",
                                                "context": "Sample.Periodic"})
    return ids, entry_for(hass, ids, with_reference=False)


async def started(hass, entry):
    coord = OCPPShadowCoordinator(hass, entry)
    await coord.load()
    return coord


def tick(clock, seconds):
    clock.now = clock.now + timedelta(seconds=seconds)
    return clock.now.timestamp()


@pytest.mark.asyncio
async def test_restart_mid_session_keeps_identity_and_is_an_outage_not_a_stop(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    hass = HomeAssistant(str(tmp_path))
    clock = Clock()
    clock.now = dt_util.utcnow()
    try:
        with patch("custom_components.bsv_settlement.ocpp_shadow.dt_util", clock.module):
            ids, entry = await simple(hass, clock)
            coord = await started(hass, entry)
            assert coord.summary()["lifecycle"]["current_session"] is None
            hass.states.async_set(ids["transaction"], "42", timestamp=tick(clock, 5))
            hass.states.async_set(ids["status"], "Charging", timestamp=tick(clock, 1))
            await hass.async_block_till_done()
            before = coord.summary()["lifecycle"]["current_session"]
            assert before["identity"]["transaction_id"] == "42"
            assert before["start_observed"] is True and before["lifecycle_state"] == "active"
            assert before["id_tag_ref"] == id_tag_ref(RAW_TAG)
            await coord.close()
            tick(clock, 120)  # HA down; the charger keeps the same transaction
            coord = await started(hass, entry)
            after = coord.summary()["lifecycle"]
            current = after["current_session"]
            assert current["identity"] == before["identity"]
            assert current["lifecycle_state"] == "active"
            assert current["restart_continuity"] == "continued_across_restart"
            assert current["ha_restart_count"] == 1
            assert "ha_restart_during_session" in current["quality_flags"]
            assert after["outage_open"] is False and after["session_count"] == 1
            hass.states.async_set(ids["transaction"], "0", timestamp=tick(clock, 60))
            await hass.async_block_till_done()
            last = coord.summary()["lifecycle"]["last_session"]
            assert last["identity"] == before["identity"]
            assert last["lifecycle_state"] == "stopped" and last["end_reason"] == "transaction_cleared"
            sensor = OCPPLifecycleSensor(coord, entry, "session_lifecycle", "Lifecycle", None)
            coord.async_set_updated_data(coord.summary())
            assert sensor.native_value == "stopped"
            attrs = sensor.extra_state_attributes
            assert attrs["billing_eligible"] is False and attrs["settlement_owner"] == "legacy_sigen"
            assert attrs["selector_implemented"] is False and attrs["charger_control"] is False
            await coord.close()
            stored = Path(coord.lifecycle_store.path).read_text()
            shadow = Path(coord.store.path).read_text()
            # The raw idTag never reaches storage, attributes or logs.
            ours = "\n".join(r.getMessage() for r in caplog.records
                             if r.name.startswith("custom_components"))
            for text in (stored, shadow, json.dumps(attrs, default=str), ours):
                assert RAW_TAG not in text
            assert id_tag_ref(RAW_TAG) in stored
            raw = json.loads(stored)
            assert (raw["version"], raw["minor_version"]) == (1, 1)
            assert set(raw["data"]) == STORES["ocpp_lifecycle"].keys
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_transaction_change_during_outage_is_superseded_not_bridged(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    clock = Clock()
    clock.now = dt_util.utcnow()
    try:
        with patch("custom_components.bsv_settlement.ocpp_shadow.dt_util", clock.module):
            ids, entry = await simple(hass, clock)
            coord = await started(hass, entry)
            hass.states.async_set(ids["transaction"], "42", timestamp=tick(clock, 5))
            await hass.async_block_till_done()
            await coord.close()
            hass.states.async_set(ids["transaction"], "43", timestamp=tick(clock, 60))
            coord = await started(hass, entry)
            lifecycle = coord.summary()["lifecycle"]
            assert lifecycle["last_session"]["identity"]["transaction_id"] == "42"
            assert lifecycle["last_session"]["lifecycle_state"] == "superseded"
            assert "stop_not_observed" in lifecycle["last_session"]["quality_flags"]
            current = lifecycle["current_session"]
            assert current["identity"]["transaction_id"] == "43"
            assert current["start_observed"] is False
            await coord.close()
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_older_store_without_lifecycle_loads_as_not_recorded(tmp_path):
    """An ocpp_shadow store written before this release: lifecycle is never invented."""
    hass = HomeAssistant(str(tmp_path))
    clock = Clock()
    clock.now = dt_util.utcnow()
    try:
        with patch("custom_components.bsv_settlement.ocpp_shadow.dt_util", clock.module):
            ids, entry = await simple(hass, clock)
            hass.states.async_set(ids["transaction"], "41", timestamp=tick(clock, 1))
            hass.states.async_set(ids["status"], "Charging", timestamp=tick(clock, 1))
            coord = await started(hass, entry)
            for n in range(1, 4):  # an earlier transaction observed by the older release
                hass.states.async_set(ids["import_register"], str(100 + n / 10), {
                    "unit_of_measurement": "kWh", "context": "Sample.Periodic"},
                    timestamp=tick(clock, 60))
                await hass.async_block_till_done()
            hass.states.async_set(ids["transaction"], "42", timestamp=tick(clock, 5))
            await hass.async_block_till_done()
            await coord.close()
            Path(coord.lifecycle_store.path).unlink()
            shadow = Path(coord.store.path)
            assert json.loads(shadow.read_text())["version"] == 3
            before = shadow.read_text()
            coord = await started(hass, entry)
            lifecycle = coord.summary()["lifecycle"]
            assert lifecycle["state"] == "recording"
            assert lifecycle["history_before_recorded_since"] == "not_recorded"
            assert lifecycle["recorded_since"] == clock.now.isoformat()
            assert lifecycle["last_session"] is None and lifecycle["session_count"] == 1
            current = lifecycle["current_session"]
            assert current["identity"]["transaction_id"] == "42"
            assert "start_not_observed" in current["quality_flags"]
            # Spans the older release recorded for 41 are not attributed to anything.
            assert current["import_observed_kwh"] is None
            assert all(s["native_transaction_id"] == "41"
                       for s in coord.ledger.data["spans"] if s["observed_import_kwh"] != "0")
            assert json.loads(shadow.read_text())["data"]["spans"] == json.loads(before)["data"]["spans"]
            await coord.close()
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["unknown_key", "foreign_charger", "raw_tag", "newer_version",
                                    "billable", "corrupt"])
async def test_refused_lifecycle_store_disables_lifecycle_only_and_is_not_rewritten(tmp_path, change):
    hass = HomeAssistant(str(tmp_path))
    clock = Clock()
    clock.now = dt_util.utcnow()
    try:
        with patch("custom_components.bsv_settlement.ocpp_shadow.dt_util", clock.module):
            ids, entry = await simple(hass, clock)
            coord = await started(hass, entry)
            hass.states.async_set(ids["transaction"], "42", timestamp=tick(clock, 5))
            await hass.async_block_till_done()
            await coord.close()
            path = Path(coord.lifecycle_store.path)
            raw = json.loads(path.read_text())
            data = raw["data"]
            if change == "unknown_key":
                data["future"] = {}
            elif change == "foreign_charger":
                data["tracker"]["charger_id"] = "another"
            elif change == "raw_tag":
                data["tracker"]["sessions"][0]["untrusted_metadata"]["id_tag_refs"] = [RAW_TAG]
            elif change == "newer_version":
                raw["version"] = 2
            elif change == "billable":
                data["tracker"]["sessions"][0]["billing_eligible"] = True
            text = "not json" if change == "corrupt" else json.dumps(raw)
            path.write_text(text)
            coord = await started(hass, entry)
            summary = coord.summary()
            assert summary["lifecycle"]["state"] == "store_refused"
            assert summary["lifecycle"]["current_session"] is None
            assert summary["lifecycle"]["billing_eligible"] is False
            assert summary["state"] in ("waiting", "observing")  # the shadow itself still runs
            await coord.close()
            assert path.read_text() == text
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_lifecycle_fault_never_interrupts_shadow_observation(tmp_path, caplog):
    hass = HomeAssistant(str(tmp_path))
    clock = Clock()
    clock.now = dt_util.utcnow()
    try:
        with patch("custom_components.bsv_settlement.ocpp_shadow.dt_util", clock.module):
            ids, entry = await simple(hass, clock)
            hass.states.async_set(ids["status"], "Charging", timestamp=tick(clock, 1))
            hass.states.async_set(ids["transaction"], "42", timestamp=tick(clock, 1))
            coord = await started(hass, entry)
            path = Path(coord.lifecycle_store.path)
            await coord.close()
            saved = path.read_text()
            coord = await started(hass, entry)
            with patch.object(LifecycleTracker, "update", side_effect=ValueError(RAW_TAG)):
                hass.states.async_set(ids["id_tag"], RAW_TAG + "X", timestamp=tick(clock, 1))
                await hass.async_block_till_done()
            for n in range(1, 4):
                hass.states.async_set(ids["import_register"], str(100 + n / 10), {
                    "unit_of_measurement": "kWh", "context": "Sample.Periodic"},
                    timestamp=tick(clock, 60))
                await hass.async_block_till_done()
            summary = coord.summary()
            assert summary["lifecycle"]["state"] == "failed"
            assert summary["lifecycle"]["billing_eligible"] is False
            assert Decimal(summary["current_span"]["observed_import_kwh"]) == Decimal("0.2")
            await coord.close()
        await hass.async_stop(force=True)  # flushes the pending last good snapshot
        restored = json.loads(path.read_text())["data"]
        before = json.loads(saved)["data"]["tracker"]["sessions"]
        assert [s["identity"] for s in restored["tracker"]["sessions"]] == [
            s["identity"] for s in before]
        assert validate_lifecycle(restored, "charger-1", 1)["tracker"].current is not None
        assert RAW_TAG not in caplog.text and RAW_TAG not in path.read_text()
    finally:
        await hass.async_stop(force=True)


# --- Pure persistence --------------------------------------------------------

def test_tracker_state_round_trip_bounds_and_holds_references_only():
    tracker = LifecycleTracker("charger-1", 1)
    tracker.update("status", "Available", "2026-10-05T00:00:00+00:00")
    tracker.update("transaction", "0", "2026-10-05T00:00:01+00:00")
    for n in range(MAX_SESSIONS + 5):
        tracker.update("id_tag", f"{RAW_TAG}{n}", f"2026-10-05T01:{n:02d}:00+00:00")
        tracker.update("transaction", str(1000 + n), f"2026-10-05T01:{n:02d}:01+00:00")
        tracker.update("transaction", "0", f"2026-10-05T01:{n:02d}:30+00:00")
    tracker.update("transaction", "2000", "2026-10-05T02:00:00+00:00")
    state = tracker.state()
    assert RAW_TAG not in json.dumps(state)
    assert len(state["sessions"]) == MAX_SESSIONS and state["sessions_trimmed"] is True
    again = LifecycleTracker.restore(json.loads(json.dumps(state)), "charger-1", 1)
    assert again.state() == state
    assert again.current["transaction_id"] == "2000"
    # A closed transaction that aged out of the store still never reopens.
    again.update("transaction", "1000", "2026-10-05T02:01:00+00:00")
    assert again.current is None or again.current["transaction_id"] == "2000"
    assert [s["transaction_id"] for s in again.sessions].count("1000") == 0
    with pytest.raises(ValueError):
        LifecycleTracker.restore(state, "charger-2", 1)
    with pytest.raises(AttributionError):
        ownership_key(again.current, id_tag=RAW_TAG)


def test_session_scope_from_pinned_binding():
    def binding(scope):
        return {"import": {"unique_id": f"{scope}.energy_active_import_register.sensor"}}
    assert session_scope(binding("ocpp.charger")) == ("charger", 1)
    assert session_scope(binding("ocpp.charger.conn2")) == ("charger", 2)
