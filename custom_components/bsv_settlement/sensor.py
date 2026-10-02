"""Five fixed sensors for the latest session, without full ledger attributes."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN

SENSORS = [
    ("session_import_energy", "Session import energy", "kWh"),
    ("session_export_energy", "Session export energy", "kWh"),
    ("session_net_amount", "Session net amount", "AUD"),
    ("settlement_status", "Settlement status", None),
    ("settlement_amount", "Settlement amount", "sat"),
]


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(SettlementSensor(coordinator, entry, *spec) for spec in SENSORS)


class SettlementSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, name, unit):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)},
                                  "name": "BSV Settlement (Mock)",
                                  "manufacturer": "Proof of concept",
                                  "model": "Simulated settlement only"}

    @property
    def session(self):
        data = self.coordinator.data or {}
        return data.get("sessions", {}).get(data.get("latest"), {})

    @property
    def native_value(self):
        session = self.session
        remote = session.get("remote", {})
        if self.key == "settlement_status":
            return remote.get("state", "metering" if session else "idle")
        if self.key == "session_import_energy":
            return sum(i["import_wh"] for i in session.get("intervals", [])) / 1000
        if self.key == "session_export_energy":
            return sum(i["export_wh"] for i in session.get("intervals", [])) / 1000
        if self.key == "session_net_amount":
            amount = session.get("payload", {}).get("net_amount_minor")
            return amount / 100 if amount is not None else None
        return remote.get("amount_sats")

    @property
    def extra_state_attributes(self):
        remote = self.session.get("remote", {})
        return {"mode": "mock", "session_id": self.session.get("session_id"),
                "settlement_id": remote.get("settlement_id"),
                "direction": remote.get("direction"), "receipt_id": remote.get("receipt_id"),
                "txid": None}
