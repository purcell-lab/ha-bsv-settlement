"""Aligned-window comparison of closed OCPP shadow spans with the legacy recorder.

Pure and read-only: no HA imports, tariffs, amounts, wallet or charger calls.
Both sides are compared over the SAME Home Assistant time window, the OCPP
span's first and last accepted meter sample (HA arrival times). The legacy
Sigenergy counter is bracketed at each window edge by its nearest recorded
readings, interpolated linearly for a point estimate and bounded by the
bracketing readings plus half its 10 Wh resolution. One source missing,
misaligned or too coarse is unresolved, never "ok".
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, DecimalException
import hashlib
import json

from .ocpp_shadow_ledger import energy, instant

SCHEMA = 1
MAX_RESULTS = 50
DIRECTIONS = ("import", "export")
SETTLEMENT_OWNER = "legacy_sigen"
# Sigen cumulative counters are MWh with five decimals (10 Wh steps), ~5 s cadence.
LEGACY_RESOLUTION_KWH = Decimal("0.01")
LEGACY_CADENCE_SECONDS = 5
# The legacy recorder keeps only value changes. Sigen reports every ~5 s, so a
# step no larger than this charger's 25 kW for one cadence (plus resolution)
# happened within one cadence before the row showing it. Larger steps imply
# unobserved reporting and use the whole bracket.
LEGACY_MAX_KW = Decimal(25)
# An edge whose effective bracket is longer than this, while the counter moved
# inside it, cannot pin the window boundary.
MAX_EDGE_BRACKET_SECONDS = 60
# Wait after a span closes so a legacy reading after the window end exists.
SETTLE_SECONDS = 20
# Give up waiting for an unloaded legacy recorder after this long.
PENDING_LIMIT_SECONDS = 900
COMPLETE_END_REASONS = ("identity_or_lifecycle_boundary",)
OUTCOMES = ("within_tolerance", "outside_tolerance", "unresolved")
EXPLANATIONS = {
    "aligned": "within_tolerance",
    "ocpp_bounds_contain_legacy": None,  # within only when the export grade is within tolerance
    "difference_exceeds_tolerance": "outside_tolerance",
    "ocpp_missing": "unresolved",
    "ocpp_span_partial": "unresolved",
    "legacy_missing": "unresolved",
    "legacy_counter_invalid": "unresolved",
    "misaligned_window": "unresolved",
    "insufficient_energy": "unresolved",
    "legacy_resolution_limited": "unresolved",
}
DEFAULT_TOLERANCES = {"import_pct": "1", "import_floor_kwh": "0.02", "export_pct": "5",
                      "export_floor_kwh": "0.02", "min_energy_kwh": "0.1"}
PLACES = Decimal("0.000001")


def _decimal(value):
    if isinstance(value, bool) or value is None or len(str(value)) > 40:
        raise ValueError("Invalid number")
    try:
        result = Decimal(str(value))
    except DecimalException:
        raise ValueError("Invalid number") from None
    if not result.is_finite():
        raise ValueError("Invalid number")
    return result


def _text(value):
    return f"{value.quantize(PLACES).normalize():f}" if value is not None else None


def tolerance_rules(values=None):
    """Validated tolerances as Decimal strings; raises ValueError when unusable."""
    merged = {**DEFAULT_TOLERANCES, **{k: v for k, v in (values or {}).items() if v is not None}}
    if set(merged) != set(DEFAULT_TOLERANCES):
        raise ValueError("Unknown reconciliation tolerance")
    parsed = {k: _decimal(v) for k, v in merged.items()}
    if not (all(0 < parsed[k] <= 100 for k in ("import_pct", "export_pct"))
            and all(0 <= parsed[k] <= 10 for k in ("import_floor_kwh", "export_floor_kwh"))
            and Decimal("0.001") <= parsed["min_energy_kwh"] <= 100):
        raise ValueError("Reconciliation tolerances out of range")
    return {k: f"{v.normalize():f}" for k, v in parsed.items()}


def _seconds(delta):
    return Decimal(str(delta.total_seconds()))


def _bracket(readings, at):
    """Nearest valid readings at-or-before and strictly after ``at``."""
    before = after = None
    for stamp, value in readings:
        if value is None:
            continue
        if stamp <= at:
            before = (stamp, value)
        else:
            after = (stamp, value)
            break
    return before, after


def _edge_start(before, after, resolution):
    """When the counter is known to have still read ``before`` (see LEGACY_MAX_KW)."""
    step = after[1] - before[1]
    if step <= LEGACY_MAX_KW * LEGACY_CADENCE_SECONDS / 3600 + resolution:
        return max(before[0], after[0] - timedelta(seconds=LEGACY_CADENCE_SECONDS))
    return before[0]


def _interpolate(before, after, at, resolution):
    if after[1] == before[1]:
        return before[1]
    start = _edge_start(before, after, resolution)
    if at <= start:
        return before[1]
    return before[1] + (after[1] - before[1]) * _seconds(at - start) / _seconds(
        after[0] - start)


def legacy_window(readings, start, end, resolution=LEGACY_RESOLUTION_KWH):
    """Legacy register delta over [start, end] from sorted (datetime, kWh|None) rows.

    Returns status ``ok`` with estimate/lower/upper/edge gaps, or
    ``legacy_missing`` (no bracketing reading) / ``legacy_counter_invalid``
    (invalid or decreasing reading inside the bracketed span).
    """
    if not readings:
        return {"status": "legacy_missing"}
    b0, a0 = _bracket(readings, start)
    b1, a1 = _bracket(readings, end)
    if None in (b0, a0, b1, a1):
        return {"status": "legacy_missing"}
    inside = [value for stamp, value in readings if b0[0] <= stamp <= a1[0]]
    if any(v is None for v in inside) or any(y < x for x, y in zip(inside, inside[1:])):
        return {"status": "legacy_counter_invalid"}
    half = resolution / 2
    gaps = [_seconds(a[0] - _edge_start(b, a, resolution)) if a[1] != b[1] else
            _seconds(a[0] - b[0]) for b, a in ((b0, a0), (b1, a1))]
    moved = [a0[1] != b0[1], a1[1] != b1[1]]
    return {
        "status": "ok",
        "estimate": (_interpolate(b1, a1, end, resolution)
                     - _interpolate(b0, a0, start, resolution)),
        "lower": max(Decimal(0), (b1[1] - half) - (a0[1] + half)),
        "upper": (a1[1] + half) - (b0[1] - half),
        "edge_gaps_s": gaps,
        "misaligned": any(g > MAX_EDGE_BRACKET_SECONDS and m for g, m in zip(gaps, moved)),
    }


def _ocpp_energy(span, direction):
    key = "observed_import_kwh" if direction == "import" else "estimate_kwh"
    try:
        value = energy(span[key], "kWh")
    except (KeyError, TypeError, ValueError):
        return None, None, None
    lower = upper = None
    if direction == "export":
        try:
            lower, upper = energy(span["lower_kwh"], "kWh"), energy(span["upper_kwh"], "kWh")
        except (KeyError, TypeError, ValueError):
            lower = upper = None
    return value, lower, upper


def reconcile_span(span, direction, readings, tolerances, legacy_entry_id, now,
                   legacy_flags=()):
    """One persisted comparison record for a closed OCPP span; never raises on data."""
    rules = tolerance_rules(tolerances)
    pct = Decimal(rules[direction + "_pct"])
    floor = Decimal(rules[direction + "_floor_kwh"])
    ocpp, lower, upper = _ocpp_energy(span, direction)
    try:
        start = instant(span["first_meter_ha_updated_at"])
        end = instant(span["last_meter_ha_updated_at"])
    except (KeyError, TypeError, ValueError):
        start = end = None
    legacy = (legacy_window(readings, start, end) if readings is not None and start
              else {"status": "legacy_missing"})
    estimate = legacy.get("estimate")
    allowance = max(pct / 100 * estimate, floor) if estimate is not None else None
    difference = ocpp - estimate if ocpp is not None and estimate is not None else None
    partial = (span.get("sample_count", 0) < 2 or start is None or end <= start
               or span.get("end_reason") not in COMPLETE_END_REASONS)
    if ocpp is None or start is None:
        code = "ocpp_missing"
    elif partial:
        code = "ocpp_span_partial"
    elif legacy["status"] != "ok":
        code = legacy["status"]
    elif legacy["misaligned"]:
        code = "misaligned_window"
    elif max(ocpp, estimate) < Decimal(rules["min_energy_kwh"]):
        code = "insufficient_energy"
    elif (legacy["upper"] - legacy["lower"]) / 2 > allowance:
        code = "legacy_resolution_limited"
    elif abs(difference) <= allowance:
        code = "aligned"
    elif lower is not None and upper is not None and lower <= estimate <= upper:
        code = "ocpp_bounds_contain_legacy"
    else:
        code = "difference_exceeds_tolerance"
    outcome = EXPLANATIONS[code] or (
        "within_tolerance" if span.get("grade") == "within_tolerance" else "unresolved")
    identity = json.dumps([span.get("span_id"), direction, legacy_entry_id], sort_keys=True)
    return {
        "comparison_id": "recorder-recon-" + hashlib.sha256(identity.encode()).hexdigest()[:24],
        "span_id": span.get("span_id"), "direction": direction,
        "native_transaction_id": span.get("native_transaction_id"),
        "window_start": start.isoformat() if start else None,
        "window_end": end.isoformat() if end else None,
        "window_s": _text(_seconds(end - start)) if start and end else None,
        "span_end_reason": span.get("end_reason"), "sample_count": span.get("sample_count"),
        "ocpp_kwh": _text(ocpp), "ocpp_lower_kwh": _text(lower), "ocpp_upper_kwh": _text(upper),
        "ocpp_grade": span.get("grade") if direction == "export" else None,
        "legacy_entry_id": legacy_entry_id, "legacy_status": legacy["status"],
        "legacy_kwh": _text(estimate), "legacy_lower_kwh": _text(legacy.get("lower")),
        "legacy_upper_kwh": _text(legacy.get("upper")),
        "legacy_edge_gaps_s": [_text(g) for g in legacy.get("edge_gaps_s", [])] or None,
        "legacy_resolution_kwh": _text(LEGACY_RESOLUTION_KWH),
        "legacy_flags": sorted(legacy_flags),
        "difference_kwh": _text(difference),
        "difference_pct": (str((difference / estimate * 100).quantize(Decimal("0.001")))
                           if difference is not None and estimate else None),
        "allowance_kwh": _text(allowance), "tolerance": dict(rules),
        "within_tolerance": outcome == "within_tolerance", "outcome": outcome,
        "explanation": code, "reconciled_at": now,
        "billing_eligible": False, "settlement_owner": SETTLEMENT_OWNER,
    }


def fresh(started_at):
    instant(started_at)
    return {"schema": SCHEMA, "started_at": started_at,
            "watermarks": {d: {"ended_at": started_at, "span_id": None} for d in DIRECTIONS},
            "results": [], "results_trimmed": False,
            "totals": dict.fromkeys((*OUTCOMES, "skipped_not_linked"), 0)}


def validate(saved):
    """Full structural check of a stored reconciliation section; raises ValueError."""
    if (not isinstance(saved, dict)
            or set(saved) != {"schema", "started_at", "watermarks", "results",
                              "results_trimmed", "totals"}
            or saved["schema"] != SCHEMA
            or not isinstance(saved["watermarks"], dict)
            or set(saved["watermarks"]) != set(DIRECTIONS)
            or not isinstance(saved["results"], list) or len(saved["results"]) > MAX_RESULTS
            or type(saved["results_trimmed"]) is not bool
            or not isinstance(saved["totals"], dict)
            or set(saved["totals"]) != {*OUTCOMES, "skipped_not_linked"}
            or any(type(v) is not int or v < 0 for v in saved["totals"].values())):
        raise ValueError("Invalid or incompatible recorder reconciliation store")
    instant(saved["started_at"])
    for mark in saved["watermarks"].values():
        if not isinstance(mark, dict) or set(mark) != {"ended_at", "span_id"} or not (
                mark["span_id"] is None or isinstance(mark["span_id"], str)):
            raise ValueError("Invalid recorder reconciliation watermark")
        instant(mark["ended_at"])
    for row in saved["results"]:
        if (not isinstance(row, dict) or row.get("direction") not in DIRECTIONS
                or row.get("explanation") not in EXPLANATIONS
                or row.get("outcome") not in OUTCOMES
                or row.get("within_tolerance") is not (row["outcome"] == "within_tolerance")
                or row.get("billing_eligible") is not False
                or row.get("settlement_owner") != SETTLEMENT_OWNER
                or not isinstance(row.get("comparison_id"), str)):
            raise ValueError("Invalid recorder reconciliation result")
        instant(row["reconciled_at"])
        for key in ("window_start", "window_end"):
            if row.get(key) is not None:
                instant(row[key])
        for key in ("ocpp_kwh", "legacy_kwh"):
            if row.get(key) is not None:
                energy(row[key], "kWh")
    return saved


def migrate(saved):
    """Schema 1 is the first; any other schema is refused, never rewritten."""
    if isinstance(saved, dict) and saved.get("schema") == SCHEMA:
        return saved
    raise ValueError("Unsupported recorder reconciliation schema")


class ReconciliationLedger:
    """Bounded, restart-safe comparison results with a per-direction watermark."""

    def __init__(self, saved=None, started_at=None):
        self.data = fresh(started_at) if saved is None else deepcopy(validate(migrate(saved)))

    def candidates(self, direction, spans):
        """Closed spans ended after this direction's watermark, oldest first."""
        mark = instant(self.data["watermarks"][direction]["ended_at"])
        found = []
        for span in spans or ():
            try:
                ended = instant(span["observation_ended_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if ended > mark:
                found.append((ended, span))
        return [span for _, span in sorted(found, key=lambda item: item[0])]

    def _advance(self, direction, span):
        self.data["watermarks"][direction] = {"ended_at": span["observation_ended_at"],
                                              "span_id": span.get("span_id")}

    def skip(self, direction, span):
        self._advance(direction, span)
        self.data["totals"]["skipped_not_linked"] += 1

    def record(self, direction, span, result):
        self._advance(direction, span)
        self.data["results"].append(deepcopy(result))
        self.data["totals"][result["outcome"]] += 1
        if len(self.data["results"]) > MAX_RESULTS:
            self.data["results"] = self.data["results"][-MAX_RESULTS:]
            self.data["results_trimmed"] = True

    def last(self, direction=None):
        for row in reversed(self.data["results"]):
            if direction is None or row["direction"] == direction:
                return row
        return None

    def summary(self):
        results = self.data["results"]
        explanations = dict.fromkeys(EXPLANATIONS, 0)
        outcomes = dict.fromkeys(OUTCOMES, 0)
        for row in results:
            explanations[row["explanation"]] += 1
            outcomes[row["outcome"]] += 1
        return {"last_result": deepcopy(self.last()),
                "last_import": deepcopy(self.last("import")),
                "last_export": deepcopy(self.last("export")),
                "retained_count": len(results), "results_trimmed": self.data["results_trimmed"],
                "outcome_counts": outcomes, "explanation_counts": explanations,
                "aligned_count": explanations["aligned"],
                "unresolved_count": outcomes["unresolved"],
                "lifetime_totals": dict(self.data["totals"]),
                "started_at": self.data["started_at"]}
