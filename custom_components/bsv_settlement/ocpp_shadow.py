"""Read-only OCPP entity observer; isolated store and no financial interfaces."""
from copy import deepcopy
from datetime import timedelta
from decimal import Decimal, DecimalException
import logging
import re

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .records import VersionedStore, check_keys
from .ocpp_export_shadow_ledger import ExportShadowLedger, grading_rules
from .ocpp_lifecycle import SETTLEMENT_OWNER, LifecycleTracker, assemble
from .ocpp_shadow_ledger import ImportShadowLedger, energy, instant, provenance_flags
from .recorder import RecorderMonitor

_LOGGER = logging.getLogger(__name__)
METRICS = {"import": "energy_active_import_register",
           "transaction": "transaction_id", "status": "status_connector"}
# Optional charger-level (connector 0) diagnostics published by the purcell-lab
# OCPP fork. Upstream builds lack them; provenance then stays unverified.
METADATA = {"version": "version_ocpp", "configuration": "configuration_keys",
            "boot": "boot_notification"}
METADATA_FIELDS = {"version": "ocpp_version_entity",
                   "configuration": "configuration_keys_entity",
                   "boot": "boot_notification_entity"}
# Optional derived-export sensors of the same connector (purcell-lab fork).
EXPORT = {"register": "energy_active_export_register", "flow": "flow_direction",
          "session": "energy_session_export"}
EXPORT_FIELDS = {"register": "export_register_entity", "flow": "flow_direction_entity",
                 "session": "session_export_entity"}
REFERENCE_FIELD = "export_reference_entity"
GRADING_FIELDS = {"min_energy_kwh": "export_min_energy_kwh",
                  "tolerance_pct": "export_tolerance_pct", "wide_pct": "export_wide_pct"}
STORE_VERSION = 3
# Native session lifecycle (shadow only). Optional connector inputs found by exact
# unique ID; the idTag value goes straight into the tracker, which keeps a one-way
# reference only.
LIFECYCLE_INPUTS = {"id_tag": "id_tag", "soc": "soc"}
LIFECYCLE_SCHEMA = 1
LIFECYCLE_KEYS = {"schema", "recorded_since", "saved_at", "tracker", "spans", "spans_trimmed",
                  "late_final"}
MAX_LIFECYCLE_SPANS = 400
REGISTER_BUFFER_S = 1800
SPAN_FIELDS = ("span_id", "native_transaction_id", "first_meter_ha_updated_at",
               "last_meter_ha_updated_at", "observation_ended_at", "end_reason",
               "observed_import_kwh", "estimate_kwh", "lower_kwh", "upper_kwh", "grade",
               "source_label")


def source_binding(hass, sources):
    """Validate pinned metric identities and same OCPP connector device.

    Existing v0.12.0 unique IDs share a station/connector prefix. Fail rather
    than guess if another upstream layout is encountered.
    """
    if set(sources) != set(METRICS) or len(set(sources.values())) != 3:
        raise ValueError("Select three distinct OCPP sources")
    registry = er.async_get(hass)
    binding, scopes = {}, set()
    for key, entity_id in sources.items():
        row = registry.async_get(entity_id)
        suffix = "." + METRICS[key] + ".sensor"
        if (row is None or row.platform != "ocpp" or row.domain != "sensor"
                or row.disabled_by is not None or not row.device_id or not row.config_entry_id
                or not row.unique_id.startswith("ocpp.") or not row.unique_id.endswith(suffix)):
            raise ValueError("Sources must be enabled OCPP connector metrics")
        scope = (row.config_entry_id, row.device_id, row.unique_id[:-len(suffix)])
        scopes.add(scope)
        binding[key] = {"entity_id": entity_id, "unique_id": row.unique_id,
                        "config_entry_id": row.config_entry_id, "device_id": row.device_id}
    if len(scopes) != 1:
        raise ValueError("OCPP sources must share the same connector scope")
    return binding


def charger_prefix(binding):
    """`ocpp.<cpid>` of a validated measurand binding, without any connN scope."""
    scope = binding["import"]["unique_id"][:-len("." + METRICS["import"] + ".sensor")]
    head, _, tail = scope.rpartition(".")
    return head if head != "ocpp" and re.fullmatch(r"conn\d+", tail) else scope


def metadata_binding(hass, binding, selected):
    """Validate optional metadata sensors against the measurands' charger.

    Same OCPP config entry and exact `ocpp.<cpid>.<slug>.sensor` unique ID are
    required; the device may be the charger rather than the connector device.
    """
    registry = er.async_get(hass)
    prefix, entry_id = charger_prefix(binding), binding["import"]["config_entry_id"]
    result = {}
    for key, entity_id in selected.items():
        if key not in METADATA:
            raise ValueError("Unknown OCPP metadata source")
        if not entity_id:
            continue
        row = registry.async_get(entity_id)
        if (row is None or row.platform != "ocpp" or row.domain != "sensor"
                or row.disabled_by is not None or row.config_entry_id != entry_id
                or row.unique_id != f"{prefix}.{METADATA[key]}.sensor"):
            raise ValueError("Metadata must be enabled sensors of the same OCPP charger")
        result[key] = {"entity_id": entity_id, "unique_id": row.unique_id,
                       "config_entry_id": row.config_entry_id, "device_id": row.device_id}
    return result


def suggest_metadata(hass, binding):
    """Discover metadata entity IDs by exact unique ID; never guess by name."""
    registry, prefix = er.async_get(hass), charger_prefix(binding)
    found = {}
    for key, slug in METADATA.items():
        entity_id = registry.async_get_entity_id("sensor", "ocpp", f"{prefix}.{slug}.sensor")
        try:
            if entity_id and metadata_binding(hass, binding, {key: entity_id}):
                found[key] = entity_id
        except ValueError:
            pass
    return found


def measurand_scope(binding):
    """`ocpp.<cpid>[.connN]` shared by the bound measurands."""
    return binding["import"]["unique_id"][:-len("." + METRICS["import"] + ".sensor")]


def export_binding(hass, binding, selected):
    """Validate optional derived-export sensors against the bound connector.

    Exact `<measurand scope>.<slug>.sensor` unique IDs in the same OCPP config
    entry are required. The register and flow direction are bound together;
    the per-transaction session export is an optional cross-check.
    """
    registry = er.async_get(hass)
    scope, entry_id = measurand_scope(binding), binding["import"]["config_entry_id"]
    if set(selected) - set(EXPORT):
        raise ValueError("Unknown OCPP export source")
    chosen = {k: v for k, v in selected.items() if v}
    if not chosen:
        return {}
    if not {"register", "flow"} <= set(chosen) or len(set(chosen.values())) != len(chosen):
        raise ValueError("Bind the export register and flow direction together")
    bound = {v["entity_id"] for v in binding.values()}
    result = {}
    for key, entity_id in chosen.items():
        row = registry.async_get(entity_id)
        if (entity_id in bound or row is None or row.platform != "ocpp"
                or row.domain != "sensor" or row.disabled_by is not None
                or row.config_entry_id != entry_id
                or row.unique_id != f"{scope}.{EXPORT[key]}.sensor"):
            raise ValueError("Export sources must be enabled sensors of the same OCPP connector")
        result[key] = {"entity_id": entity_id, "unique_id": row.unique_id,
                       "config_entry_id": row.config_entry_id, "device_id": row.device_id}
    return result


def suggest_export(hass, binding):
    """Discover export sensors by exact unique ID; never guess by name."""
    registry, scope = er.async_get(hass), measurand_scope(binding)
    found = {}
    for key, slug in EXPORT.items():
        entity_id = registry.async_get_entity_id("sensor", "ocpp", f"{scope}.{slug}.sensor")
        row = registry.async_get(entity_id) if entity_id else None
        if row and row.disabled_by is None and row.config_entry_id == binding["import"]["config_entry_id"]:
            found[key] = entity_id
    return found if {"register", "flow"} <= set(found) else {}


def session_scope(binding):
    """(charger, connector) of the native session identity, from the pinned binding.

    A scope without ``connN`` is the single-connector layout: connector 1.
    """
    scope = measurand_scope(binding)
    head, _, tail = scope.rpartition(".")
    if head != "ocpp" and re.fullmatch(r"conn\d+", tail):
        return head[len("ocpp."):], int(tail[4:])
    return scope[len("ocpp."):], 1


def lifecycle_inputs(hass, binding):
    """Optional lifecycle sensors of the bound connector by exact unique ID; never by name."""
    registry, scope = er.async_get(hass), measurand_scope(binding)
    bound = {v["entity_id"] for v in binding.values()}
    found = {}
    for key, slug in LIFECYCLE_INPUTS.items():
        entity_id = registry.async_get_entity_id("sensor", "ocpp", f"{scope}.{slug}.sensor")
        row = registry.async_get(entity_id) if entity_id else None
        if (row and entity_id not in bound and row.disabled_by is None
                and row.config_entry_id == binding["import"]["config_entry_id"]):
            found[key] = entity_id
    return found


def lifecycle_view(row):
    """Compact, attribute-sized view of one assembled native session; references only."""
    flags = set(row["quality_flags"])
    if row["ended_at"] is None:
        state = "active"
    elif flags & {"late_final_reading_unattributed", "late_or_duplicate_transaction_event"}:
        state = "late_final"
    elif row["end_reason"] == "superseded_without_stop":
        state = "superseded"
    elif flags & {"charger_fault", "charger_fault_at_end"}:
        state = "faulted"
    else:
        state = "stopped"
    refs = row["untrusted_metadata"]["id_tag_refs"]
    imp, exp = row["import"], row["export"]
    return {
        "identity": dict(row["identity"]), "lifecycle_state": state,
        "started_at": row["started_at"], "ended_at": row["ended_at"],
        "end_reason": row["end_reason"], "finishing_at": row["finishing_at"],
        "start_observed": row["start_observed"],
        "restart_continuity": "continued_across_restart" if row["ha_restarts"] else "no_restart",
        "ha_restart_count": len(row["ha_restarts"]), "ha_restarts": row["ha_restarts"][-3:],
        "id_tag_ref": refs[-1] if refs else None,
        "id_tag_changes": row["untrusted_metadata"]["id_tag_changes"],
        "import_observed_kwh": imp["observed_kwh"], "import_span_count": imp["span_count"],
        "import_reconciliation": imp["reconciliation_outcomes"],
        "export_observed_kwh": exp["observed_kwh"], "export_span_count": exp["span_count"],
        "export_lower_kwh": exp.get("observed_lower_kwh"),
        "export_upper_kwh": exp.get("observed_upper_kwh"), "export_grades": exp.get("grades"),
        "export_reconciliation": exp["reconciliation_outcomes"],
        "late_final_import_kwh": row["late_final_import_kwh"],
        "quality_flags": row["quality_flags"],
        "vehicle_evidence": (row["vehicle_evidence"] or {}).get("assessment"),
        "vehicle_attribution": "unresolved", "billing_eligible": False,
        "settlement_owner": SETTLEMENT_OWNER,
    }


def validate_lifecycle(saved, charger_id, connector_id):
    """Validated lifecycle store data, or None when nothing was recorded. Raises ValueError."""
    if saved is None:
        return None
    check_keys("ocpp_lifecycle", saved)
    if (set(saved) != LIFECYCLE_KEYS or saved["schema"] != LIFECYCLE_SCHEMA
            or type(saved["spans_trimmed"]) is not bool
            or not isinstance(saved["spans"], dict) or set(saved["spans"]) != {"import", "export"}
            or not isinstance(saved["late_final"], dict)):
        raise ValueError("Invalid OCPP lifecycle store")
    instant(saved["recorded_since"])
    instant(saved["saved_at"])
    tracker = LifecycleTracker.restore(saved["tracker"], charger_id, connector_id)
    for direction, key in (("import", "observed_import_kwh"), ("export", "estimate_kwh")):
        rows = saved["spans"][direction]
        if not isinstance(rows, dict) or len(rows) > MAX_LIFECYCLE_SPANS:
            raise ValueError("Invalid OCPP lifecycle spans")
        for span_id, span in rows.items():
            if (not isinstance(span, dict) or set(span) - set(SPAN_FIELDS)
                    or span.get("span_id") != span_id
                    or not isinstance(span.get("native_transaction_id"), str)):
                raise ValueError("Invalid OCPP lifecycle span")
            energy(span[key], "kWh")
            instant(span["first_meter_ha_updated_at"])
    for value in saved["late_final"].values():
        energy(value, "kWh")
    return {**deepcopy(saved), "tracker": tracker}


def reference_binding(hass, binding, exported, entity_id):
    """Optional independent cumulative energy counter, diagnostic only.

    It must be a registered, enabled sensor outside the OCPP integration with
    an HA energy unit, and is only meaningful once export is bound.
    """
    if not entity_id:
        return None
    if not exported:
        raise ValueError("Bind the export register before a reference")
    row = er.async_get(hass).async_get(entity_id)
    state = hass.states.get(entity_id)
    if (row is None or row.domain != "sensor" or row.platform == "ocpp"
            or row.disabled_by is not None or state is None
            or state.attributes.get("unit_of_measurement") not in EnergyConverter.VALID_UNITS
            or entity_id in {v["entity_id"] for v in [*binding.values(), *exported.values()]}):
        raise ValueError("Reference must be an enabled non-OCPP energy sensor")
    return {"entity_id": entity_id, "unique_id": row.unique_id, "platform": row.platform}


def reference_kwh(state):
    """Unit-converted reference reading as a kWh string, or None."""
    if state is None:
        return None
    unit = state.attributes.get("unit_of_measurement")
    if unit not in EnergyConverter.VALID_UNITS:
        return None
    try:
        ratio = Decimal(str(EnergyConverter.convert(1.0, unit, "kWh")))
        value = Decimal(str(state.state)) * ratio
    except (DecimalException, ValueError, TypeError):
        return None
    try:
        return str(energy(value, "kWh"))
    except ValueError:
        return None


class ShadowStore(VersionedStore):
    """Schemas 1 and 2 are validated in full before HA rewrites the file."""

    def __init__(self, hass, entry_id, binding):
        super().__init__(hass, "ocpp_shadow", entry_id)
        self.binding = binding

    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        if old_major_version not in (1, 2) or not isinstance(old_data, dict) or (
                old_data.get("schema") != old_major_version):
            raise ValueError("Unsupported OCPP shadow store version")
        return ImportShadowLedger(self.binding, old_data).data


class OCPPShadowCoordinator(DataUpdateCoordinator):
    mode = "ocpp_import_shadow"

    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name="OCPP import shadow", config_entry=entry,
                         update_interval=timedelta(seconds=15))
        self.entry = entry
        self.sources = {k: entry.data[k + "_entity"] for k in METRICS}
        self.metadata = {k: v["entity_id"]
                         for k, v in entry.options.get("metadata_binding", {}).items()}
        self.export_binding = entry.options.get("export_binding") or {}
        self.export_sources = {k: v["entity_id"] for k, v in self.export_binding.items()}
        self.reference = entry.options.get("export_reference_binding") or None
        self.grading = grading_rules(entry.options.get("export_grading"))
        self.store = ShadowStore(hass, entry.entry_id, entry.data["source_binding"])
        self.ledger = None
        self.export = None
        self.cancel_listener = None
        self.binding_error = False
        self.metadata_error = False
        self.export_error = False
        # Read-only readiness and legacy reconciliation; separate store.
        self.recorder = RecorderMonitor(hass, entry)
        # Native session lifecycle; separate store, shadow only.
        self.lifecycle_store = VersionedStore(hass, "ocpp_lifecycle", entry.entry_id)
        self.lifecycle = None
        self.lifecycle_state = "not_loaded"
        self.lifecycle_sources = {}
        self.lifecycle_spans = {"import": {}, "export": {}}
        self.lifecycle_meta = {}
        self.register = []
        self.watched = set()

    async def load(self):
        binding = source_binding(self.hass, self.sources)
        if binding != self.entry.data["source_binding"]:
            raise ValueError("OCPP source identity changed; review configuration")
        ledger = ImportShadowLedger(binding, await self.store.async_load())
        export = None
        if ledger.data["export"] is not None or self.export_binding:
            # Same store, separate section: retained even while unbound.
            export = ExportShadowLedger(
                ledger.data["export"], self.export_binding or None, self.reference, self.grading)
            ledger.data["export"] = export.data
        self.ledger, self.export = ledger, export
        await self.recorder.load(dt_util.utcnow().isoformat())
        await self.load_lifecycle(binding)
        watched = [*self.sources.values(), *self.export_sources.values()]
        if self.reference:
            watched.append(self.reference["entity_id"])
        self.watched = set(watched)
        self.cancel_listener = async_track_state_change_event(
            self.hass, [*watched, *self.lifecycle_sources.values()], self._state_changed)
        self.observe()

    async def load_lifecycle(self, binding):
        """Refusal disables lifecycle tracking only; a refused file is never rewritten.

        A missing store (first run, or upgrade from an older release) starts
        recording now: earlier history is reported as not recorded, never rebuilt.
        """
        now = dt_util.utcnow().isoformat()
        charger_id, connector_id = session_scope(binding)
        try:
            saved = validate_lifecycle(await self.lifecycle_store.async_load(),
                                       charger_id, connector_id)
        except (ValueError, KeyError, TypeError, HomeAssistantError):
            _LOGGER.warning("OCPP lifecycle store refused; lifecycle tracking disabled")
            self.lifecycle, self.lifecycle_state = None, "store_refused"
            return
        self.lifecycle_sources = {"status": self.sources["status"],
                                  "transaction": self.sources["transaction"],
                                  **lifecycle_inputs(self.hass, binding)}
        if saved is None:
            self.lifecycle = LifecycleTracker(charger_id, connector_id)
            self.lifecycle_meta = {"recorded_since": now, "spans_trimmed": False, "late_final": {}}
        else:
            self.lifecycle = saved["tracker"]
            self.lifecycle_spans = saved["spans"]
            self.lifecycle_meta = {k: saved[k] for k in ("recorded_since", "spans_trimmed",
                                                         "late_final")}
            # A restart or reload is an outage from the last save, never a stop.
            self.lifecycle.resume(saved["saved_at"])
        self.lifecycle_state = "recording"
        try:
            self.resume_lifecycle(saved is not None, now)
        except Exception as exc:  # noqa: BLE001
            self.lifecycle_failed(exc)

    def resume_lifecycle(self, restored, now):
        """Feed entity values newer than the tracker has seen, oldest first."""
        seen = self.lifecycle.last_seen
        rows = []
        for key, entity_id in self.lifecycle_sources.items():
            state = self.hass.states.get(entity_id)
            if state is not None and (key not in seen or state.last_updated > instant(seen[key])):
                rows.append((state.last_updated, key, state))
        for _, key, state in sorted(rows, key=lambda row: row[0]):
            self.lifecycle.update(key, state.state, state.last_updated.isoformat())
        if self.lifecycle.outage is not None and restored:
            # Entities kept their values across a reload: the outage ends now.
            tx = self.hass.states.get(self.sources["transaction"])
            status = self.hass.states.get(self.sources["status"])
            if tx is not None and status is not None and all(
                    s.state.lower() not in ("unknown", "unavailable") for s in (tx, status)):
                self.lifecycle.update("transaction", tx.state, now)
        self.save_lifecycle()

    def lifecycle_data(self):
        return {"schema": LIFECYCLE_SCHEMA, "recorded_since": self.lifecycle_meta["recorded_since"],
                "saved_at": dt_util.utcnow().isoformat(),
                "tracker": self.lifecycle.state(), "spans": deepcopy(self.lifecycle_spans),
                "spans_trimmed": self.lifecycle_meta["spans_trimmed"],
                "late_final": dict(self.lifecycle_meta["late_final"])}

    def save_lifecycle(self):
        # Snapshot now: a later tracking fault leaves this last good state to be written.
        if self.lifecycle is not None:
            data = self.lifecycle_data()
            self.lifecycle_store.async_delay_save(lambda: data, 1)

    @callback
    def _state_changed(self, event):
        entity_id, state = event.data["entity_id"], event.data.get("new_state")
        if self.lifecycle is not None and not self.binding_error and state is not None:
            self.feed_lifecycle(entity_id, state)
        if entity_id in self.watched:
            self.observe()
            self.recorder.reconcile(self, dt_util.utcnow().isoformat())
        self.async_set_updated_data(self.summary())

    def lifecycle_failed(self, exc):
        # Diagnostic path must never break observation. No value or traceback is
        # logged: an input may be an idTag.
        _LOGGER.warning("OCPP lifecycle tracking failed (%s); observation continues",
                        type(exc).__name__)
        self.lifecycle, self.lifecycle_state = None, "failed"

    def feed_lifecycle(self, entity_id, state):
        try:
            for key, source in self.lifecycle_sources.items():
                if source == entity_id:
                    self.lifecycle.update(key, state.state, state.last_updated.isoformat())
                    self.save_lifecycle()
            if entity_id == self.sources["import"]:
                self.buffer_register(state)
        except Exception as exc:  # noqa: BLE001
            self.lifecycle_failed(exc)

    def buffer_register(self, state):
        """Recent import register readings, only to report a late final reading."""
        try:
            value = energy(state.state, state.attributes.get("unit_of_measurement"))
        except ValueError:
            return
        self.register.append((state.last_updated, value))
        limit = state.last_updated - timedelta(seconds=REGISTER_BUFFER_S)
        self.register = [row for row in self.register if row[0] >= limit]

    def collect_lifecycle_spans(self):
        """Closed shadow spans of retained sessions, kept beyond the ledgers' 50-span window."""
        if self.lifecycle is None:
            return
        try:
            self._collect_lifecycle_spans()
        except Exception as exc:  # noqa: BLE001
            self.lifecycle_failed(exc)

    def _collect_lifecycle_spans(self):
        txs = {s["transaction_id"] for s in self.lifecycle.sessions}
        changed = False
        for direction, spans in (("import", self.ledger.data["spans"]),
                                 ("export", self.export.data["spans"] if self.export else [])):
            index = self.lifecycle_spans[direction]
            for span in spans:
                if span.get("native_transaction_id") in txs and span["span_id"] not in index:
                    index[span["span_id"]] = {k: span[k] for k in SPAN_FIELDS if k in span}
                    changed = True
            for span_id in [k for k, v in index.items() if v["native_transaction_id"] not in txs]:
                del index[span_id]
                changed = True
            while len(index) > MAX_LIFECYCLE_SPANS:
                del index[next(iter(index))]
                self.lifecycle_meta["spans_trimmed"] = True
        if changed:
            self.save_lifecycle()

    def lifecycle_sessions(self, now):
        """Native sessions with attributed observed totals (diagnostic evidence only)."""
        if self.lifecycle is None:
            return []
        current = [s for s in (self.ledger.data["current"],
                               self.export.data["current"] if self.export else None) if s]
        results = self.recorder.ledger.data["results"] if self.recorder.ledger else ()
        rows = assemble(
            deepcopy(self.lifecycle.sessions),
            [*self.lifecycle_spans["import"].values(),
             *[s for s in current if "observed_import_kwh" in s]],
            [*self.lifecycle_spans["export"].values(),
             *[s for s in current if "estimate_kwh" in s]],
            results, window_end=now, import_register=self.register or None)
        late = self.lifecycle_meta["late_final"]
        txs = {row["transaction_id"] for row in rows}
        for tx in [tx for tx in late if tx not in txs]:
            del late[tx]
        for row in rows:
            tx = row["transaction_id"]
            if row["late_final_import_kwh"] is not None:
                if late.get(tx) != row["late_final_import_kwh"]:
                    late[tx] = row["late_final_import_kwh"]
                    self.save_lifecycle()
            elif tx in late:
                # The in-memory register window has passed; keep what was reported.
                row["late_final_import_kwh"] = late[tx]
                row["quality_flags"] = sorted({*row["quality_flags"],
                                               "late_final_reading_unattributed"})
        return rows

    def lifecycle_summary(self, now):
        common = {"billing_eligible": False, "settlement_owner": SETTLEMENT_OWNER,
                  "selector_implemented": False, "vehicle_attribution": "unresolved",
                  "payment_control": False, "charger_control": False}
        try:
            rows = self.lifecycle_sessions(now)
        except Exception as exc:  # noqa: BLE001
            self.lifecycle_failed(exc)
        if self.lifecycle is None:
            return {**common, "state": self.lifecycle_state, "current_session": None,
                    "last_session": None}
        tracker = self.lifecycle
        closed = [row for row in rows if row["ended_at"] is not None]
        return {
            **common,
            "state": "binding_changed" if self.binding_error else self.lifecycle_state,
            "recorded_since": self.lifecycle_meta["recorded_since"],
            "history_before_recorded_since": "not_recorded",
            "inputs": {k: ("bound" if k in self.lifecycle_sources else "not_found")
                       for k in ("status", "transaction", *LIFECYCLE_INPUTS)},
            "outage_open": tracker.outage is not None, "outage_since": tracker.outage,
            "current_session": lifecycle_view(rows[-1]) if tracker.current is not None else None,
            "last_session": lifecycle_view(closed[-1]) if closed else None,
            "session_count": len(rows), "sessions_trimmed": tracker.sessions_trimmed,
            "spans_trimmed": self.lifecycle_meta["spans_trimmed"],
        }

    @callback
    def observe(self):
        now = dt_util.utcnow().isoformat()
        try:
            if source_binding(self.hass, self.sources) != self.entry.data["source_binding"]:
                raise ValueError("Changed binding")
        except ValueError:
            self.binding_error = True
            self.ledger.close_span("source_binding_changed", now)
            self.ledger.barrier = dt_util.parse_datetime(now)
            if self.export is not None:
                self.export.close_span("source_binding_changed", now)
                self.export.barrier = self.ledger.barrier
            self.store.async_delay_save(lambda: deepcopy(self.ledger.data), 1)
            return
        self.binding_error = False
        snapshot = {}
        for key, entity_id in self.sources.items():
            state = self.hass.states.get(entity_id)
            if state is not None:
                snapshot[key] = {
                    "value": state.state, "unit": state.attributes.get("unit_of_measurement"),
                    "context": state.attributes.get("context"),
                    "context_source": state.attributes.get("context_source"),
                    "restored": state.attributes.get("restored", False),
                    "ha_updated_at": state.last_updated.isoformat(),
                }
        snapshot["metadata"] = self.metadata_snapshot()
        before = deepcopy(self.ledger.data)
        self.ledger.observe(snapshot, now)
        if self.export is not None:
            self.export.observe(self.export_snapshot(snapshot), now)
        if before != self.ledger.data:
            self.store.async_delay_save(lambda: deepcopy(self.ledger.data), 1)
        self.collect_lifecycle_spans()

    def export_snapshot(self, snapshot):
        """Rows for the export ledger; read-only, unit conversion happens here."""
        result = {"status": snapshot.get("status", {}), "transaction": snapshot.get("transaction", {}),
                  "provenance_flags": sorted(provenance_flags(self.ledger.provenance))}
        if not self.export_binding:
            return result
        try:
            self.export_error = export_binding(
                self.hass, self.entry.data["source_binding"], self.export_sources
            ) != self.export_binding
        except ValueError:
            self.export_error = True
        if self.export_error:
            return {**result, "binding_valid": False}
        for key, entity_id in self.export_sources.items():
            state = self.hass.states.get(entity_id)
            if state is None:
                continue
            row = {"value": state.state, "unit": state.attributes.get("unit_of_measurement"),
                   "ha_updated_at": state.last_updated.isoformat(),
                   "restored": state.attributes.get("restored", False)}
            if key == "register":
                row["attributes"] = dict(state.attributes)
            result[{"register": "export", "flow": "flow", "session": "session_export"}[key]] = row
        if self.reference:
            # Degrade, never fail: a moved reference is ignored, not trusted.
            entity_id = self.reference["entity_id"]
            row = er.async_get(self.hass).async_get(entity_id)
            if row is not None and row.unique_id == self.reference["unique_id"] and row.disabled_by is None:
                state = self.hass.states.get(entity_id)
                result["reference"] = {"kwh": reference_kwh(state)}
        return result

    def metadata_snapshot(self):
        """Degrade, never fail: a changed metadata identity is simply ignored."""
        try:
            self.metadata_error = metadata_binding(
                self.hass, self.entry.data["source_binding"], self.metadata
            ) != self.entry.options.get("metadata_binding", {})
        except ValueError:
            self.metadata_error = True
        if self.metadata_error:
            return {}
        # Raw attributes stay in memory; the ledger copies an allow-list only.
        return {key: {"state": state.state, "attributes": dict(state.attributes)}
                for key, entity_id in self.metadata.items()
                if (state := self.hass.states.get(entity_id)) is not None}

    def summary(self):
        result = self.ledger.summary()
        if self.binding_error:
            result["state"] = "incompatible"
            result["quality_flags"].append("source_binding_changed")
        if self.metadata_error:
            result["quality_flags"] = sorted({*result["quality_flags"], "metadata_binding_changed"})
        elif not self.metadata:
            result["quality_flags"] = sorted({*result["quality_flags"], "metadata_not_bound"})
        export = (self.export.summary() if self.export is not None
                  else ExportShadowLedger(grading=self.grading).summary())
        if self.export_error:
            export["flags"] = sorted({*export["flags"], "export_binding_changed"})
        now = dt_util.utcnow().isoformat()
        return {**result, "export_shadow": export, "mode": self.mode,
                "recorder": self.recorder.summary(self, now),
                "lifecycle": self.lifecycle_summary(now), "updated_at": now}

    async def _async_update_data(self):
        self.observe()
        self.recorder.reconcile(self, dt_util.utcnow().isoformat())
        return self.summary()

    async def execute(self, action, data, approving_user_id=None):
        if action != "refresh":
            raise HomeAssistantError("OCPP shadow is read-only; no wallet or charger actions")
        await self.async_request_refresh()
        return self.data

    async def close(self):
        if self.cancel_listener:
            self.cancel_listener()
            self.cancel_listener = None
        if self.ledger is not None:
            await self.store.async_save(deepcopy(self.ledger.data))
            await self.recorder.close()
        if self.lifecycle is not None:
            await self.lifecycle_store.async_save(self.lifecycle_data())
