"""Native OCPP session lifecycle and the session attribution contract (shadow only).

Pure and read-only: no HA imports, tariffs, amounts, wallet or charger calls.
It turns the OCPP integration's connector status / transaction ID / idTag / SoC
entity timeline into native sessions, and assembles each session's observed
import and export from the existing shadow spans. The result is diagnostic
evidence; ``billing_eligible`` is always False and the settlement owner stays
the legacy Sigenergy recorder.

Attribution contract (see docs/ocpp-lifecycle-replay.md):

1. Session identity is exactly ``(charger, connector, OCPP transactionId,
   start time)``. Nothing else, in particular not the idTag, takes part.
2. The idTag is untrusted metadata. On the live Sigenergy charger it is a
   random per-charger-session token: the same car receives new idTags and
   different cars can carry the same one. It is never a vehicle identifier,
   never an ownership or payment key and never merges or splits sessions. It is
   kept only as a one-way reference for diagnostics.
3. Vehicle attribution comes only from external evidence (for example SoC
   continuity across a session boundary) and is never automatic:
   ``vehicle_attribution`` is always ``unresolved`` here. Evidence is recorded
   for an operator to review, never acted on.
4. A closed transaction is never reopened. A late or duplicate stop for it is
   flagged on that session; energy is never bridged across a session boundary.
5. Missing, faulted or gapped observation is reported as ``None`` plus quality
   flags, never as zero and never merged into a neighbouring session.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import timedelta
from decimal import Decimal, DecimalException
import hashlib
import re

from .ocpp_shadow_ledger import instant

SETTLEMENT_OWNER = "legacy_sigen"
IDENTITY_FIELDS = ("charger_id", "connector_id", "transaction_id", "started_at")
# Fields that must never be part of an identity or ownership key.
UNTRUSTED_FIELDS = ("id_tag", "id_tag_ref", "idTag", "vehicle", "vehicle_id")
STOPPED = ("0", "")
UNKNOWN = ("unknown", "unavailable", "none")
# A Faulted status this soon after a stop belongs to the session that just ended.
FAULT_AFTER_STOP_S = 120
# A late final register advance this soon after a stop is reported, not attributed.
LATE_FINAL_WINDOW_S = 300
# SoC agreement across a boundary that counts as continuity evidence (#61 rule).
SOC_CONTINUITY_PCT = Decimal(2)
COMPLETE_END_REASONS = ("identity_or_lifecycle_boundary",)
# Persisted tracker state (live shadow observer): bounded, raw idTags never kept.
STATE_SCHEMA = 1
MAX_SESSIONS = 20
MAX_CLOSED_IDS = 200
MAX_TRACKER_EVENTS = 100
INPUT_KEYS = ("status", "transaction", "id_tag", "soc")
ID_TAG_REF = re.compile(r"idtag-[0-9a-f]{10}")


class AttributionError(ValueError):
    """Raised when untrusted metadata is offered as an identity or ownership key."""


@dataclass(frozen=True)
class SessionIdentity:
    """The only session identity. idTag is deliberately not a field."""
    charger_id: str
    connector_id: int
    transaction_id: str
    started_at: str

    def __post_init__(self):
        if not isinstance(self.charger_id, str) or not self.charger_id:
            raise AttributionError("charger required")
        if type(self.connector_id) is not int or self.connector_id < 0:
            raise AttributionError("connector required")
        if (not isinstance(self.transaction_id, str) or self.transaction_id in STOPPED
                or self.transaction_id.lower() in UNKNOWN):
            raise AttributionError("native transaction ID required")
        instant(self.started_at)

    def key(self):
        return tuple(getattr(self, name) for name in IDENTITY_FIELDS)


if tuple(f.name for f in fields(SessionIdentity)) != IDENTITY_FIELDS:  # pragma: no cover
    raise RuntimeError("SessionIdentity must have exactly the identity fields")


def ownership_key(session=None, **forbidden):
    """The key any consumer must use to own, attribute or bill a session.

    It is the session identity and nothing else. Passing an idTag or vehicle
    hint raises ``AttributionError`` rather than being silently ignored.
    """
    if forbidden:
        raise AttributionError(
            f"Only the session identity is an ownership key; refused: {sorted(forbidden)}")
    identity = session["identity"] if isinstance(session, dict) else session
    if isinstance(identity, dict):
        identity = SessionIdentity(**identity)
    if not isinstance(identity, SessionIdentity):
        raise AttributionError("Session identity required")
    return identity.key()


def id_tag_ref(value):
    """One-way, non-identifying reference for an idTag (diagnostics only)."""
    return "idtag-" + hashlib.sha256(str(value).encode()).hexdigest()[:10]


def _number(value):
    try:
        result = Decimal(str(value))
    except (DecimalException, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _classify_tx(value):
    text = "" if value is None else str(value).strip()
    if text.lower() in UNKNOWN:
        return "unknown", None
    if text in STOPPED:
        return "stopped", None
    return "active", text


class LifecycleTracker:
    """Native sessions from the connector entity timeline (HA arrival times).

    ``update(key, value, at)`` takes ``status``, ``transaction``, ``id_tag`` and
    ``soc`` events in time order. Unknown/unavailable values (an HA restart or
    integration reload) open an outage; they never end a session by themselves.
    """

    def __init__(self, charger_id, connector_id):
        self.charger_id = charger_id
        self.connector_id = connector_id
        self.sessions = []
        self.current = None
        self.closed_ids = {}
        self.status = None
        # Only the one-way reference of the current idTag is held, never the raw tag.
        self.id_tag_ref = None
        self.outage = None
        # The start is observed only when this tracker saw the connector without
        # a transaction immediately before (not at window start or after an outage).
        self.stopped_seen = False
        self.events = []
        self.last_seen = {}
        self.sessions_trimmed = False

    def _flag(self, session, flag):
        if flag not in session["quality_flags"]:
            session["quality_flags"].append(flag)
            session["quality_flags"].sort()

    def _open(self, tx, at):
        identity = SessionIdentity(self.charger_id, self.connector_id, tx, at)
        session = {
            "identity": asdict(identity), "transaction_id": tx, "started_at": at,
            "start_observed": self.stopped_seen,
            "ended_at": None, "end_reason": None, "finishing_at": None,
            "statuses": [self.status] if self.status else [],
            "ha_restarts": [], "quality_flags": [],
            "untrusted_metadata": {"id_tag_refs": [], "id_tag_changes": 0,
                                   "note": "idTag is a random per-session token; not identity"},
            "soc_first": None, "soc_last": None,
            "vehicle_attribution": "unresolved", "vehicle_attribution_automatic": False,
            "vehicle_evidence": None,
            "billing_eligible": False, "settlement_owner": SETTLEMENT_OWNER,
        }
        if not session["start_observed"]:
            self._flag(session, "start_not_observed")
        self.current = session
        self.sessions.append(session)
        if self.id_tag_ref:
            self._tag(self.id_tag_ref)
        self.events.append({"at": at, "kind": "session_started", "transaction_id": tx})

    def _close(self, at, reason, *flags):
        session = self.current
        session["ended_at"], session["end_reason"] = at, reason
        for flag in flags:
            self._flag(session, flag)
        if "Charging" not in session["statuses"]:
            self._flag(session, "never_charging")
        self.closed_ids[session["transaction_id"]] = session
        self.current = None
        self.events.append({"at": at, "kind": "session_ended", "reason": reason,
                            "transaction_id": session["transaction_id"]})

    def _tag(self, ref):
        session = self.current
        if session is None or ref is None:
            return
        refs = session["untrusted_metadata"]["id_tag_refs"]
        if ref not in refs:
            if refs:
                session["untrusted_metadata"]["id_tag_changes"] += 1
                self._flag(session, "id_tag_changed_within_transaction")
            refs.append(ref)

    def update(self, key, value, at):
        unknown = value is None or str(value).strip().lower() in UNKNOWN
        if key in INPUT_KEYS:
            self.last_seen[key] = at
        if key in ("status", "transaction") and unknown:
            if self.outage is None:
                self.outage = at
            if key == "transaction":
                self.stopped_seen = False
            return
        if self.outage is not None and key in ("status", "transaction"):
            if self.current is not None:
                self.current["ha_restarts"].append({"from": self.outage, "to": at})
                self._flag(self.current, "ha_restart_during_session")
            self.outage = None
        if key == "status":
            self._status(value, at)
        elif key == "transaction":
            self._transaction(value, at)
        elif key == "id_tag":
            self.id_tag_ref = None if unknown or value == "" else id_tag_ref(value)
            self._tag(self.id_tag_ref)
        elif key == "soc" and self.current is not None:
            number = _number(value)
            if number is not None and not unknown:
                if self.current["soc_first"] is None:
                    self.current["soc_first"] = {"at": at, "pct": str(number)}
                self.current["soc_last"] = {"at": at, "pct": str(number)}

    def _status(self, value, at):
        self.status = value
        session = self.current
        if session is not None:
            if not session["statuses"] or session["statuses"][-1] != value:
                session["statuses"].append(value)
            if value == "Finishing" and session["finishing_at"] is None:
                session["finishing_at"] = at
            if value == "Faulted":
                self._flag(session, "charger_fault")
        elif value == "Faulted" and self.sessions:
            last = self.sessions[-1]
            if (last["ended_at"] and instant(at) - instant(last["ended_at"])
                    <= timedelta(seconds=FAULT_AFTER_STOP_S)):
                last["statuses"].append("Faulted")
                self._flag(last, "charger_fault_at_end")

    def _transaction(self, value, at):
        kind, tx = _classify_tx(value)
        session = self.current
        if kind == "stopped":
            if session is not None:
                self._close(at, "transaction_cleared")
            self.stopped_seen = True
            return
        if session is not None and tx == session["transaction_id"]:
            return
        if tx in self.closed_ids:
            # A closed transaction never reopens: late/duplicate stop evidence only.
            closed = self.closed_ids[tx]
            if closed is not None:  # None: the closed session aged out of the store
                self._flag(closed, "late_or_duplicate_transaction_event")
                closed.setdefault("late_events", []).append(at)
            self.events.append({"at": at, "kind": "late_or_duplicate_transaction_event",
                                "transaction_id": tx})
            return
        if session is not None:
            self._close(at, "superseded_without_stop", "stop_not_observed")
        self._open(tx, at)
        self.stopped_seen = False

    def finish(self, at):
        """End of the replay window: an open session stays open (not stopped)."""
        if self.current is not None:
            self._flag(self.current, "open_at_window_end")
        return self.sessions

    # Persistence for the live shadow observer --------------------------------
    def trim(self):
        """Bound retained sessions, closed IDs and events. Never drops the open session."""
        if len(self.sessions) > MAX_SESSIONS:
            self.sessions = self.sessions[-MAX_SESSIONS:]
            self.sessions_trimmed = True
        kept = {s["transaction_id"]: s for s in self.sessions if s["ended_at"]}
        ids = list(self.closed_ids)[-MAX_CLOSED_IDS:]
        self.closed_ids = {tx: kept.get(tx) for tx in ids}
        self.events = self.events[-MAX_TRACKER_EVENTS:]

    def state(self):
        """JSON-safe tracker state. Holds idTag references only, never a raw idTag."""
        self.trim()
        return deepcopy({
            "schema": STATE_SCHEMA, "charger_id": self.charger_id,
            "connector_id": self.connector_id, "sessions": self.sessions,
            "current": self.current is not None, "closed_transaction_ids": list(self.closed_ids),
            "status": self.status, "id_tag_ref": self.id_tag_ref, "outage": self.outage,
            "stopped_seen": self.stopped_seen, "last_seen": self.last_seen,
            "events": self.events, "sessions_trimmed": self.sessions_trimmed,
            "billing_eligible": False, "settlement_owner": SETTLEMENT_OWNER})

    @classmethod
    def restore(cls, state, charger_id, connector_id):
        """Validated tracker from ``state()``; raises ValueError rather than guessing."""
        try:
            return cls._restore(state, charger_id, connector_id)
        except (AttributionError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError(f"Invalid OCPP lifecycle state: {exc}") from None

    @classmethod
    def _restore(cls, state, charger_id, connector_id):
        if (not isinstance(state, dict) or state.get("schema") != STATE_SCHEMA
                or state.get("charger_id") != charger_id
                or state.get("connector_id") != connector_id
                or state.get("billing_eligible") is not False
                or state.get("settlement_owner") != SETTLEMENT_OWNER
                or not isinstance(state.get("sessions"), list)
                or len(state["sessions"]) > MAX_SESSIONS
                or type(state.get("current")) is not bool
                or not isinstance(state.get("closed_transaction_ids"), list)
                or len(state["closed_transaction_ids"]) > MAX_CLOSED_IDS
                or not isinstance(state.get("events"), list)
                or len(state["events"]) > MAX_TRACKER_EVENTS
                or type(state.get("stopped_seen")) is not bool
                or type(state.get("sessions_trimmed")) is not bool
                or not isinstance(state.get("last_seen"), dict)
                or set(state["last_seen"]) - set(INPUT_KEYS)):
            raise ValueError("Invalid or foreign OCPP lifecycle state")
        ref = state.get("id_tag_ref")
        if ref is not None and not ID_TAG_REF.fullmatch(str(ref)):
            raise ValueError("OCPP lifecycle state holds a non-reference idTag")
        for at in [state.get("outage"), *state["last_seen"].values()]:
            if at is not None:
                instant(at)
        tracker = cls(charger_id, connector_id)
        sessions = deepcopy(state["sessions"])
        for index, session in enumerate(sessions):
            identity = SessionIdentity(**session["identity"])
            refs = session["untrusted_metadata"]["id_tag_refs"]
            if (identity.charger_id != charger_id or identity.connector_id != connector_id
                    or session["transaction_id"] != identity.transaction_id
                    or session["started_at"] != identity.started_at
                    or session["billing_eligible"] is not False
                    or session["settlement_owner"] != SETTLEMENT_OWNER
                    or session["vehicle_attribution"] != "unresolved"
                    or not isinstance(refs, list)
                    or not all(isinstance(r, str) and ID_TAG_REF.fullmatch(r) for r in refs)
                    or not all(isinstance(f, str) for f in session["quality_flags"])
                    or not isinstance(session["statuses"], list)
                    or not isinstance(session["ha_restarts"], list)
                    or (session["ended_at"] is None) != (state["current"] and index == len(sessions) - 1)):
                raise ValueError("Invalid OCPP lifecycle session")
            if session["ended_at"] is not None:
                instant(session["ended_at"])
        tracker.sessions = sessions
        tracker.current = sessions[-1] if state["current"] else None
        closed = {s["transaction_id"]: s for s in sessions if s["ended_at"]}
        tracker.closed_ids = {str(tx): closed.get(tx) for tx in state["closed_transaction_ids"]}
        tracker.status = state.get("status")
        tracker.id_tag_ref = ref
        tracker.outage = state.get("outage")
        tracker.stopped_seen = state["stopped_seen"]
        tracker.last_seen = dict(state["last_seen"])
        tracker.events = deepcopy(state["events"])
        tracker.sessions_trimmed = state["sessions_trimmed"]
        return tracker

    def resume(self, at):
        """Observer (re)started: until the connector is seen again this is an outage.

        An HA restart or reload is never a stop. The open session, if any, stays
        open; the next status/transaction value records the restart on it.
        """
        if self.outage is None:
            self.outage = at
        self.stopped_seen = False


def vehicle_evidence(sessions):
    """SoC continuity across consecutive sessions. Evidence only; never attributes."""
    previous = None
    for session in sessions:
        evidence = {"method": "soc_continuity", "assessment": "insufficient",
                    "previous_transaction_id": None, "previous_last_soc_pct": None,
                    "first_soc_pct": None, "delta_pct": None,
                    "automatic_attribution": False}
        if previous is not None:
            evidence["previous_transaction_id"] = previous["transaction_id"]
            last = previous["soc_last"]
            first = session["soc_first"]
            if last and first:
                delta = Decimal(first["pct"]) - Decimal(last["pct"])
                evidence.update(previous_last_soc_pct=last["pct"], first_soc_pct=first["pct"],
                                delta_pct=str(delta),
                                assessment=("continuous" if abs(delta) <= SOC_CONTINUITY_PCT
                                            else "discontinuous"))
        session["vehicle_evidence"] = evidence
        session["vehicle_attribution"] = "unresolved"
        previous = session
    return sessions


def _within(session, span, window_end):
    try:
        first = instant(span["first_meter_ha_updated_at"])
    except (KeyError, TypeError, ValueError):
        return False
    end = instant(session["ended_at"]) if session["ended_at"] else instant(window_end)
    return instant(session["started_at"]) <= first <= end


def _sum(values):
    return sum((Decimal(v) for v in values), Decimal(0))


def _text(value):
    return None if value is None else f"{value.quantize(Decimal('0.000001')).normalize():f}"


def _direction(session, spans, direction, results, reference, tolerances, window_end):
    """Observed totals for one direction plus aligned reference comparison."""
    key = "observed_import_kwh" if direction == "import" else "estimate_kwh"
    mine = [s for s in spans if s.get("native_transaction_id") == session["transaction_id"]
            and _within(session, s, window_end)]
    flags = set()
    if not mine:
        flags.add(f"no_{direction}_observation")
    for span in mine:
        reason = span.get("end_reason")
        if reason is None:
            flags.add(f"{direction}_span_open")
        elif reason not in COMPLETE_END_REASONS:
            flags.add(f"{direction}_{reason}")
        if direction == "export":
            if span.get("source_label") == "estimated":
                flags.add("export_estimated")
            if span.get("grade") not in (None, "within_tolerance"):
                flags.add(f"export_grade_{span['grade']}")
    observed = _sum(s[key] for s in mine) if mine else None
    by_span = {r["span_id"]: r for r in results if r["direction"] == direction}
    recon = [by_span[s["span_id"]] for s in mine if s["span_id"] in by_span]
    outcomes = sorted({r["outcome"] for r in recon})
    result = {
        "observed_kwh": _text(observed), "span_count": len(mine),
        "span_ids": [s["span_id"] for s in mine],
        "span_end_reasons": sorted({s.get("end_reason") or "open" for s in mine}),
        "reconciliation_outcomes": {o: sum(r["outcome"] == o for r in recon) for o in outcomes},
        "reconciliation_explanations": sorted({r["explanation"] for r in recon}),
    }
    if direction == "export" and mine:
        lowers = [s.get("lower_kwh") for s in mine]
        uppers = [s.get("upper_kwh") for s in mine]
        result["observed_lower_kwh"] = None if None in lowers else _text(_sum(lowers))
        result["observed_upper_kwh"] = None if None in uppers else _text(_sum(uppers))
        result["grades"] = sorted({s.get("grade") or "open" for s in mine})
    if reference is not None:
        result.update(_reference(session, mine, direction, reference, tolerances, observed,
                                 window_end, flags))
    result["quality_flags"] = sorted(flags)
    return result


def _reference(session, mine, direction, reference, tolerances, observed, window_end, flags):
    """Compare observed spans with the reference counter over the SAME windows."""
    from .recorder_reconciliation import legacy_window, tolerance_rules

    rules = tolerance_rules(tolerances)
    pct, floor = Decimal(rules[direction + "_pct"]), Decimal(rules[direction + "_floor_kwh"])
    start = instant(session["started_at"])
    end = instant(session["ended_at"] or window_end)
    whole = legacy_window(reference, start, end)
    out = {"reference_session_kwh": _text(whole.get("estimate")),
           "reference_session_status": whole["status"]}
    windows = [legacy_window(reference, instant(s["first_meter_ha_updated_at"]),
                             instant(s["last_meter_ha_updated_at"])) for s in mine]
    if not mine:
        out.update(reference_observed_kwh=None, divergence_kwh=None, divergence_pct=None,
                   allowance_kwh=None, comparison="unresolved_no_observation")
        if whole.get("estimate") is not None and whole["estimate"] > floor:
            flags.add("reference_energy_without_observation")
        return out
    if any(w["status"] != "ok" for w in windows):
        flags.add("reference_unavailable")
        out.update(reference_observed_kwh=None, divergence_kwh=None, divergence_pct=None,
                   allowance_kwh=None, comparison="unresolved_reference_missing")
        return out
    aligned = _sum(w["estimate"] for w in windows)
    allowance = max(pct / 100 * aligned, floor)
    divergence = observed - aligned
    if max(observed, aligned) < Decimal(rules["min_energy_kwh"]):
        comparison = "insufficient_energy"
    elif abs(divergence) <= allowance:
        comparison = "within_tolerance"
    else:
        comparison = "outside_tolerance"
        flags.add(f"{direction}_divergence_outside_tolerance")
    outside = (whole["estimate"] - aligned) if whole.get("estimate") is not None else None
    if outside is not None and outside > floor:
        flags.add(f"{direction}_reference_energy_outside_observed_spans")
    out.update(reference_observed_kwh=_text(aligned), divergence_kwh=_text(divergence),
               divergence_pct=(str((divergence / aligned * 100).quantize(Decimal("0.01")))
                               if aligned else None),
               allowance_kwh=_text(allowance), comparison=comparison,
               reference_outside_spans_kwh=_text(outside), tolerance_pct=rules[direction + "_pct"])
    return out


def late_final_reading(session, register, next_start):
    """Import register advance after a stop, reported and never attributed."""
    if not session["ended_at"] or not register:
        return None
    end = instant(session["ended_at"])
    limit = end + timedelta(seconds=LATE_FINAL_WINDOW_S)
    if next_start is not None:
        limit = min(limit, instant(next_start))
    at_end = [v for t, v in register if t <= end]
    after = [v for t, v in register if end < t <= limit]
    if not at_end or not after:
        return None
    advance = max(after) - at_end[-1]
    return advance if advance > 0 else None


def assemble(sessions, import_spans, export_spans, results=(), references=None,
             tolerances=None, window_end=None, import_register=None):
    """Attach shadow spans to native sessions by transaction ID and time window.

    Spans are matched to exactly one session: same native transaction ID and a
    first sample inside that session's [start, end]. Totals are sums of
    observed spans only. No observation is ``None`` with flags, never zero.
    """
    references = references or {}
    vehicle_evidence(sessions)
    assembled = []
    for index, session in enumerate(sessions):
        row = {**session, "quality_flags": list(session["quality_flags"])}
        row["ownership_key"] = list(ownership_key(session))
        for direction, spans in (("import", import_spans), ("export", export_spans)):
            row[direction] = _direction(session, spans, direction, results,
                                        references.get(direction), tolerances, window_end)
        following = sessions[index + 1]["started_at"] if index + 1 < len(sessions) else None
        late = late_final_reading(session, import_register, following)
        row["late_final_import_kwh"] = _text(late)
        flags = set(row["quality_flags"]) | set(row["import"]["quality_flags"]) | set(
            row["export"]["quality_flags"])
        if late is not None:
            flags.add("late_final_reading_unattributed")
        if row["import"]["observed_kwh"] is None and row["export"]["observed_kwh"] is None:
            flags.add("no_meter_observation")
        row["quality_flags"] = sorted(flags)
        assembled.append(row)
    return assembled


def overlapping(sessions):
    """Pairs of sessions on the same connector whose time windows overlap (should be none)."""
    found = []
    for a, b in zip(sessions, sessions[1:]):
        if a["ended_at"] is None or instant(b["started_at"]) < instant(a["ended_at"]):
            found.append((a["transaction_id"], b["transaction_id"]))
    return found


def as_datetime_rows(rows, scale=Decimal(1)):
    """[(iso, value)] -> sorted [(datetime, kWh)]; unavailable rows are not readings."""
    result = []
    for at, value in rows:
        number = _number(value)
        if number is not None and number >= 0:
            result.append((instant(at) if isinstance(at, str) else at, number * scale))
    return sorted(result, key=lambda item: item[0])


__all__ = ["AttributionError", "LifecycleTracker", "SessionIdentity", "assemble",
           "id_tag_ref", "late_final_reading", "overlapping", "ownership_key",
           "vehicle_evidence", "as_datetime_rows"]
