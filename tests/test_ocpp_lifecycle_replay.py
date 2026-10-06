"""OCPP lifecycle replay: sanitised live and synthetic fixtures; no live HA, charger or funds."""
from decimal import Decimal
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import re

import pytest

pytest.importorskip("homeassistant")

from custom_components.bsv_settlement import ocpp_lifecycle
from custom_components.bsv_settlement.ocpp_lifecycle import (
    IDENTITY_FIELDS, UNTRUSTED_FIELDS, AttributionError, LifecycleTracker, SessionIdentity,
    ownership_key)
from custom_components.bsv_settlement.ocpp_replay import load, replay

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/ocpp_lifecycle"
COMPONENT = ROOT / "custom_components/bsv_settlement"
REPORT = ROOT / "docs/qa/ocpp-shadow-report-2026-10-05.md"
REPORT_TITLE = "OCPP native shadow report: live sessions 5–6 October 2026"
ALL = sorted(p.name for p in FIXTURES.glob("*.json"))
LIVE = [name for name in ALL if name.startswith("live_")]


def _module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=None)
def run(name):
    return replay(FIXTURES / name)


def sessions(name):
    return {s["transaction_id"]: s for s in run(name)["sessions"]}


def kwh(value):
    return None if value is None else Decimal(value)


# --- Fixtures --------------------------------------------------------------

def test_fixture_set_covers_live_and_synthetic_edge_cases():
    assert len(LIVE) == 5 and len(ALL) == 12
    labels = {load(FIXTURES / name)["evidence"] for name in ALL}
    assert labels == {"physical_live_sanitised", "synthetic"}


@pytest.mark.parametrize("name", LIVE)
def test_live_fixtures_are_sanitised(name):
    fixture = load(FIXTURES / name)
    text = json.dumps([fixture["events"], fixture["metadata"], fixture["units"]])
    assert fixture["evidence"] == "physical_live_sanitised"
    tags = {e[2] for e in fixture["events"] if e[1] == "id_tag"}
    assert tags <= {"", "unavailable", "unknown"} | {f"IDTAG-{i:02d}" for i in range(1, 100)}
    # No entity IDs, endpoints, serials or non-allow-listed attributes leak.
    for needle in ("sensor.", "garage", "webhook", "nabu", "serial", "reference_entity",
                   "friendly_name", "divergence"):
        assert needle not in text
    assert "meter_serial_number" not in json.dumps(fixture["metadata"])


def test_synthetic_fixtures_regenerate_byte_for_byte(tmp_path):
    builder = _module("build_ocpp_fixtures")
    for fixture in [*builder.synthetic(), builder.v2g_negative_import()]:
        path = tmp_path / (fixture["fixture"].replace("-", "_") + ".json")
        builder.dump(fixture, path)
        assert path.read_text() == (FIXTURES / path.name).read_text()
        assert fixture["evidence"] == "synthetic"


def test_builder_replaces_id_tags_and_drops_attributes():
    builder = _module("build_ocpp_fixtures")
    capture = {
        "sensor.charger_id_tag": [
            {"state": "SECRETTAGVALUE01", "last_updated": "2026-10-05T12:00:01+10:00",
             "attributes": {"friendly_name": "x"}},
            {"state": "SECRETTAGVALUE02", "last_updated": "2026-10-05T12:00:02+10:00"}],
        "sensor.charger_energy_active_import_register": [
            {"state": "1.0", "last_updated": "2026-10-05T12:00:01+10:00", "attributes": {
                "unit_of_measurement": "kWh", "context": "Sample.Periodic",
                "serial": "X", "friendly_name": "y"}}],
        "sensor.unrelated_wallet": [{"state": "1", "last_updated": "2026-10-05T12:00:01+10:00"}],
    }
    fixture = builder.build(capture, "t", "2026-10-05T12:00:00+10:00",
                            "2026-10-05T12:01:00+10:00", "test")
    text = json.dumps(fixture["events"])
    assert "SECRETTAG" not in json.dumps(fixture)
    assert "serial" not in text and "wallet" not in text and "friendly" not in text
    assert [e[2] for e in fixture["events"] if e[1] == "id_tag"] == ["IDTAG-01", "IDTAG-02"]
    (register,) = [e for e in fixture["events"] if e[1] == "import_register"]
    assert register[3] == {"context": "Sample.Periodic"}


def test_loader_refuses_malformed_fixtures(tmp_path):
    good = json.loads((FIXTURES / "synthetic_fault_zero_energy.json").read_text())
    for change in ({"schema": 2}, {"evidence": "simulated_as_live"}, {"events": []}):
        path = tmp_path / "bad.json"
        path.write_text(json.dumps({**good, **change}))
        with pytest.raises(ValueError):
            load(path)
    bad = {**good, "events": [good["events"][1], good["events"][0], *good["events"][2:]]}
    if bad["events"][0][0] != bad["events"][1][0]:
        path = tmp_path / "order.json"
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            load(path)
    path = tmp_path / "key.json"
    path.write_text(json.dumps({**good, "events": [["2026-01-01T00:00:00+00:00", "wallet", "1"]]}))
    with pytest.raises(ValueError):
        load(path)


# --- Invariants over every fixture ----------------------------------------

@pytest.mark.parametrize("name", ALL)
def test_replay_invariants(name):
    result = run(name)
    assert result["billing_eligible"] is False and result["settlement_owner"] == "legacy_sigen"
    assert result["overlapping_sessions"] == []
    keys = [tuple(s["ownership_key"]) for s in result["sessions"]]
    assert len(keys) == len(set(keys))
    owners = {}
    for session in result["sessions"]:
        assert session["billing_eligible"] is False
        assert session["vehicle_attribution"] == "unresolved"
        assert session["vehicle_attribution_automatic"] is False
        assert session["ownership_key"] == [session["identity"][f] for f in IDENTITY_FIELDS]
        for direction in ("import", "export"):
            row = session[direction]
            if row["span_count"] == 0:
                # Never zero-filled: unobserved is None with a flag.
                assert row["observed_kwh"] is None
                assert f"no_{direction}_observation" in session["quality_flags"]
            for span_id in row["span_ids"]:
                assert span_id not in owners, "a span was attributed to two sessions"
                owners[span_id] = session["transaction_id"]
    for span in [*result["import_spans"], *result["export_spans"]]:
        assert span["billing_eligible"] is False
        if span["span_id"] in owners:
            assert span["native_transaction_id"] == owners[span["span_id"]]
    # Raw or placeholder idTags never appear in replay output; only one-way refs.
    assert "IDTAG-" not in json.dumps(result["sessions"])
    for row in result["reconciliations"]:
        assert row["billing_eligible"] is False and row["settlement_owner"] == "legacy_sigen"


# --- Synthetic edge cases ---------------------------------------------------

def test_start_stop_import_matches_reference():
    session = sessions("synthetic_start_stop_import.json")["1001"]
    assert session["start_observed"] and session["end_reason"] == "transaction_cleared"
    assert session["import"]["comparison"] == "within_tolerance"
    assert kwh(session["import"]["observed_kwh"]) == Decimal("3.48")
    assert kwh(session["import"]["divergence_kwh"]) == 0
    # The first interval before the baseline is reported, never invented.
    assert kwh(session["import"]["reference_outside_spans_kwh"]) == Decimal("0.12")


def test_ha_restart_mid_session_keeps_one_identity_and_excludes_downtime():
    result = run("synthetic_ha_restart_mid_session.json")
    assert len(result["sessions"]) == 1 and len(result["ha_restarts"]) == 1
    session = result["sessions"][0]
    assert len(session["ha_restarts"]) == 1
    assert "ha_restart_during_session" in session["quality_flags"]
    assert session["import"]["span_count"] == 2
    assert session["import"]["comparison"] == "within_tolerance"
    # Energy across the restart is excluded and surfaced, not bridged.
    assert kwh(session["import"]["observed_kwh"]) < kwh(session["import"]["reference_session_kwh"])
    assert "import_reference_energy_outside_observed_spans" in session["quality_flags"]


def test_late_and_duplicate_stop_never_reopen_or_shift_energy():
    result = run("synthetic_late_duplicate_stop.json")
    assert [s["transaction_id"] for s in result["sessions"]] == ["2001", "2002"]
    first, second = result["sessions"]
    assert "late_or_duplicate_transaction_event" in first["quality_flags"]
    assert "late_final_reading_unattributed" in first["quality_flags"]
    assert kwh(first["late_final_import_kwh"]) == Decimal("0.05")
    # The late final reading is attributed to neither session.
    assert kwh(first["import"]["observed_kwh"]) == Decimal("2.28")
    assert kwh(second["import"]["observed_kwh"]) == Decimal("1.08")
    assert second["import"]["comparison"] == "within_tolerance"
    assert first["ended_at"] < second["started_at"]


def test_back_to_back_sessions_never_bridge_export_across_the_boundary():
    fixture = load(FIXTURES / "synthetic_back_to_back_export.json")
    result = run("synthetic_back_to_back_export.json")
    first, second = result["sessions"]
    assert (first["transaction_id"], second["transaction_id"]) == ("3001", "3002")
    registers = [Decimal(e[2]) for e in fixture["events"] if e[1] == "export_register"]
    observed = kwh(first["export"]["observed_kwh"]) + kwh(second["export"]["observed_kwh"])
    # The register kept integrating through the 40 s boundary; that delta is excluded.
    assert observed < registers[-1] - registers[0]
    span = next(s for s in result["export_spans"] if s["native_transaction_id"] == "3002")
    assert span["first_meter_ha_updated_at"] > second["started_at"]
    assert span["partial_start"] is True
    for session in (first, second):
        assert session["export"]["comparison"] == "within_tolerance"
        assert "export_estimated" in session["quality_flags"]
    assert second["vehicle_evidence"]["assessment"] == "continuous"
    assert second["vehicle_attribution"] == "unresolved"


@pytest.mark.parametrize("name", ["synthetic_fault_zero_energy.json",
                                  "live_2026-10-06_fault_zero_energy.json",
                                  "live_2026-10-05_fault_early.json"])
def test_fault_zero_energy_transaction_is_flagged_not_zero(name):
    (session,) = run(name)["sessions"]
    flags = set(session["quality_flags"])
    assert {"never_charging", "no_meter_observation", "no_import_observation",
            "no_export_observation"} <= flags
    assert flags & {"charger_fault", "charger_fault_at_end"}
    assert session["import"]["observed_kwh"] is None
    assert session["export"]["observed_kwh"] is None
    assert session["import"]["comparison"] == "unresolved_no_observation"
    # The reference shows zero; the OCPP side stays unobserved, not zero.
    assert kwh(session["import"]["reference_session_kwh"]) == 0


def test_meter_gap_is_excluded_flagged_and_never_merged():
    session = sessions("synthetic_meter_gap.json")["1001"]
    assert session["import"]["span_count"] == 2
    assert "import_invalid_or_stale_meter" in session["quality_flags"]
    assert "import_reference_energy_outside_observed_spans" in session["quality_flags"]
    assert kwh(session["import"]["observed_kwh"]) == Decimal("3.36")
    assert kwh(session["import"]["reference_outside_spans_kwh"]) == Decimal("1.44")
    assert session["import"]["comparison"] == "within_tolerance"


def test_v2g_negative_import_becomes_estimated_export_within_tolerance():
    session = sessions("synthetic_v2g_negative_import.json")["5001"]
    export = session["export"]
    assert export["comparison"] == "within_tolerance"
    assert Decimal("0") < kwh(export["divergence_pct"]) <= Decimal("5")
    assert export["grades"] == ["unreliable"]  # 60 s floor on a stepped profile
    assert kwh(export["observed_lower_kwh"]) <= kwh(export["reference_observed_kwh"]) <= kwh(
        export["observed_upper_kwh"])
    assert "export_estimated" in session["quality_flags"]
    # The Wh import register is flat while discharging. The window opens mid-session,
    # so the first sample is only a baseline: 0.18 kWh of pre-discharge import counts.
    assert session["import"]["comparison"] == "within_tolerance"
    assert kwh(session["import"]["observed_kwh"]) == Decimal("0.18")
    assert "start_not_observed" in session["quality_flags"]


# --- Live sentinel sessions -------------------------------------------------

def test_live_long_v2g_session_survives_two_restarts_then_car_swap():
    result = run("live_2026-10-05_v2g_restarts_car_swap.json")
    assert [s["transaction_id"] for s in result["sessions"]] == ["1791165819", "1791196678"]
    first, second = result["sessions"]
    assert len(first["ha_restarts"]) == 2 and first["end_reason"] == "transaction_cleared"
    assert first["started_at"].startswith("2026-10-05T12:03:39")
    assert first["ended_at"].startswith("2026-10-05T20:37:21")
    assert second["started_at"].startswith("2026-10-05T20:37:58")
    assert first["import"]["comparison"] == "within_tolerance"
    assert abs(kwh(first["import"]["divergence_pct"])) <= 1
    assert first["export"]["comparison"] == "within_tolerance"
    assert abs(kwh(first["export"]["divergence_pct"])) <= 5
    assert kwh(first["export"]["reference_session_kwh"]) == Decimal("41.28")
    # Car swap: new identity, different idTag, SoC discontinuity is evidence only.
    assert first["untrusted_metadata"]["id_tag_refs"] != second["untrusted_metadata"]["id_tag_refs"]
    assert second["vehicle_evidence"]["assessment"] == "discontinuous"
    assert second["vehicle_attribution"] == "unresolved"
    assert second["export"]["comparison"] == "within_tolerance"


def test_live_restart_mid_session_is_one_session_with_partial_start():
    (session,) = run("live_2026-10-05_restart_mid_session.json")["sessions"]
    assert session["transaction_id"] == "1791198138"
    assert len(session["ha_restarts"]) == 1
    assert {"start_not_observed", "ha_restart_during_session"} <= set(session["quality_flags"])
    assert session["export"]["comparison"] == "within_tolerance"


def test_live_same_idtag_on_back_to_back_transactions_stays_two_sessions():
    by_tx = sessions("live_2026-10-06_restarts_back_to_back_same_idtag.json")
    assert list(by_tx) == ["1791210279", "1791235916", "1791236227"]
    short, following = by_tx["1791235916"], by_tx["1791236227"]
    assert short["untrusted_metadata"]["id_tag_refs"] == following["untrusted_metadata"]["id_tag_refs"]
    assert short["ownership_key"] != following["ownership_key"]
    assert short["ended_at"] < following["started_at"]
    assert len(by_tx["1791210279"]["ha_restarts"]) == 2
    assert len(following["ha_restarts"]) == 1
    assert following["import"]["comparison"] == "within_tolerance"
    assert following["export"]["comparison"] == "within_tolerance"


# --- Attribution contract ---------------------------------------------------

def test_identity_is_exactly_charger_connector_transaction_start():
    assert IDENTITY_FIELDS == ("charger_id", "connector_id", "transaction_id", "started_at")
    assert not set(IDENTITY_FIELDS) & set(UNTRUSTED_FIELDS)
    identity = SessionIdentity("charger-1", 1, "42", "2026-10-05T12:00:00+10:00")
    assert ownership_key(identity) == ("charger-1", 1, "42", "2026-10-05T12:00:00+10:00")
    for bad in ("0", "", "unavailable"):
        with pytest.raises(AttributionError):
            SessionIdentity("charger-1", 1, bad, "2026-10-05T12:00:00+10:00")


@pytest.mark.parametrize("hint", ["id_tag", "idTag", "vehicle", "id_tag_ref"])
def test_untrusted_metadata_is_refused_as_ownership_key(hint):
    identity = SessionIdentity("charger-1", 1, "42", "2026-10-05T12:00:00+10:00")
    with pytest.raises(AttributionError):
        ownership_key(identity, **{hint: "IDTAG-01"})


def test_id_tag_never_splits_or_merges_sessions():
    tracker = LifecycleTracker("charger-1", 1)
    events = [("status", "Available"), ("transaction", "0"), ("id_tag", "A"),
              ("transaction", "1"), ("status", "Charging"),
              ("id_tag", "B"),  # integration regenerated the tag: same session
              ("status", "Finishing"), ("transaction", "0"),
              ("transaction", "2"), ("status", "Charging"),  # same tag B, new session
              ("transaction", "0")]
    for second, (key, value) in enumerate(events):
        tracker.update(key, value, f"2026-10-05T12:00:{second:02d}+10:00")
    first, second = tracker.finish("2026-10-05T12:01:00+10:00")
    assert (first["transaction_id"], second["transaction_id"]) == ("1", "2")
    assert first["untrusted_metadata"]["id_tag_changes"] == 1
    assert "id_tag_changed_within_transaction" in first["quality_flags"]
    assert first["untrusted_metadata"]["id_tag_refs"][-1] == second["untrusted_metadata"]["id_tag_refs"][0]
    assert ownership_key(first) != ownership_key(second)
    text = json.dumps([first, second])
    assert '"A"' not in text and '"B"' not in text


def test_only_lifecycle_replay_and_shadow_observer_touch_id_tags():
    pattern = re.compile(r"id_?tag", re.IGNORECASE)
    # The live shadow observer subscribes to the connector idTag sensor and hands
    # the value straight to the tracker; every other mention is a one-way reference.
    allowed = {"ocpp_lifecycle.py", "ocpp_replay.py", "ocpp_shadow.py"}
    offenders = [p.name for p in COMPONENT.glob("*.py")
                 if p.name not in allowed and pattern.search(p.read_text())]
    assert offenders == []
    raw = [line.strip() for line in (COMPONENT / "ocpp_shadow.py").read_text().splitlines()
           if pattern.search(line) and not re.search(r"id_tag_(ref|changes)", line)
           and not line.strip().startswith("#")]
    assert raw == ['LIFECYCLE_INPUTS = {"id_tag": "id_tag", "soc": "soc"}'], raw
    # Within the lifecycle module idTag only reaches untrusted metadata.
    source = (COMPONENT / "ocpp_lifecycle.py").read_text()
    assert "id_tag" not in source.split("class SessionIdentity")[1].split("def key")[0]


FINANCIAL = ("api.py", "audit.py", "auto_credit.py", "budget.py", "collection.py",
             "collection_recovery.py", "confirmation.py", "confirmation_scheduler.py",
             "coordinator.py", "credit_receipt_link.py", "credit_recovery.py", "embedded.py",
             "energy_adjustment.py", "enrolment.py", "fees.py", "ledger.py",
             "ledger_checkpoint.py", "linked_credit.py", "mainnet.py", "monthly_allowance.py",
             "monthly_authority.py", "monthly_consent.py", "monthly_ownership.py",
             "monthly_portal.py", "monthly_projection.py", "monthly_recorder.py",
             "ongoing_credit.py", "owned_waiver.py", "portal.py", "proxy.py", "proxy_ledger.py",
             "receipt_ack.py", "session_closure.py", "session_review.py", "weekly.py",
             "driver_http.py", "driver_live.py", "recorder.py", "recorder_reconciliation.py")


def test_only_the_shadow_observer_imports_lifecycle_and_nothing_financial_consumes_it():
    imports = re.compile(r"(from|import)\s+(\.|custom_components\.bsv_settlement\.)?"
                         r"(ocpp_lifecycle|ocpp_replay)\b")
    users = sorted(p.name for p in COMPONENT.glob("*.py")
                   if p.name not in ("ocpp_lifecycle.py", "ocpp_replay.py")
                   and imports.search(p.read_text()))
    assert users == ["ocpp_shadow.py"]
    assert not re.search(r"ocpp_replay", (COMPONENT / "ocpp_shadow.py").read_text())
    # No settlement, payment, budget, collection or recorder module reads lifecycle output.
    mention = re.compile(r"ocpp_lifecycle|ocpp_replay|LifecycleTracker|ownership_key|"
                         r"lifecycle_(sessions|summary|view|store)|session_lifecycle|"
                         r"[\[(]\s*[\"']lifecycle[\"']")
    present = {p.name for p in COMPONENT.glob("*.py")}
    assert set(FINANCIAL) <= present, sorted(set(FINANCIAL) - present)
    assert [name for name in FINANCIAL if mention.search((COMPONENT / name).read_text())] == []
    assert ocpp_lifecycle.SETTLEMENT_OWNER == "legacy_sigen"


# --- Report ---------------------------------------------------------------

def test_committed_report_is_reproducible():
    report = _module("ocpp_shadow_report")
    text = report.render(report.run(sorted(FIXTURES / name for name in LIVE)), REPORT_TITLE)
    assert text == REPORT.read_text()
    assert "Billing eligible sessions: 0; automatic vehicle attributions: 0." in text
