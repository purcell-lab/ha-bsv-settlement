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
    if coordinator.mode == "sensor_proxy":
        async_add_entities(ProxySensor(coordinator, entry, key, name, unit) for key, name, unit in (
            ("recorder_status", "Recorder status", None),
            ("transaction_id", "Proxy transaction ID", None),
            ("import_kwh", "Session import energy", "kWh"),
            ("export_kwh", "Session export energy", "kWh"),
            ("net_cost_aud", "Provisional session cost", "AUD"),
        ))
        return
    specs = SENSORS
    if coordinator.mode.startswith("embedded_"):
        specs = [*SENSORS, ("operator_wallet_status", "Operator wallet status", None)]
    if coordinator.mode == "embedded_mainnet":
        specs = [*specs, ("confirmed_wallet_balance", "Confirmed wallet balance", "sat")]
    async_add_entities(SettlementSensor(coordinator, entry, *spec) for spec in specs)


class SettlementSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, name, unit):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)},
                                  "name": (f"BSV Operator Wallet ({coordinator.api.network.title()})"
                                           if coordinator.mode.startswith("embedded_")
                                           else "BSV Settlement (Mock)"),
                                  "manufacturer": "Proof of concept",
                                  "model": ("Embedded SDK, guarded mainnet" if coordinator.mode == "embedded_mainnet"
                                            else "Embedded SDK, broadcast disabled"
                                            if coordinator.mode == "embedded_testnet"
                                            else "Simulated settlement only")}

    @property
    def session(self):
        data = self.coordinator.data or {}
        return data.get("sessions", {}).get(data.get("latest"), {})

    @property
    def native_value(self):
        if self.key == "operator_wallet_status":
            return (self.coordinator.data or {}).get("health", {}).get("state")
        if self.key == "confirmed_wallet_balance":
            return (self.coordinator.data or {}).get("health", {}).get("balance_sats")
        session = self.session
        remote = session.get("remote", {})
        if self.key == "settlement_status":
            return remote.get("state", "metering" if session else "idle")
        if self.key == "session_import_energy":
            if not session:
                return None
            return sum(i["import_wh"] for i in session.get("intervals", [])) / 1000
        if self.key == "session_export_energy":
            if not session:
                return None
            return sum(i["export_wh"] for i in session.get("intervals", [])) / 1000
        if self.key == "session_net_amount":
            amount = session.get("payload", {}).get("net_amount_minor")
            return amount / 100 if amount is not None else None
        return remote.get("amount_sats")

    @property
    def extra_state_attributes(self):
        if self.key in ("operator_wallet_status", "confirmed_wallet_balance"):
            health = (self.coordinator.data or {}).get("health", {})
            return {key: health.get(key) for key in (
                "mode", "network", "backend", "broadcast_enabled", "balance_verified",
                "budget_gate_implemented", "driver_wallet_external", "last_self_test",
                "receive_address", "operator_public_key", "driver_identity_status",
                "chain_checked_at", "chain_error", "balance_source", "last_payment",
                "max_payment_sats", "max_fee_sats", "latest_session_review")}
        remote = self.session.get("remote", {})
        return {"mode": self.coordinator.mode, "session_id": self.session.get("session_id"),
                "settlement_id": remote.get("settlement_id"),
                "direction": remote.get("direction"), "receipt_id": remote.get("receipt_id"),
                "txid": None}


class ProxySensor(CoordinatorEntity, SensorEntity):
    """Small state attributes only; full source observations stay in private storage."""
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, key, name, unit):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = "mdi:ev-station"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title,
            "manufacturer": "Proof of concept", "model": "Read-only sensor session proxy"}

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        latest = data.get("latest_session") or {}
        if self.key == "recorder_status":
            return data.get("state")
        if self.key == "transaction_id":
            return latest.get("ocpp_transaction_id")
        return latest.get(self.key)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        if self.key == "recorder_status":
            return {**data, "mode": "sensor_proxy", "payment_control": False,
                    "charger_control": False}
        session = data.get("latest_session") or {}
        return {"session_id": session.get("session_id"),
                "ocpp_transaction_id": session.get("ocpp_transaction_id"),
                "transaction_id_source": "proxy_generated", "billing_eligible": False,
                "quality_flags": session.get("quality_flags", [])}
