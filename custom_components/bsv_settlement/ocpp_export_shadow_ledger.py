"""Graded, entity-observed export spans from a derived OCPP register; never settleable.

The purcell-lab OCPP fork derives an export register by integrating negative
import power between 60 s samples, with min/max bounds. This ledger only
records how far that estimate moved inside conservative observation spans and
how wide its bounds are. HA state timestamps are arrival evidence; the fork's
``last_sample_timestamp`` is the charger clock. No tariffs, credits, net
amounts, authority, protocol interception or charger commands.
"""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, DecimalException
import hashlib
import json

from .ocpp_shadow_ledger import ACTIVE, energy, instant, transaction

MAX_EVENTS = 1000
MAX_SPANS = 50
MIN_GAP_SECONDS = 180
MAX_GAP_SECONDS = 86400
FUTURE_SECONDS = 5
# A flow change is paired with the register sample it arrived with.
FLOW_PAIRING_SECONDS = 5
# Below 1 Wh per interval a register move is treated as flat for anomaly flags
# only (60 s at -58 W is 0.97 Wh); every register delta still counts in the span.
FLAT_KWH = Decimal("0.001")
DERIVED_SOURCE = "derived_from_negative_import"
LABELS = ("estimated", "native_unverified")
FLOWS = ("import", "export", "idle", "unknown")
GRADES = ("ungraded", "insufficient_energy", "within_tolerance", "wide", "unreliable")
DEFAULT_GRADING = {"min_energy_kwh": "0.1", "tolerance_pct": "5", "wide_pct": "15"}
SPREAD_PLACES = Decimal("0.000001")


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


def grading_rules(values=None):
    """Validated thresholds as Decimal strings; raises ValueError when unusable."""
    merged = {**DEFAULT_GRADING, **{k: v for k, v in (values or {}).items() if v is not None}}
    if set(merged) != set(DEFAULT_GRADING):
        raise ValueError("Unknown export grading threshold")
    minimum, tolerance, wide = (_decimal(merged[k]) for k in DEFAULT_GRADING)
    if not (Decimal("0.001") <= minimum <= 100 and 0 < tolerance < wide <= 100):
        raise ValueError("Export grading thresholds out of range")
    return {"min_energy_kwh": str(minimum.normalize()), "tolerance_pct": str(tolerance.normalize()),
            "wide_pct": str(wide.normalize())}


def grade(span, rules):
    """(grade, spread) for a span's deltas. Spread is a fraction of the estimate."""
    estimate = Decimal(span["estimate_kwh"])
    if span["lower_kwh"] is None or span["upper_kwh"] is None:
        return "ungraded", None
    lower, upper = Decimal(span["lower_kwh"]), Decimal(span["upper_kwh"])
    if not lower <= estimate <= upper:
        return "ungraded", None
    if estimate < Decimal(rules["min_energy_kwh"]):
        return "insufficient_energy", None
    spread = (upper - lower) / estimate
    percent = spread * 100
    result = ("within_tolerance" if percent <= Decimal(rules["tolerance_pct"])
              else "wide" if percent <= Decimal(rules["wide_pct"]) else "unreliable")
    return result, str(spread.quantize(SPREAD_PLACES))


def source_label(attributes):
    """Derived-by-integration is an estimate; anything else is unverified native data."""
    return "estimated" if (attributes or {}).get("source") == DERIVED_SOURCE else "native_unverified"


def _text(value):
    """Canonical plain decimal text, e.g. 0E-26 -> "0" and 60.0 -> "60"."""
    return f"{value.normalize():f}"


def _bound(value):
    try:
        result = _decimal(value)
    except ValueError:
        return None
    return result if 0 <= result <= Decimal("1000000000") else None


def _steps(value):
    return value if type(value) is int and 0 <= value <= 10**9 else None


def _flow(row):
    value = (row or {}).get("value")
    return value if value in ("import", "export", "idle") else "unknown"


def _gap_limit(attributes):
    try:
        attr = _decimal((attributes or {}).get("max_sample_gap_s"))
    except ValueError:
        return Decimal(MIN_GAP_SECONDS)
    return max(Decimal(MIN_GAP_SECONDS), min(attr, Decimal(MAX_GAP_SECONDS)))


def _charger_time(attributes):
    value = (attributes or {}).get("last_sample_timestamp")
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        return instant(value)
    except ValueError:
        return None


def _optional_kwh(text):
    if text is not None:
        energy(text, "kWh")


def validate_section(saved):
    """Full structural check of a stored export section; raises ValueError."""
    if (not isinstance(saved, dict)
            or set(saved) != {"binding", "reference_binding", "sequence", "events", "spans",
                              "current", "journal_trimmed", "spans_trimmed"}
            or not isinstance(saved["binding"], (dict, type(None)))
            or not isinstance(saved["reference_binding"], (dict, type(None)))
            or type(saved["sequence"]) is not int or saved["sequence"] < 0
            or not isinstance(saved["events"], list) or len(saved["events"]) > MAX_EVENTS
            or not isinstance(saved["spans"], list) or len(saved["spans"]) > MAX_SPANS
            or type(saved["journal_trimmed"]) is not bool
            or type(saved["spans_trimmed"]) is not bool):
        raise ValueError("Invalid OCPP export shadow store")
    for row in saved["events"]:
        if not isinstance(row, dict) or not isinstance(row.get("kind"), str):
            raise ValueError("Invalid OCPP export shadow journal")
        instant(row["observed_at"])
    closed = [(span, True) for span in saved["spans"]]
    for span, ended in [*closed, *([(saved["current"], False)] if saved["current"] else [])]:
        if (not isinstance(span, dict) or not isinstance(span.get("span_id"), str)
                or not transaction(span.get("native_transaction_id"))
                or span.get("source_label") not in LABELS
                or type(span.get("sample_count")) is not int or span["sample_count"] < 1
                or span.get("billing_eligible") is not False
                or span.get("settlement_owner") is not None
                or not isinstance(span.get("flow_direction_counts"), dict)
                or set(span["flow_direction_counts"]) != set(FLOWS)
                or any(type(v) is not int or v < 0 for v in span["flow_direction_counts"].values())
                or not all(isinstance(span.get(k), list) for k in (
                    "anomaly_flags", "provenance_flags", "quality_flags"))
                or (span.get("grade") in GRADES) is not ended):
            raise ValueError("Invalid OCPP export shadow span")
        for key in ("estimate_kwh", "register_start_kwh", "register_last_kwh"):
            energy(span[key], "kWh")
        for key in ("lower_kwh", "upper_kwh", "reference_delta_kwh"):
            _optional_kwh(span.get(key))
        instant(span["first_meter_ha_updated_at"])
        instant(span["last_meter_ha_updated_at"])
        for key in ("first_source_timestamp", "last_source_timestamp"):
            if span.get(key) is not None:
                instant(span[key])
    return saved


def fresh(binding=None, reference=None):
    return {"binding": deepcopy(binding), "reference_binding": deepcopy(reference),
            "sequence": 0, "events": [], "spans": [], "current": None,
            "journal_trimmed": False, "spans_trimmed": False}


class ExportShadowLedger:
    """Restart-safe bounded export observation journal. Deliberately no session API.

    ``binding``/``reference`` are the validated identities currently configured
    (None when unbound). A stored section with other bindings is kept; its open
    span ends at the restart gap and the change is journaled.
    """

    def __init__(self, saved=None, binding=None, reference=None, grading=None):
        self.binding = deepcopy(binding) or None
        self.reference = deepcopy(reference) or None
        self.rules = grading_rules(grading)
        self.data = fresh(self.binding, self.reference) if saved is None else deepcopy(
            validate_section(saved))
        self.context = None
        self.barrier = None
        self.last = None
        self.flags = {"observation_not_started"}
        self.restart_pending = True

    def event(self, kind, now, **values):
        self.data["events"].append({"kind": kind, "observed_at": now, **values})
        if len(self.data["events"]) > MAX_EVENTS:
            self.data["events"] = self.data["events"][-MAX_EVENTS:]
            self.data["journal_trimmed"] = True

    def settle(self):
        """Count the last sample's paired flow and judge the interval ending there."""
        current, last = self.data["current"], self.last
        if not current or not last or last.get("settled"):
            return
        last["settled"] = True
        current["flow_direction_counts"][last["flow"]] += 1
        delta, flows = last["delta"], (last["previous_flow"], last["flow"])
        if delta is None:
            return
        anomalies = set(current["anomaly_flags"])
        if flows == ("export", "export") and delta <= FLAT_KWH:
            anomalies.add("export_flow_without_register_advance")
        if flows == ("import", "import") and delta > FLAT_KWH:
            anomalies.add("register_advance_during_import")
        current["anomaly_flags"] = sorted(anomalies)

    def close_span(self, reason, now, anomaly=None):
        current = self.data["current"]
        if current:
            self.settle()
            if anomaly:
                current["anomaly_flags"] = sorted({*current["anomaly_flags"], anomaly})
            current["grade"], current["spread"] = grade(current, self.rules)
            current["grading"] = dict(self.rules)
            current["observation_ended_at"] = now
            current["end_reason"] = reason
            self.data["spans"].append(current)
            if len(self.data["spans"]) > MAX_SPANS:
                self.data["spans"] = self.data["spans"][-MAX_SPANS:]
                self.data["spans_trimmed"] = True
            self.data["current"] = None
            self.event("export_observation_boundary", now, reason=reason,
                       span_id=current["span_id"], grade=current["grade"])
        self.last = None

    def _restart(self, now, clock):
        self.close_span("restart_gap", now)
        self.event("observer_started", now)
        for key, value in (("binding", self.binding), ("reference_binding", self.reference)):
            if self.data[key] != value:
                self.event(key + "_changed", now,
                           previous=(self.data[key] or {}).get("unique_id"),
                           current=(value or {}).get("unique_id"))
                self.data[key] = deepcopy(value)
        self.barrier = clock
        self.restart_pending = False

    def observe(self, snapshot, now):
        """snapshot: status/transaction/export/flow rows, optional session/reference."""
        clock = instant(now)
        if self.restart_pending:
            self._restart(now, clock)
        if self.binding is None:
            self.flags = {"export_not_bound"}
            return
        if snapshot.get("binding_valid") is False:
            self.close_span("export_binding_changed", now)
            self.barrier = clock
            self.flags = {"export_binding_changed"}
            return
        status = snapshot.get("status", {}).get("value")
        tx = transaction(snapshot.get("transaction", {}).get("value"))
        context = (status, tx)
        if context != self.context:
            same_tx = (self.context and tx == self.context[1] and
                       status in ACTIVE and self.context[0] in ACTIVE)
            if not same_tx:
                self.close_span("identity_or_lifecycle_boundary", now)
                self.barrier = clock
            self.context = context
        if status not in ACTIVE or tx is None:
            self.flags = {"lifecycle_unresolved" if status in (None, "unknown", "unavailable")
                          else "not_observing_active_transaction"}
            return
        meter = snapshot.get("export") or {}
        attributes = meter.get("attributes") or {}
        try:
            timestamp = instant(meter["ha_updated_at"])
            value = energy(meter["value"], meter.get("unit"))
            if meter.get("restored") or (clock - timestamp).total_seconds() < -FUTURE_SECONDS:
                raise ValueError("Restored or future export register")
        except (KeyError, TypeError, ValueError):
            self.close_span("invalid_register_or_unit", now)
            self.flags = {"invalid_register_or_unit"}
            self.barrier = clock
            return
        flow = _flow(snapshot.get("flow"))
        limit = _gap_limit(attributes)
        charger = _charger_time(attributes)
        token = [timestamp.isoformat(), str(value), charger.isoformat() if charger else None]
        if self.last and token == self.last["token"]:
            flow_at = (snapshot.get("flow") or {}).get("ha_updated_at")
            try:
                paired = instant(flow_at) <= timestamp + timedelta(seconds=FLOW_PAIRING_SECONDS)
            except (TypeError, ValueError):
                paired = False
            if paired and not self.last.get("settled"):
                self.last["flow"] = flow
            if Decimal(str((clock - timestamp).total_seconds())) > limit:
                self.close_span("meter_gap", now)
                self.event("excluded_delta", now, reason="meter_gap")
                self.barrier = clock
                self.flags = {"awaiting_fresh_baseline"}
            return
        if self.barrier is not None and timestamp <= self.barrier:
            self.flags = {"awaiting_fresh_baseline"}
            return
        label = source_label(attributes)
        if self.last:
            previous = self.last
            backwards = timestamp <= instant(previous["token"][0]) or (
                charger and previous["charger"] and charger <= instant(previous["charger"]))
            if backwards:
                self.close_span("out_of_order_or_conflicting_register", now)
                self.barrier = clock
                self.flags = {"out_of_order_or_conflicting_register"}
                return
            gaps = [Decimal(str((timestamp - instant(previous["token"][0])).total_seconds()))]
            if charger and previous["charger"]:
                gaps.append(Decimal(str((charger - instant(previous["charger"])).total_seconds())))
            if value < Decimal(previous["kwh"]):
                self.close_span("register_decrease", now, anomaly="register_decrease")
                self.event("excluded_delta", now, reason="register_decrease")
            elif max(gaps) > limit:
                self.close_span("meter_gap", now)
                self.event("excluded_delta", now, reason="meter_gap")
            elif label != self.data["current"]["source_label"]:
                self.close_span("source_label_changed", now)
            else:
                self.settle()
        self.record(snapshot, now, tx, timestamp, value, charger, flow, label, attributes)

    def record(self, snapshot, now, tx, timestamp, value, charger, flow, label, attributes):
        lower, upper = _bound(attributes.get("energy_lower_bound_kwh")), _bound(
            attributes.get("energy_upper_bound_kwh"))
        steps = _steps(attributes.get("step_intervals"))
        reference = _reference(snapshot.get("reference"))
        session = _session(snapshot.get("session_export"))
        provenance = set(snapshot.get("provenance_flags") or ())
        current = self.data["current"]
        if current is None:
            self.data["sequence"] += 1
            key = json.dumps([self.binding, self.data["sequence"], tx, now], sort_keys=True)
            current = self.data["current"] = {
                "span_id": "ocpp-export-shadow-" + hashlib.sha256(key.encode()).hexdigest()[:24],
                "native_transaction_id": tx, "observed_at": now, "source_label": label,
                "first_meter_ha_updated_at": timestamp.isoformat(),
                "last_meter_ha_updated_at": timestamp.isoformat(),
                "first_source_timestamp": charger.isoformat() if charger else None,
                "last_source_timestamp": None,
                "register_start_kwh": str(value), "register_last_kwh": str(value),
                "estimate_kwh": "0",
                "bounds_start": ([str(lower), str(upper)] if lower is not None
                                 and upper is not None else None),
                "bounds_last": None, "lower_kwh": None, "upper_kwh": None,
                "steps_start": steps, "step_intervals": None,
                "sample_count": 0, "max_gap_s": None,
                "flow_direction_counts": dict.fromkeys(FLOWS, 0),
                "anomaly_flags": [], "provenance_flags": sorted(provenance),
                "quality_flags": [], "partial_start": True,
                "reference_start_kwh": reference, "reference_last_kwh": reference,
                "reference_status": "not_bound" if self.reference is None else (
                    "ok" if reference is not None else "unavailable"),
                "reference_delta_kwh": None, "reference_divergence_kwh": None,
                "reference_divergence_ratio": None,
                "session_export_start_kwh": session, "session_export_last_kwh": session,
                "session_export_delta_kwh": None,
                "grade": None, "spread": None,
                "billing_eligible": False, "settlement_owner": None,
            }
            previous_flow, delta = None, None
        else:
            previous_flow = self.last["flow"]
            delta = value - Decimal(self.last["kwh"])
            gap = Decimal(str((timestamp - instant(self.last["token"][0])).total_seconds()))
            if current["max_gap_s"] is None or gap > Decimal(current["max_gap_s"]):
                current["max_gap_s"] = _text(gap)
        quality = set(current["quality_flags"])
        if charger is None:
            quality.add("source_timestamp_unavailable")
        if label == "native_unverified":
            quality.add("native_export_unverified")
        current["register_last_kwh"] = str(value)
        current["estimate_kwh"] = _text(value - Decimal(current["register_start_kwh"]))
        self._bounds(current, lower, upper, quality)
        if current["steps_start"] is not None and steps is not None and steps >= current["steps_start"]:
            current["step_intervals"] = steps - current["steps_start"]
        else:
            current["steps_start"], current["step_intervals"] = None, None
        self._reference(current, reference)
        if current["session_export_start_kwh"] is not None and session is not None:
            current["session_export_last_kwh"] = session
            change = Decimal(session) - Decimal(current["session_export_start_kwh"])
            current["session_export_delta_kwh"] = _text(change) if change >= 0 else None
            if change < 0:
                quality.add("session_export_reset")
        current["quality_flags"] = sorted(quality)
        current["provenance_flags"] = sorted({*current["provenance_flags"], *provenance})
        current["sample_count"] += 1
        current["last_meter_ha_updated_at"] = timestamp.isoformat()
        current["last_source_timestamp"] = charger.isoformat() if charger else None
        self.last = {"token": [timestamp.isoformat(), str(value),
                               charger.isoformat() if charger else None],
                     "charger": charger.isoformat() if charger else None,
                     "kwh": str(value), "flow": flow, "previous_flow": previous_flow,
                     "delta": delta, "settled": False}
        self.flags = set()
        self.event("export_sample", now, span_id=current["span_id"], native_transaction_id=tx,
                   normalized_kwh=str(value), lower_kwh=None if lower is None else str(lower),
                   upper_kwh=None if upper is None else str(upper), step_intervals=steps,
                   ha_updated_at=timestamp.isoformat(),
                   source_timestamp=charger.isoformat() if charger else None,
                   flow_direction=flow, source_label=label, reference_kwh=reference)

    @staticmethod
    def _bounds(current, lower, upper, quality):
        start = current["bounds_start"]
        if lower is None or upper is None:
            quality.add("bounds_unavailable")
        if start is None or lower is None or upper is None:
            current["bounds_start"] = current["lower_kwh"] = current["upper_kwh"] = None
            return
        previous = current["bounds_last"] or start
        if lower < Decimal(previous[0]) or upper < Decimal(previous[1]):
            quality.add("bounds_decreased")
            current["bounds_start"] = current["lower_kwh"] = current["upper_kwh"] = None
            return
        current["bounds_last"] = [str(lower), str(upper)]
        current["lower_kwh"] = _text(lower - Decimal(start[0]))
        current["upper_kwh"] = _text(upper - Decimal(start[1]))
        if not (Decimal(current["lower_kwh"]) <= Decimal(current["estimate_kwh"])
                <= Decimal(current["upper_kwh"])):
            quality.add("bounds_do_not_bracket_estimate")

    def _reference(self, current, reference):
        if current["reference_status"] != "ok":
            return
        if reference is None:
            current["reference_status"] = "unavailable"
        elif Decimal(reference) < Decimal(current["reference_start_kwh"]):
            current["reference_status"] = "reference_decreased"
        else:
            current["reference_last_kwh"] = reference
            delta = Decimal(reference) - Decimal(current["reference_start_kwh"])
            divergence = Decimal(current["estimate_kwh"]) - delta
            current["reference_delta_kwh"] = _text(delta)
            current["reference_divergence_kwh"] = _text(divergence)
            current["reference_divergence_ratio"] = (
                str((divergence / delta).quantize(SPREAD_PLACES)) if delta > 0 else None)
            return
        current["reference_delta_kwh"] = current["reference_divergence_kwh"] = None
        current["reference_divergence_ratio"] = None

    def summary(self):
        current = self.data["current"]
        counts = dict.fromkeys(GRADES, 0)
        for span in self.data["spans"]:
            counts[span["grade"]] += 1
        last = self.data["spans"][-1] if self.data["spans"] else None
        return {
            "state": ("not_bound" if self.binding is None else
                      "observing" if current and not self.flags else "waiting"),
            "flags": sorted(self.flags),
            "current_span": deepcopy(current), "last_span": deepcopy(last),
            "latest_grade": last["grade"] if last else None,
            "grade_counts": counts, "grading": dict(self.rules),
            "retained_span_count": len(self.data["spans"]),
            "journal_trimmed": self.data["journal_trimmed"],
            "spans_trimmed": self.data["spans_trimmed"],
            "billing_eligible": False, "settlement_owner": None,
            "payment_control": False, "charger_control": False,
            "export_credit_aud": None, "net_cost_aud": None,
        }


def _reference(row):
    """Reference rows are already converted to kWh by the coordinator."""
    try:
        return str(energy((row or {})["kwh"], "kWh"))
    except (KeyError, TypeError, ValueError):
        return None


def _session(row):
    try:
        return str(energy(row["value"], row.get("unit")))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
