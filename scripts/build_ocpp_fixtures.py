"""Build a sanitised OCPP lifecycle replay fixture from a read-only HA history capture.

Input is the JSON produced by Home Assistant ``ha_get_history`` with
``minimal_response: false`` (``{entity_id: [state rows]}``), captured read-only.
Output is a fixture for ``custom_components/bsv_settlement/ocpp_replay.py``.

Sanitisation (deterministic):

- idTags are replaced with ``IDTAG-nn`` placeholders in order of first
  appearance. Raw idTags never reach the fixture.
- Entity IDs are replaced with logical keys (``status``, ``import_register`` ...).
- Attributes are reduced to an allow-list; reference/divergence diagnostics,
  friendly names, serials and anything else are dropped.
- Timestamps and values are kept unchanged. No wallet, payment or site data is read.

Synthetic edge cases are generated deterministically with ``--synthetic DIR``.
They are labelled ``evidence: synthetic`` and are never physical evidence.

Usage::

    python scripts/build_ocpp_fixtures.py capture.json fixture.json \
        --name live-... --start 2026-10-05T12:00:00+10:00 --end ... --description "..."
    python scripts/build_ocpp_fixtures.py --synthetic tests/fixtures/ocpp_lifecycle
"""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
import json
from pathlib import Path

# Entity-ID suffix -> logical key. Order matters: longer, more specific first.
KEYS = (
    ("charger_status_connector", "status"),
    ("charger_transaction_id", "transaction"),
    ("charger_id_tag", "id_tag"),
    ("charger_energy_active_import_register", "import_register"),
    ("charger_energy_active_export_register", "export_register"),
    ("charger_energy_session_export", "session_export"),
    ("charger_power_active_import", "power_import"),
    ("charger_power_active_export", "power_export"),
    ("charger_soc", "soc"),
    ("charger_flow_direction", "flow"),
    ("dc_charger_total_charging_capacity", "ref_import"),
    ("dc_charger_total_discharging_capacity", "ref_export"),
    ("dc_charger_current_discharging_capacity_single_time", "ref_session_export"),
)
ATTRIBUTES = {
    "import_register": ("context", "context_source"),
    "export_register": ("source", "estimated", "energy_lower_bound_kwh",
                        "energy_upper_bound_kwh", "step_intervals", "max_sample_gap_s",
                        "last_sample_timestamp"),
    "session_export": ("source", "estimated"),
    "power_import": ("context", "context_source"),
    "power_export": ("source", "estimated"),
    "soc": ("context_source",),
    "flow": ("deadband_kw",),
}
# Charger provenance as recorded live by the fork's metadata sensors (#61, #69).
# Serial numbers are deliberately absent.
METADATA = {
    "version": {"state": "1.6", "attributes": {"subprotocol": "ocpp1.6", "transport": "ws"}},
    "configuration": {"state": "6", "attributes": {
        "MeterValueSampleInterval": "60", "SupportedFeatureProfiles": "Core",
        "measurands_configurable": False}},
    "boot": {"state": "SIGEN", "attributes": {
        "charge_point_vendor": "SIGEN", "charge_point_model": "EVDC 25 7.5S2",
        "firmware_version": "V100R001C21SPC117"}},
}
UNAVAILABLE = ("unavailable", "unknown")


def key_for(entity_id):
    for suffix, key in KEYS:
        if entity_id.endswith(suffix):
            return key
    return None


def when(row):
    return datetime.fromisoformat(row["last_updated"])


def build(capture, name, start, end, description, evidence="physical_live_sanitised"):
    start, end = datetime.fromisoformat(start), datetime.fromisoformat(end)
    tags, units, events, initial = {}, {}, [], {}
    for entity_id, rows in capture.items():
        key = key_for(entity_id)
        if key is None:
            continue
        rows = sorted(rows, key=when)
        before = [r for r in rows if when(r) < start]
        inside = [r for r in rows if start <= when(r) <= end]
        if before and not any(when(r) == start for r in inside):
            inside.insert(0, {**before[-1], "last_updated": start.isoformat()})
            initial[key] = True
        for row in inside:
            attributes = row.get("attributes") or {}
            unit = attributes.get("unit_of_measurement")
            if unit is not None:
                if units.setdefault(key, unit) != unit:
                    raise ValueError(f"{key} changed unit; review before building")
            value = row["state"]
            if key == "id_tag" and value not in ("", *UNAVAILABLE):
                value = tags.setdefault(value, f"IDTAG-{len(tags) + 1:02d}")
            kept = {k: attributes[k] for k in ATTRIBUTES.get(key, ()) if k in attributes}
            event = [when(row).isoformat(), key, value]
            if kept:
                event.append(kept)
            events.append(event)
    order = {key: index for index, (_, key) in enumerate(KEYS)}
    events.sort(key=lambda e: (datetime.fromisoformat(e[0]), order[e[1]]))
    return {
        "schema": 1, "fixture": name, "evidence": evidence, "description": description,
        "charger": "charger-1", "connector": 1,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "sanitisation": {
            "id_tags": "replaced with IDTAG-nn placeholders in order of first appearance",
            "entities": "entity IDs replaced with logical keys",
            "attributes": "allow-listed; reference/divergence diagnostics and serials dropped",
            "kept": "HA last_updated timestamps, states and OCPP transaction IDs",
            "initial_state_keys": sorted(initial),
        },
        "units": dict(sorted(units.items())),
        "metadata": METADATA,
        "events": events,
    }


def dump(fixture, path):
    text = json.dumps(fixture, separators=(",", ":"))
    # One event per line keeps diffs reviewable.
    text = text.replace(',"events":[[', ',"events":[\n[').replace("],[", "],\n[")
    Path(path).write_text(text + "\n")


# --- Synthetic edge cases ---------------------------------------------------
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
MWH = Decimal("0.00001")  # Sigen counters: MWh with 5 decimals (10 Wh)


def _q(value, places="0.000001"):
    return str(Decimal(value).quantize(Decimal(places), rounding=ROUND_HALF_UP).normalize())


class Scenario:
    """Deterministic charger + reference timeline from a power profile.

    ``power(t)`` is the true EV power in kW (positive import, negative export),
    held between 5 s reference steps. Meter samples every 60 s carry the
    charger's import register (kWh) and the fork-style derived export register
    (trapezoid of sampled export power, with min/max bounds).
    """

    def __init__(self, name, description, power):
        self.name, self.description, self.power = name, description, power
        self.events = []
        self.true_import = self.true_export = Decimal(0)
        self.ref = {"ref_import": Decimal("15.80000"), "ref_export": Decimal("11.10000")}
        self.register = Decimal("15800.000")
        self.export = {"value": Decimal("80"), "lower": Decimal("78"), "upper": Decimal("82"),
                       "steps": 100, "last": None}
        self.session_export = Decimal(0)
        self.offset = Decimal(0)  # register-only energy, e.g. a late final reading

    def at(self, second):
        return (BASE + timedelta(seconds=second)).isoformat()

    def add(self, second, key, value, attributes=None):
        event = [self.at(second), key, value]
        if attributes:
            event.append(attributes)
        self.events.append(event)

    def lifecycle(self, second, status=None, tx=None, tag=None):
        if tag is not None:
            self.add(second, "id_tag", tag)
        if tx is not None:
            self.add(second, "transaction", tx)
        if status is not None:
            self.add(second, "status", status)

    def run_reference(self, start, end, step=5):
        """True energy at 5 s steps; reference counters emitted when they change."""
        for second in range(start, end, step):
            kw = Decimal(str(self.power(second)))
            kwh = abs(kw) * step / 3600
            if kw > 0:
                self.true_import += kwh
            elif kw < 0:
                self.true_export += kwh
            for key, total in (("ref_import", self.true_import), ("ref_export", self.true_export)):
                value = (Decimal("15.80000") if key == "ref_import" else Decimal("11.10000")) + (
                    total / 1000).quantize(MWH, rounding="ROUND_FLOOR")
                if value != self.ref[key]:
                    self.ref[key] = value
                    self.add(second + step, key, str(value))

    def meter(self, second, last_true_import, session_export=True, flow_at=None):
        """One MeterValues arrival: import register, power, flow and derived export."""
        kw = Decimal(str(self.power(second)))
        self.add(second, "power_import", _q(max(kw, 0), "0.001"),
                 {"context": "Sample.Periodic", "context_source": "defaulted"})
        export_kw = max(-kw, Decimal(0))
        e = self.export
        if e["last"] is not None:
            hours = Decimal(second - e["last"][0]) / 3600
            e["value"] += (e["last"][1] + export_kw) / 2 * hours
            e["lower"] += min(e["last"][1], export_kw) * hours
            e["upper"] += max(e["last"][1], export_kw) * hours
            self.session_export += (e["last"][1] + export_kw) / 2 * hours
            if e["last"][1] != export_kw:
                e["steps"] += 1
        e["last"] = (second, export_kw)
        self.add(second, "export_register", _q(e["value"]), {
            "source": "derived_from_negative_import", "estimated": True,
            "energy_lower_bound_kwh": float(_q(e["lower"])),
            "energy_upper_bound_kwh": float(_q(e["upper"])),
            "step_intervals": e["steps"], "max_sample_gap_s": 180.0,
            "last_sample_timestamp": self.at(second - 1)})
        if session_export:
            self.add(second, "session_export", _q(self.session_export),
                     {"source": "derived_from_negative_import", "estimated": True})
        flow = "export" if kw < Decimal("-0.1") else "import" if kw > Decimal("0.1") else "idle"
        self.add(flow_at if flow_at is not None else second, "flow", flow)
        register = Decimal("15800.000") + last_true_import + self.offset
        if register != self.register:
            self.register = register
            self.add(second, "import_register", _q(register, "0.001"),
                     {"context": "Sample.Periodic", "context_source": "defaulted"})

    def fixture(self, end):
        order = {key: index for index, (_, key) in enumerate(KEYS)}
        self.events.sort(key=lambda e: (e[0], order[e[1]]))
        return {
            "schema": 1, "fixture": self.name, "evidence": "synthetic",
            "description": self.description, "charger": "charger-1", "connector": 1,
            "window": {"start": self.at(0), "end": self.at(end)},
            "sanitisation": {"id_tags": "synthetic placeholders", "kept": "synthetic data only",
                             "initial_state_keys": []},
            "units": {"export_register": "kWh", "import_register": "kWh", "power_export": "kW",
                      "power_import": "kW", "ref_export": "MWh", "ref_import": "MWh",
                      "session_export": "kWh", "soc": "%"},
            "metadata": METADATA, "events": self.events,
        }


def _initial(scenario):
    scenario.add(0, "status", "Available")
    scenario.add(0, "transaction", "0")
    scenario.add(0, "id_tag", "")
    scenario.add(0, "import_register", "15800.000",
                 {"context": "Sample.Periodic", "context_source": "defaulted"})
    scenario.add(0, "ref_import", "15.80000")
    scenario.add(0, "ref_export", "11.10000")


def _charge(scenario, start, end, meters, gap=None, restart=None, tag="IDTAG-01",
            tx="1001", soc=(40, 60)):
    """Preparing at ``start``, Charging +30 s, meters every 60 s, Finishing at ``end``."""
    scenario.lifecycle(start, status="Preparing", tx=tx, tag=tag)
    scenario.lifecycle(start + 30, status="Charging")
    scenario.add(start + 31, "soc", str(soc[0]))
    for index, second in enumerate(range(start + 31, end, 60)):
        if gap and gap[0] <= second < gap[1]:
            continue
        if restart and restart[0] <= second < restart[1]:
            continue
        true_import = sum((Decimal(str(scenario.power(t))) * 5 / 3600
                           for t in range(0, second, 5) if scenario.power(t) > 0), Decimal(0))
        scenario.meter(second, true_import.quantize(Decimal("0.001"), rounding="ROUND_FLOOR"))
    scenario.add(end - 1, "soc", str(soc[1]))
    scenario.lifecycle(end, status="Finishing")
    scenario.lifecycle(end + 3, tx="0", tag="")


def synthetic():
    out = []
    # 1. Plain start/stop import at 7.2 kW.
    s = Scenario("synthetic-start-stop-import",
                 "Remote-start style session: 30 min at 7.2 kW import, Finishing then StopTransaction.",
                 lambda t: 7.2 if 30 <= t < 1830 else 0)
    _initial(s)
    s.run_reference(0, 2100)
    _charge(s, 0, 1830, None)
    s.lifecycle(1840, status="Available")
    out.append(s.fixture(2100))

    # 2. HA restart mid-session: entities unavailable, then restored without context.
    s = Scenario("synthetic-ha-restart-mid-session",
                 "45 min at 7.2 kW with an HA restart at 900-930 s; entities return first "
                 "without context (restored), then with charger context.",
                 lambda t: 7.2 if 30 <= t < 2730 else 0)
    _initial(s)
    s.run_reference(0, 3000)
    _charge(s, 0, 2730, None, restart=(900, 960))
    for key in ("import_register", "export_register", "flow", "id_tag", "status", "transaction"):
        s.add(900, key, "unavailable")
    s.add(930, "id_tag", "IDTAG-01")
    s.add(930, "status", "Charging")
    s.add(930, "transaction", "1001")
    s.add(930, "import_register", "15801.740")  # restored display state, no context
    s.lifecycle(2740, status="Available")
    out.append(s.fixture(3000))

    # 3. Late and duplicate StopTransaction, late final meter, then a new session.
    s = Scenario("synthetic-late-duplicate-stop",
                 "Session 2001 stops; the closed transaction ID is re-published 4 s later "
                 "(duplicate/late StopTransaction) and the import register advances 0.05 kWh "
                 "after the stop. A new session 2002 follows.",
                 lambda t: 7.2 if 30 <= t < 1230 or 1830 <= t < 2430 else 0)
    _initial(s)
    s.run_reference(0, 2700)
    _charge(s, 0, 1230, None, tx="2001")
    s.add(1237, "transaction", "2001")
    s.add(1238, "transaction", "0")
    s.add(1245, "import_register", _q(s.register + Decimal("0.05"), "0.001"),
          {"context": "Sample.Periodic", "context_source": "defaulted"})
    s.register += Decimal("0.05")
    s.offset += Decimal("0.05")
    s.lifecycle(1250, status="Available")
    _charge(s, 1800, 2430, None, tx="2002", tag="IDTAG-02")
    s.lifecycle(2440, status="Available")
    out.append(s.fixture(2700))

    # 4. Back-to-back V2G sessions exporting continuously across the boundary.
    s = Scenario("synthetic-back-to-back-export",
                 "Two V2G sessions (3001, 3002) at 2 kW export with a 40 s Finishing/"
                 "Available/Preparing boundary; the derived register keeps integrating "
                 "across the boundary, which must not be bridged into either session.",
                 lambda t: -2.0 if 30 <= t < 3700 else 0)
    _initial(s)
    s.run_reference(0, 3900)
    _charge(s, 0, 1800, None, tx="3001", soc=(60, 59))
    s.lifecycle(1806, status="Available")
    _charge(s, 1840, 3600, None, tx="3002", tag="IDTAG-01", soc=(59, 58))
    s.lifecycle(3610, status="Available")
    out.append(s.fixture(3900))

    # 5. Charger fault, zero-energy transaction.
    s = Scenario("synthetic-fault-zero-energy",
                 "Transaction 4001 starts, never reaches Charging, Finishing, cleared, Faulted.",
                 lambda t: 0)
    _initial(s)
    s.lifecycle(60, status="Preparing", tx="4001", tag="IDTAG-01")
    s.lifecycle(92, status="Finishing")
    s.lifecycle(95, tx="0", tag="")
    s.lifecycle(95, status="Faulted")
    out.append(s.fixture(600))

    # 6. Meter gap: MeterValues missing for 10 minutes while the session continues.
    s = Scenario("synthetic-meter-gap",
                 "40 min at 7.2 kW; no MeterValues between 900 and 1500 s while the "
                 "transaction stays Charging. The gap is excluded, never zero-filled.",
                 lambda t: 7.2 if 30 <= t < 2430 else 0)
    _initial(s)
    s.run_reference(0, 2700)
    _charge(s, 0, 2430, None, gap=(900, 1500))
    s.lifecycle(2440, status="Available")
    out.append(s.fixture(2700))
    return out


def v2g_negative_import():
    """4 Oct live V2G MeterValues (#61) replayed as negative Power.Active.Import.

    Charger samples (signed import power, import register in Wh) are the live
    values; the export register is derived like the fork, and the reference
    discharge counter is SYNTHESISED by distributing the inverter's observed
    11.06370 -> 11.06495 MWh change in proportion to the sampled power.
    """
    rows = [(0, 17.234, 15772510), (60, 16.527, 15772790), (121, -0.249, 15772970),
            (181, -0.269, 15772970), (241, -0.359, 15772970), (301, -0.229, 15772970),
            (361, -7.048, 15772970), (421, -7.269, 15772970), (481, -7.267, 15772970),
            (541, -7.332, 15772970), (601, -7.601, 15772970), (661, -18.819, 15772970),
            (721, -21.839, 15772970), (781, -0.007, 15772970)]
    s = Scenario("synthetic-v2g-negative-import",
                 "Live 4 Oct stepped V2G MeterValues as negative Power.Active.Import (Wh "
                 "import register flat during discharge). Reference discharge counter is "
                 "synthesised from the inverter's 1.250 kWh change; not physical evidence.",
                 lambda t: 0)
    s.add(0, "status", "Charging")
    s.add(0, "transaction", "5001")
    s.add(0, "id_tag", "IDTAG-01")
    s.add(0, "ref_import", "15.77251")
    s.add(60, "ref_import", "15.77279")
    s.add(121, "ref_import", "15.77297")
    s.add(0, "ref_export", "11.06370")
    weights = []
    for (t0, p0, _), (t1, p1, _) in zip(rows, rows[1:]):
        weights.append((t0, t1, max(-p0, 0) + max(-p1, 0)))
    total = sum(w for _, _, w in weights)
    counter, emitted = Decimal("11.06370"), Decimal("11.06370")
    for t0, t1, weight in weights:
        share = Decimal("0.00125") * Decimal(str(weight)) / Decimal(str(total))
        steps = (t1 - t0) // 5
        for i in range(1, steps + 1):
            counter += share / steps
            value = counter.quantize(MWH, rounding="ROUND_HALF_UP")
            if value != emitted:
                emitted = value
                s.add(t0 + i * 5, "ref_export", str(value))
    last_register = None
    for second, kw, wh in rows:
        kw = Decimal(str(kw))
        s.add(second, "power_import", str(kw), {"context": "Sample.Periodic",
                                                "context_source": "defaulted"})
        e, export_kw = s.export, max(-kw, Decimal(0))
        if e["last"] is not None:
            hours = Decimal(second - e["last"][0]) / 3600
            e["value"] += (e["last"][1] + export_kw) / 2 * hours
            e["lower"] += min(e["last"][1], export_kw) * hours
            e["upper"] += max(e["last"][1], export_kw) * hours
            e["steps"] += 1
        e["last"] = (second, export_kw)
        s.add(second, "export_register", _q(e["value"]), {
            "source": "derived_from_negative_import", "estimated": True,
            "energy_lower_bound_kwh": float(_q(e["lower"])),
            "energy_upper_bound_kwh": float(_q(e["upper"])),
            "step_intervals": e["steps"], "max_sample_gap_s": 180.0,
            "last_sample_timestamp": s.at(second - 1)})
        flow = "export" if kw < Decimal("-0.1") else "import" if kw > Decimal("0.1") else "idle"
        s.add(second, "flow", flow)
        if wh != last_register:
            last_register = wh
            s.add(second, "import_register", str(wh),
                  {"context": "Sample.Periodic", "context_source": "defaulted"})
    s.lifecycle(800, status="Finishing")
    s.lifecycle(803, tx="0", tag="")
    fixture = s.fixture(1000)
    fixture["units"]["import_register"] = "Wh"
    return fixture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", nargs="?")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--name")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--description")
    parser.add_argument("--synthetic", metavar="DIR")
    args = parser.parse_args()
    if args.synthetic:
        for fixture in [*synthetic(), v2g_negative_import()]:
            dump(fixture, Path(args.synthetic) / (fixture["fixture"].replace("-", "_") + ".json"))
        return
    if not all((args.capture, args.output, args.name, args.start, args.end, args.description)):
        parser.error("capture, output, --name, --start, --end and --description are required")
    capture = json.loads(Path(args.capture).read_text())
    dump(build(capture, args.name, args.start, args.end, args.description), args.output)


if __name__ == "__main__":
    main()
