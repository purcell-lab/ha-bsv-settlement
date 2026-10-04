"""Read-only OCPP entity observer; isolated store and no financial interfaces."""
from copy import deepcopy
from datetime import timedelta
import logging

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


class OCPPShadowCoordinator(DataUpdateCoordinator):
    mode = "ocpp_import_shadow"

    def __init__(self, hass, entry):
        super().__init__(hass, _LOGGER, name="OCPP import shadow", config_entry=entry,
                         update_interval=timedelta(seconds=15))
        self.entry = entry
        self.sources = {k: entry.data[k + "_entity"] for k in METRICS}
        self.store = Store(hass, 1, f"{DOMAIN}.ocpp_shadow.{entry.entry_id}")
        self.ledger = None
        self.cancel_listener = None
        self.binding_error = False

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
                    "restored": state.attributes.get("restored", False),
                    "ha_updated_at": state.last_updated.isoformat(),
                }
        before = deepcopy(self.ledger.data)
        self.ledger.observe(snapshot, now)
        if before != self.ledger.data:
            self.store.async_delay_save(lambda: deepcopy(self.ledger.data), 1)

    def summary(self):
        result = self.ledger.summary()
        if self.binding_error:
            result["state"] = "incompatible"
            result["quality_flags"].append("source_binding_changed")
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
