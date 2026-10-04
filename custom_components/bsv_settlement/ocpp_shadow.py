"""Read-only OCPP entity observer; isolated store and no financial interfaces."""
from copy import deepcopy
from datetime import timedelta
import logging
import re

from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .ocpp_shadow_ledger import ImportShadowLedger, energy

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
STORE_VERSION = 2


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


class ShadowStore(Store):
    """Schema 1 -> 2 is validated in full before HA rewrites the file."""

    def __init__(self, hass, entry_id, binding):
        super().__init__(hass, STORE_VERSION, f"{DOMAIN}.ocpp_shadow.{entry_id}")
        self.binding = binding

    async def _async_migrate_func(self, old_major_version, old_minor_version, old_data):
        if old_major_version != 1:
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
        self.store = ShadowStore(hass, entry.entry_id, entry.data["source_binding"])
        self.ledger = None
        self.cancel_listener = None
        self.binding_error = False
        self.metadata_error = False

    async def load(self):
        binding = source_binding(self.hass, self.sources)
        if binding != self.entry.data["source_binding"]:
            raise ValueError("OCPP source identity changed; review configuration")
        self.ledger = ImportShadowLedger(binding, await self.store.async_load())
        self.cancel_listener = async_track_state_change_event(
            self.hass, list(self.sources.values()), self._state_changed)
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
        if before != self.ledger.data:
            self.store.async_delay_save(lambda: deepcopy(self.ledger.data), 1)

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
        return {**result, "mode": self.mode, "updated_at": dt_util.utcnow().isoformat()}

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
