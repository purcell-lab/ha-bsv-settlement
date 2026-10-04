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
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .const import DOMAIN
from .ocpp_export_shadow_ledger import ExportShadowLedger, grading_rules
from .ocpp_shadow_ledger import ImportShadowLedger, energy, provenance_flags

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


class ShadowStore(Store):
    """Schemas 1 and 2 are validated in full before HA rewrites the file."""

    def __init__(self, hass, entry_id, binding):
        super().__init__(hass, STORE_VERSION, f"{DOMAIN}.ocpp_shadow.{entry_id}")
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
        watched = [*self.sources.values(), *self.export_sources.values()]
        if self.reference:
            watched.append(self.reference["entity_id"])
        self.cancel_listener = async_track_state_change_event(
            self.hass, watched, self._state_changed)
        self.observe()

    @callback
    def _state_changed(self, event):
        self.observe()
        self.async_set_updated_data(self.summary())

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
        return {**result, "export_shadow": export, "mode": self.mode,
                "updated_at": dt_util.utcnow().isoformat()}

    async def _async_update_data(self):
        self.observe()
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
