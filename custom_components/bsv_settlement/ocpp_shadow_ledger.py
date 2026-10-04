"""Conservative entity-observed import spans, never settleable OCPP sessions.

HA state timestamps are arrival evidence, NOT original charger meter timestamps.
No tariffs, wallet amounts, authority, protocol interception or charger commands.
"""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal, DecimalException
import hashlib
import json

SCHEMA = 3
MAX_EVENTS = 1000
MAX_SPANS = 50
MAX_AGE_SECONDS = 180
ACTIVE = {"Charging", "SuspendedEV", "SuspendedEVSE"}
UNITS = {"Wh": Decimal("0.001"), "kWh": Decimal(1), "MWh": Decimal(1000)}


def instant(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Timezone required")
    return result


def energy(value, unit):
    """Decimal conversion before differencing. No negative or non-finite meters."""
    if unit not in UNITS or isinstance(value, bool) or len(str(value)) > 40:
        raise ValueError("Invalid import register")
    try:
        result = Decimal(str(value)) * UNITS[unit]
    except DecimalException:
        raise ValueError("Invalid import register") from None
    if not result.is_finite() or not 0 <= result <= Decimal("1000000000"):
        raise ValueError("Invalid import register")
    return result


def _text(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 64:
        return "unknown"
    return value.strip()


def _available(row):
    row = row or {}
    if str(row.get("state", "")).lower() in ("", "unknown", "unavailable", "none"):
        return None
    return row.get("attributes") or {}


def provenance(metadata, context_source):
    """Reduce optional fork metadata to an allow-listed snapshot.

    Absent rows or attributes degrade to "unverified"/"unknown". Serial numbers
    are deliberately never copied; meter identity is presence-only.
    """
    metadata = metadata or {}
    version, keys, boot = (_available(metadata.get(k)) for k in ("version", "configuration", "boot"))
    interval = (keys or {}).get("MeterValueSampleInterval")
    profiles = (keys or {}).get("SupportedFeatureProfiles")
    configurable = (keys or {}).get("measurands_configurable")
    reported = boot is not None and any(boot.get(k) not in (None, "") for k in (
        "meter_type", "meter_serial_number"))
    protocol = _text(metadata["version"].get("state")) if version is not None else "unknown"
    return {
        "protocol_version": "unverified" if protocol == "unknown" else protocol,
        "subprotocol": _text((version or {}).get("subprotocol")),
        "transport": _text((version or {}).get("transport")),
        "feature_profiles": (sorted({_text(p) for p in profiles.split(",") if p.strip()})[:10]
                             if isinstance(profiles, str) and len(profiles) <= 256 else "unknown"),
        "meter_value_sample_interval_s": (
            int(interval) if isinstance(interval, str) and interval.isdigit()
            and 0 < int(interval) <= 86400 else "unknown"),
        "measurands_configurable": configurable if type(configurable) is bool else "unknown",
        "vendor": _text((boot or {}).get("charge_point_vendor")),
        "model": _text((boot or {}).get("charge_point_model")),
        "firmware": _text((boot or {}).get("firmware_version")),
        "meter_identity": "reported" if reported else "unavailable",
        "context_source": context_source if context_source in ("charger", "defaulted") else "unknown",
    }


def provenance_flags(snapshot):
    """Quality flags only; none of these change acceptance or eligibility."""
    flags = set()
    if snapshot["context_source"] == "defaulted":
        # Fork reports the charger sent no context; Sample.Periodic was filled in.
        flags.add("context_defaulted_by_integration")
    elif snapshot["context_source"] != "charger":
        flags.add("context_source_unknown")
    if snapshot["protocol_version"] == "unverified":
        flags.add("protocol_version_unverified")
    if snapshot["transport"] == "ws":
        flags.add("transport_unencrypted")
    elif snapshot["transport"] != "wss":
        flags.add("transport_unknown")
    if snapshot["meter_identity"] != "reported":
        flags.add("meter_identity_unavailable")
    return flags


def migrate(saved):
    """Schema 1 -> 2 -> 3 without dropping records.

    1 -> 2: old spans get explicit unknown provenance.
    2 -> 3: an empty ``export`` section (export shadow not yet observed).
    ``migrated_from_schema`` keeps the oldest schema this store came from.
    """
    if not isinstance(saved, dict) or saved.get("schema") not in (1, 2):
        return saved
    result = deepcopy(saved)
    if result["schema"] == 2:
        result["schema"] = SCHEMA
        result.setdefault("migrated_from_schema", 2)
        result["export"] = None
        return result
    unknown = provenance({}, None)
    for span in [*result.get("spans", []), *([result["current"]] if result.get("current") else [])]:
        if isinstance(span, dict):
            span.setdefault("provenance", deepcopy(unknown))
            span.setdefault("provenance_flags", sorted(
                provenance_flags(unknown) | {"provenance_not_recorded"}))
            span.setdefault("provenance_changed", False)
    result["schema"] = 2
    result["migrated_from_schema"] = 1
    return migrate(result)


def transaction(value):
    text = str(value).strip()
    if text.lower() in {"", "0", "none", "unknown", "unavailable"} or len(text) > 128:
        return None
    return text


class ImportShadowLedger:
    """Restart-safe bounded observation journal. Deliberately no session API."""

    def __init__(self, binding, saved=None):
        self.binding = deepcopy(binding)
        self.data = {"schema": SCHEMA, "binding": deepcopy(binding), "sequence": 0,
                     "events": [], "spans": [], "current": None,
                     "journal_trimmed": False, "spans_trimmed": False, "export": None}
        if saved is not None:
            # Validate before installing state; never rewrite an unknown schema.
            saved = migrate(saved)
            if (not isinstance(saved, dict) or saved.get("schema") != SCHEMA
                    or saved.get("binding") != binding
                    or type(saved.get("sequence")) is not int or saved["sequence"] < 0
                    or not isinstance(saved.get("events"), list)
                    or len(saved["events"]) > MAX_EVENTS
                    or not isinstance(saved.get("spans"), list)
                    or len(saved["spans"]) > MAX_SPANS
                    or "current" not in saved
                    or type(saved.get("journal_trimmed")) is not bool
                    or type(saved.get("spans_trimmed")) is not bool
                    or "export" not in saved):
                raise ValueError("Invalid or incompatible OCPP shadow store")
            if saved["export"] is not None:
                # The export section shares this store; validate it before any rewrite.
                from .ocpp_export_shadow_ledger import validate_section
                validate_section(saved["export"])
            for row in saved["events"]:
                if not isinstance(row, dict) or not isinstance(row.get("kind"), str):
                    raise ValueError("Invalid OCPP shadow journal")
                instant(row["observed_at"])
            for span in [*saved["spans"], *([saved["current"]] if saved.get("current") else [])]:
                if (not isinstance(span, dict) or not isinstance(span.get("span_id"), str)
                        or not transaction(span.get("native_transaction_id"))
                        or type(span.get("sample_count")) is not int or span["sample_count"] < 1
                        or not isinstance(span.get("provenance"), dict)
                        or not isinstance(span.get("provenance_flags"), list)
                        or type(span.get("provenance_changed")) is not bool):
                    raise ValueError("Invalid OCPP shadow span")
                energy(span["observed_import_kwh"], "kWh")
                instant(span["first_meter_ha_updated_at"])
                instant(span["last_meter_ha_updated_at"])
            self.data = deepcopy(saved)
        self.context = None
        self.barrier = None
        self.last = None
        self.last_event = None
        self.flags = {"observation_not_started"}
        self.provenance = provenance({}, None)
        self.span_provenance = None
        self.restart_pending = True

    def event(self, kind, now, **values):
        self.data["events"].append({"kind": kind, "observed_at": now, **values})
        if len(self.data["events"]) > MAX_EVENTS:
            self.data["events"] = self.data["events"][-MAX_EVENTS:]
            self.data["journal_trimmed"] = True

    def close_span(self, reason, now):
        current = self.data["current"]
        if current:
            current["observation_ended_at"] = now
            current["end_reason"] = reason
            self.data["spans"].append(current)
            if len(self.data["spans"]) > MAX_SPANS:
                self.data["spans"] = self.data["spans"][-MAX_SPANS:]
                self.data["spans_trimmed"] = True
            self.data["current"] = None
            self.event("observation_boundary", now, reason=reason)
        self.last = None

    def observe(self, snapshot, now):
        """snapshot has status/transaction/import rows; import times are HA times."""
        clock = instant(now)
        if self.restart_pending:
            self.close_span("restart_gap", now)
            self.event("observer_started", now)
            self.barrier = clock
            self.restart_pending = False
        meter = snapshot.get("import", {})
        self.provenance = provenance(snapshot.get("metadata"), meter.get("context_source"))
        status = snapshot.get("status", {}).get("value")
        tx = transaction(snapshot.get("transaction", {}).get("value"))
        context = (status, tx)
        if context != self.context:
            # Suspend/resume with the SAME tx is not a new transaction. Retain
            # the span, but never attribute a meter predating an identity change.
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
        try:
            timestamp = instant(meter["ha_updated_at"])
            value = energy(meter["value"], meter.get("unit"))
            age = (clock - timestamp).total_seconds()
            # context_source only adds flags; it never relaxes this requirement.
            if meter.get("restored") or meter.get("context") != "Sample.Periodic":
                raise ValueError("Unverified meter context")
            if age < -5 or age > MAX_AGE_SECONDS:
                raise ValueError("Stale or future meter")
        except (KeyError, TypeError, ValueError):
            self.close_span("invalid_or_stale_meter", now)
            self.flags = {"invalid_or_stale_meter"}
            self.barrier = clock
            return
        if self.barrier is not None and timestamp <= self.barrier:
            self.flags = {"awaiting_fresh_baseline"}
            return
        token = (timestamp.isoformat(), str(value))
        if self.last and timestamp <= instant(self.last["ha_updated_at"]):
            if token == self.last_event:
                return
            self.close_span("out_of_order_or_conflicting_meter", now)
            self.barrier = clock
            self.flags = {"out_of_order_or_conflicting_meter"}
            return
        if self.last:
            gap = (timestamp - instant(self.last["ha_updated_at"])).total_seconds()
            if value < Decimal(self.last["kwh"]) or gap > MAX_AGE_SECONDS:
                reason = "counter_decreased" if value < Decimal(self.last["kwh"]) else "meter_gap"
                self.close_span(reason, now)
                self.event("excluded_delta", now, reason=reason)
        current = self.data["current"]
        flags = provenance_flags(self.provenance)
        if current is None:
            self.data["sequence"] += 1
            key = json.dumps([self.binding, self.data["sequence"], tx, now], sort_keys=True)
            current = self.data["current"] = {
                "span_id": "ocpp-shadow-" + hashlib.sha256(key.encode()).hexdigest()[:24],
                "native_transaction_id": tx, "observed_at": now,
                "first_meter_ha_updated_at": timestamp.isoformat(),
                "last_meter_ha_updated_at": timestamp.isoformat(),
                "observed_import_kwh": "0", "sample_count": 0,
                "partial_start": True, "source_timestamp": None,
                "billing_eligible": False, "settlement_owner": False,
                "protocol_version": self.provenance["protocol_version"],
                "provenance": deepcopy(self.provenance),
                "provenance_flags": sorted(flags), "provenance_changed": False,
            }
            self.span_provenance = deepcopy(self.provenance)
        else:
            previous = self.span_provenance or current["provenance"]
            if previous != self.provenance:
                # Journal only; the span is NOT split, its opening snapshot is kept,
                # and its flags accumulate conservatively.
                changed = sorted(k for k in self.provenance if previous.get(k) != self.provenance[k])
                self.event("provenance_changed", now, span_id=current["span_id"],
                           changed_fields=changed,
                           previous={k: deepcopy(previous.get(k)) for k in changed},
                           current={k: deepcopy(self.provenance[k]) for k in changed})
                current["provenance_changed"] = True
                current["provenance_flags"] = sorted(set(current["provenance_flags"]) | flags)
                self.span_provenance = deepcopy(self.provenance)
            if self.last:
                current["observed_import_kwh"] = str(
                    Decimal(current["observed_import_kwh"]) + value - Decimal(self.last["kwh"]))
        current["sample_count"] += 1
        current["last_meter_ha_updated_at"] = timestamp.isoformat()
        self.last = {"ha_updated_at": timestamp.isoformat(), "kwh": str(value)}
        self.last_event = token
        self.flags = set()
        self.event("import_sample", now, span_id=current["span_id"],
                   native_transaction_id=tx, original_value=str(meter["value"]),
                   original_unit=meter["unit"], normalized_kwh=str(value),
                   ha_updated_at=timestamp.isoformat(), source_timestamp=None,
                   context=meter["context"],
                   context_source=self.provenance["context_source"])

    def summary(self):
        current = self.data["current"]
        return {
            "state": "observing" if current and not self.flags else "waiting",
            "quality_flags": sorted(self.flags | provenance_flags(self.provenance) | {
                                                "entity_observation_only",
                                                "partial_import_span", "export_not_observed",
                                                "source_timestamp_unavailable"}),
            "provenance": deepcopy(self.provenance),
            "current_span": deepcopy(current),
            "previous_span": deepcopy(self.data["spans"][-1]) if self.data["spans"] else None,
            "retained_span_count": len(self.data["spans"]),
            "journal_trimmed": self.data["journal_trimmed"],
            "spans_trimmed": self.data["spans_trimmed"],
            "billing_eligible": False, "settlement_owner": False,
            "payment_control": False, "charger_control": False,
            "export_kwh": None, "net_cost_aud": None,
        }
