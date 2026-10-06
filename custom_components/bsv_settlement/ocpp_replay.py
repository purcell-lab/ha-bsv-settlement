"""Deterministic offline replay of OCPP lifecycle fixtures through the shadow code.

Pure and offline: no HA instance, services, wallet or charger. A fixture is a
time-ordered list of entity state changes (HA ``last_updated`` arrival times),
captured read-only from Home Assistant history and sanitised, or synthetic.

The replay mirrors the live ``OCPPShadowCoordinator``:

- the import and export shadow ledgers observe on every change of a watched
  entity and on a 15 s poll, with the same snapshot shapes;
- an HA restart (connector status and transaction both unavailable) unloads
  the observer, and when the entities return it is rebuilt from the persisted
  store, exactly as after a real restart;
- every closed span is reconciled against the reference Sigen counters with
  ``recorder_reconciliation.reconcile_span``;
- ``ocpp_lifecycle`` builds native sessions and assembles each session's
  observed import/export and reference comparison.

Nothing here changes which recorder settles or produces a billable record.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path

from .ocpp_export_shadow_ledger import ExportShadowLedger
from .ocpp_lifecycle import LifecycleTracker, as_datetime_rows, assemble, overlapping
from .ocpp_shadow_ledger import ImportShadowLedger, instant, provenance_flags
from .recorder_reconciliation import SETTLE_SECONDS, reconcile_span, tolerance_rules

FIXTURE_SCHEMA = 1
POLL_SECONDS = 15
EVIDENCE = ("physical_live_sanitised", "synthetic")
KEYS = ("status", "transaction", "id_tag", "import_register", "export_register",
        "session_export", "power_import", "power_export", "soc", "flow",
        "ref_import", "ref_export", "ref_session_export")
WATCHED = ("status", "transaction", "import_register", "export_register", "flow",
           "session_export", "ref_export")
LIFECYCLE = ("status", "transaction", "id_tag", "soc")
UNITS = {"Wh": Decimal("0.001"), "kWh": Decimal(1), "MWh": Decimal(1000)}
BINDING = {"fixture": "ocpp-lifecycle-replay"}
EXPORT_BINDING = {"register": {"unique_id": "fixture.energy_active_export_register"}}
REFERENCE_BINDING = {"unique_id": "fixture.reference_discharge", "platform": "fixture"}
LEGACY_ENTRY = "fixture-legacy-sigen"
UNAVAILABLE = ("unavailable", "unknown")


def load(path):
    """Read and validate a fixture; raises ValueError for anything malformed."""
    fixture = json.loads(Path(path).read_text())
    if (not isinstance(fixture, dict) or fixture.get("schema") != FIXTURE_SCHEMA
            or fixture.get("evidence") not in EVIDENCE
            or not isinstance(fixture.get("events"), list) or not fixture["events"]
            or not isinstance(fixture.get("units"), dict)):
        raise ValueError(f"Invalid OCPP lifecycle fixture: {path}")
    previous = None
    for event in fixture["events"]:
        if (not isinstance(event, list) or len(event) not in (3, 4)
                or event[1] not in KEYS or not isinstance(event[2], str)):
            raise ValueError(f"Invalid fixture event: {event!r}")
        at = instant(event[0])
        if previous is not None and at < previous:
            raise ValueError("Fixture events must be in time order")
        previous = at
    for key, unit in fixture["units"].items():
        if key in ("import_register", "export_register", "session_export", "ref_import",
                   "ref_export", "ref_session_export") and unit not in UNITS:
            raise ValueError(f"Unsupported unit for {key}: {unit}")
    return fixture


class Replay:
    """One fixture through the import/export shadow, reconciliation and lifecycle."""

    def __init__(self, fixture, tolerances=None, grading=None, reference=True):
        self.fixture = fixture
        self.units = fixture["units"]
        self.tolerances = tolerance_rules(tolerances)
        self.grading = grading
        self.with_reference = reference and "ref_export" in self.units
        self.state = {}
        self.history = {key: [] for key in KEYS}
        self.tracker = LifecycleTracker(fixture.get("charger", "charger-1"),
                                        int(fixture.get("connector", 1)))
        self.import_spans, self.export_spans = {}, {}
        self.restarts = []
        self.down = None
        self.saved = None
        self.observations = 0
        self.ledger = self.export = None
        self._start_observer()

    # Observer lifecycle -------------------------------------------------
    def _start_observer(self):
        saved = deepcopy(self.saved)
        self.ledger = ImportShadowLedger(BINDING, saved)
        self.export = ExportShadowLedger(
            None if saved is None else saved["export"], EXPORT_BINDING,
            REFERENCE_BINDING if self.with_reference else None, self.grading)
        self.ledger.data["export"] = self.export.data

    def _stop_observer(self):
        self.saved = deepcopy(self.ledger.data)
        self.ledger = self.export = None

    # Snapshots (same shapes as OCPPShadowCoordinator) -------------------
    def _row(self, key):
        return self.state.get(key)

    def _snapshot(self):
        snapshot = {}
        for name, key in (("status", "status"), ("transaction", "transaction"),
                          ("import", "import_register")):
            row = self._row(key)
            if row is not None:
                attrs = row["attributes"]
                snapshot[name] = {"value": row["value"], "unit": self.units.get(key),
                                  "context": attrs.get("context"),
                                  "context_source": attrs.get("context_source"),
                                  "restored": False, "ha_updated_at": row["at"]}
        snapshot["metadata"] = deepcopy(self.fixture.get("metadata") or {})
        return snapshot

    def _export_snapshot(self, snapshot):
        result = {"status": snapshot.get("status", {}), "transaction": snapshot.get("transaction", {}),
                  "provenance_flags": sorted(provenance_flags(self.ledger.provenance))}
        for name, key in (("export", "export_register"), ("flow", "flow"),
                          ("session_export", "session_export")):
            row = self._row(key)
            if row is not None:
                entry = {"value": row["value"], "unit": self.units.get(key),
                         "ha_updated_at": row["at"], "restored": False}
                if name == "export":
                    entry["attributes"] = dict(row["attributes"])
                result[name] = entry
        if self.with_reference:
            row = self._row("ref_export")
            kwh = None
            if row is not None and row["value"] not in UNAVAILABLE:
                try:
                    kwh = str(Decimal(row["value"]) * UNITS[self.units["ref_export"]])
                except ArithmeticError:
                    kwh = None
            result["reference"] = {"kwh": kwh}
        return result

    def _observe(self, at):
        if self.ledger is None:
            return
        snapshot = self._snapshot()
        self.ledger.observe(snapshot, at)
        self.export.observe(self._export_snapshot(snapshot), at)
        self.observations += 1
        self._collect()

    def _collect(self):
        # Ledgers retain only the last 50 spans; collect each as it closes.
        for store, spans in ((self.import_spans, self.ledger.data["spans"]),
                             (self.export_spans, self.export.data["spans"])):
            for span in spans[-3:]:
                store.setdefault(span["span_id"], deepcopy(span))

    # Replay -------------------------------------------------------------
    def _poll_until(self, clock, until):
        while clock is not None and clock + timedelta(seconds=POLL_SECONDS) < until:
            clock += timedelta(seconds=POLL_SECONDS)
            self._observe(clock.isoformat())
        return clock

    def _restart_transition(self, key, value, at):
        if key not in ("status", "transaction"):
            return
        others = [self.state.get(k, {}).get("value") for k in ("status", "transaction")]
        if self.down is None and value in UNAVAILABLE and all(
                v in UNAVAILABLE for v in others):
            self.down = at
            self._stop_observer()
        elif self.down is not None and value not in UNAVAILABLE and all(
                v not in UNAVAILABLE for v in others):
            self.restarts.append({"from": self.down, "to": at})
            self.down = None
            self._start_observer()

    def run(self):
        clock = None
        for event in self.fixture["events"]:
            at, key, value = event[0], event[1], event[2]
            attributes = event[3] if len(event) == 4 else {}
            moment = instant(at)
            clock = self._poll_until(clock, moment)
            self.state[key] = {"value": value, "attributes": attributes, "at": at}
            self.history[key].append((at, value))
            if key in LIFECYCLE:
                self.tracker.update(key, value, at)
            self._restart_transition(key, value, at)
            if key in WATCHED:
                self._observe(at)
                clock = moment
            elif clock is None:
                clock = moment
        end = instant(self.fixture["window"]["end"]) if self.fixture.get("window") else clock
        self._poll_until(clock, end + timedelta(seconds=POLL_SECONDS))
        return self.result(end.isoformat())

    def references(self, window_end=None):
        """Change-only reference rows, plus the last value held at the window end.

        HA history records every change, so the last reading is still the
        counter's value at the window end (the replay analogue of the live
        reconciliation counting the current legacy state as a reading).
        """
        out = {}
        for direction, key in (("import", "ref_import"), ("export", "ref_export")):
            if key in self.units and self.history[key]:
                rows = as_datetime_rows(self.history[key], UNITS[self.units[key]])
                if rows and window_end is not None and rows[-1][0] < instant(window_end):
                    rows.append((instant(window_end), rows[-1][1]))
                out[direction] = rows
        return out

    def result(self, window_end):
        references = self.references(window_end)
        results = []
        for direction, spans in (("import", self.import_spans), ("export", self.export_spans)):
            for span in spans.values():
                ended = instant(span["observation_ended_at"])
                now = (ended + timedelta(seconds=SETTLE_SECONDS)).isoformat()
                results.append(reconcile_span(
                    span, direction, references.get(direction), self.tolerances,
                    LEGACY_ENTRY, now))
        sessions = self.tracker.finish(window_end)
        register = (as_datetime_rows(self.history["import_register"],
                                     UNITS[self.units["import_register"]])
                    if "import_register" in self.units else None)
        current = [s for s in (self.ledger.data["current"] if self.ledger else None,
                               self.export.data["current"] if self.export else None) if s]
        assembled = assemble(
            sessions, [*self.import_spans.values(),
                       *[s for s in current if "observed_import_kwh" in s]],
            [*self.export_spans.values(), *[s for s in current if "estimate_kwh" in s]],
            results, references, self.tolerances, window_end, register)
        return {
            "fixture": self.fixture["fixture"], "evidence": self.fixture["evidence"],
            "description": self.fixture.get("description"),
            "window": self.fixture.get("window"), "ha_restarts": self.restarts,
            "observations": self.observations, "sessions": assembled,
            "import_spans": list(self.import_spans.values()),
            "export_spans": list(self.export_spans.values()),
            "reconciliations": results, "overlapping_sessions": overlapping(sessions),
            "tolerances": self.tolerances,
            "billing_eligible": False, "settlement_owner": "legacy_sigen",
        }


def replay(path_or_fixture, **options):
    fixture = load(path_or_fixture) if isinstance(path_or_fixture, (str, Path)) else path_or_fixture
    return Replay(fixture, **options).run()
