"""Five fixed sensors for the latest session, without full ledger attributes."""
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EntityCategory
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
    if coordinator.mode == "ocpp_import_shadow":
        async_add_entities(OCPPShadowSensor(coordinator, entry, key, name, unit)
                           for key, name, unit in (
                               ("shadow_status", "OCPP import shadow status", None),
                               ("observed_import_kwh", "Observed import span energy", "kWh")))
        async_add_entities(OCPPExportShadowSensor(coordinator, entry, key, name, unit)
                           for key, name, unit in (
                               ("export_last_span", "OCPP export shadow last span", "kWh"),
                               ("export_quality", "OCPP export shadow quality", None)))
        async_add_entities(RecorderReadinessSensor(coordinator, entry, key, name, None)
                           for key, name in (
                               ("session_recorder", "Session recorder"),
                               ("recorder_readiness", "OCPP recorder readiness"),
                               ("last_reconciliation", "Last OCPP reconciliation")))
        return
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


class OCPPShadowSensor(CoordinatorEntity, SensorEntity):
    """No settleable session schema or private journal in entity attributes."""
    _attr_has_entity_name = True
    _attr_icon = "mdi:eye-outline"

    def __init__(self, coordinator, entry, key, name, unit):
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)}, "name": entry.title,
            "manufacturer": "Proof of concept", "model": "Read-only OCPP import shadow"}

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        if self.key == "shadow_status":
            return data.get("state")
        from decimal import Decimal
        span = data.get("current_span") or {}
        value = span.get("observed_import_kwh")
        return Decimal(value) if value is not None else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        return {k: data.get(k) for k in (
            "mode", "quality_flags", "provenance", "current_span", "previous_span",
            "retained_span_count", "journal_trimmed", "spans_trimmed",
            "billing_eligible", "settlement_owner", "payment_control",
            "charger_control", "export_kwh", "net_cost_aud")}


class OCPPExportShadowSensor(OCPPShadowSensor):
    """Diagnostic export estimate and grade; never a credit, price or settlement input."""
    _attr_icon = "mdi:transmission-tower-export"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def export(self):
        return (self.coordinator.data or {}).get("export_shadow") or {}

    @property
    def native_value(self):
        export = self.export
        if self.key == "export_quality":
            return "not_bound" if export.get("state") == "not_bound" else export.get("latest_grade")
        from decimal import Decimal
        value = (export.get("last_span") or {}).get("estimate_kwh")
        return Decimal(value) if value is not None else None

    @property
    def extra_state_attributes(self):
        export = self.export
        common = {"mode": (self.coordinator.data or {}).get("mode"), "state": export.get("state"),
                  "flags": export.get("flags"), "grading": export.get("grading"),
                  "billing_eligible": False, "settlement_owner": None,
                  "payment_control": False, "charger_control": False}
        if self.key == "export_quality":
            return {**common, "grade_counts": export.get("grade_counts"),
                    "retained_span_count": export.get("retained_span_count"),
                    "journal_trimmed": export.get("journal_trimmed"),
                    "spans_trimmed": export.get("spans_trimmed")}
        span = export.get("last_span") or {}
        return {**common, **{k: span.get(k) for k in (
            "span_id", "grade", "spread", "estimate_kwh", "lower_kwh", "upper_kwh",
            "source_label", "anomaly_flags", "quality_flags", "provenance_flags",
            "sample_count", "step_intervals", "max_gap_s", "flow_direction_counts",
            "first_source_timestamp", "last_source_timestamp",
            "first_meter_ha_updated_at", "last_meter_ha_updated_at",
            "observation_ended_at", "end_reason", "reference_status",
            "reference_delta_kwh", "reference_divergence_kwh", "reference_divergence_ratio",
            "session_export_delta_kwh")},
            "current_span": export.get("current_span")}


class RecorderReadinessSensor(OCPPShadowSensor):
    """Diagnostic recorder contract state; no selector, switch or settlement input."""
    _attr_icon = "mdi:clipboard-check-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def recorder(self):
        return (self.coordinator.data or {}).get("recorder") or {}

    @property
    def native_value(self):
        recorder = self.recorder
        if self.key == "session_recorder":
            return recorder.get("settlement_recorder")
        if self.key == "recorder_readiness":
            return (recorder.get("ocpp") or {}).get("overall_level")
        reconciliation = recorder.get("reconciliation") or {}
        if reconciliation.get("state") != "ready":
            return reconciliation.get("state")
        last = reconciliation.get("last_result")
        return last["explanation"] if last else "none"

    @property
    def extra_state_attributes(self):
        recorder = self.recorder
        common = {"billing_eligible": False, "settlement_owner": recorder.get("settlement_owner"),
                  "selector_implemented": False, "payment_control": False,
                  "charger_control": False}
        if self.key == "session_recorder":
            return {**common, "legacy_entry_id": recorder.get("legacy_entry_id"),
                    "link_state": recorder.get("link_state"),
                    "ocpp_role": (recorder.get("ocpp") or {}).get("role"),
                    "legacy_health": (recorder.get("legacy") or {}).get("health"),
                    "legacy_readiness": (recorder.get("legacy") or {}).get("directions")}
        if self.key == "recorder_readiness":
            ocpp = recorder.get("ocpp") or {}
            directions = ocpp.get("directions") or {}
            return {**common, "health": ocpp.get("health"),
                    "limitations": ocpp.get("limitations"),
                    **{f"{d}_level": (directions.get(d) or {}).get("level") for d in ("import", "export")},
                    **{f"{d}_limitations": (directions.get(d) or {}).get("limitations")
                       for d in ("import", "export")},
                    **{f"{d}_sample_age_s": (directions.get(d) or {}).get("sample_age_s")
                       for d in ("import", "export")},
                    "directions": directions,
                    "operator_validation_available": recorder.get("operator_validation_available")}
        reconciliation = recorder.get("reconciliation") or {}
        last = reconciliation.get("last_result") or {}
        return {**common, "legacy_entry_id": recorder.get("legacy_entry_id"),
                "state": reconciliation.get("state"), "flags": reconciliation.get("flags"),
                **{k: last.get(k) for k in (
                    "outcome", "within_tolerance", "direction", "span_id",
                    "window_start", "window_end", "window_s", "ocpp_kwh", "ocpp_lower_kwh",
                    "ocpp_upper_kwh", "ocpp_grade", "legacy_kwh", "legacy_lower_kwh",
                    "legacy_upper_kwh", "difference_kwh", "difference_pct", "allowance_kwh",
                    "reconciled_at")},
                "tolerance": reconciliation.get("tolerance"),
                **{f"last_{d}_explanation": (reconciliation.get("last_" + d) or {}).get(
                    "explanation") for d in ("import", "export")},
                **{k: reconciliation.get(k) for k in (
                    "aligned_count", "unresolved_count", "outcome_counts", "explanation_counts",
                    "retained_count", "results_trimmed", "pending_count", "lifetime_totals")}}


# Wallet health fields small enough to record on every update.
WALLET_SUMMARY_KEYS = (
    "mode", "network", "backend", "broadcast_enabled", "balance_verified",
    "budget_gate_implemented", "driver_wallet_external", "last_self_test",
    "receive_address", "operator_public_key", "driver_identity_status",
    "chain_checked_at", "chain_error", "balance_source", "last_payment",
    "chain_attempted_at", "pending_change_sats", "pending_change_source",
    "max_payment_sats", "max_fee_sats")
# Ledger-sized fields the dashboard cards read from the live state. Together
# they reach ~50 KB on a live mainnet wallet, past the recorder's 16 KB
# attribute limit, so they are kept out of the recorder (history keeps the
# compact counts from wallet_summary instead).
WALLET_DETAIL_KEYS = (
    "driver_approvals", "session_payments", "ongoing_credit", "closed_sessions",
    "latest_session_review", "automatic_credit")
BALANCE_KEYS = (
    "mode", "network", "backend", "balance_verified", "balance_source",
    "chain_checked_at", "chain_attempted_at", "chain_error",
    "pending_change_sats", "pending_change_source")


def _count(value, key=None):
    if key is not None:
        value = value.get(key) if isinstance(value, dict) else None
    return len(value) if isinstance(value, (list, tuple)) else None


def wallet_summary(health):
    """Recorded counts and flags standing in for the unrecorded ledger fields."""
    ongoing = health.get("ongoing_credit")
    automatic = health.get("automatic_credit")
    return {
        "driver_approval_count": _count(health.get("driver_approvals")),
        "session_payment_count": _count(health.get("session_payments")),
        "closed_session_count": _count(health.get("closed_sessions")),
        "ongoing_credit_session_count": _count(ongoing, "sessions"),
        "ongoing_credit_effective": ongoing.get("effective") if isinstance(ongoing, dict) else None,
        "automatic_credit_enabled": automatic.get("enabled") if isinstance(automatic, dict) else None,
        "automatic_credit_payment_count": _count(automatic, "payments"),
    }


class SettlementSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True
    _unrecorded_attributes = frozenset(WALLET_DETAIL_KEYS)

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
        if self.key == "operator_wallet_status":
            health = (self.coordinator.data or {}).get("health", {})
            return {**{key: health.get(key) for key in (*WALLET_SUMMARY_KEYS, *WALLET_DETAIL_KEYS)},
                    **wallet_summary(health)}
        if self.key == "confirmed_wallet_balance":
            health = (self.coordinator.data or {}).get("health", {})
            return {key: health.get(key) for key in BALANCE_KEYS}
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
