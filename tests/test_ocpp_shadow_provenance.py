"""Synthetic OCPP fork provenance for the import shadow; no live charger or funds."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import MappingProxyType

import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.bsv_settlement.ocpp_shadow_ledger import (
    ImportShadowLedger, migrate, provenance, provenance_flags)
from custom_components.bsv_settlement.ocpp_shadow import (
    METADATA, METADATA_FIELDS, METRICS, OCPPShadowCoordinator, ShadowStore,
    charger_prefix, metadata_binding, source_binding, suggest_metadata)
from custom_components.bsv_settlement.config_flow import (
    BSVSettlementConfigFlow, OCPPShadowOptionsFlow)
from custom_components.bsv_settlement.sensor import OCPPShadowSensor

BINDING = {"connector": "synthetic-single-connector"}
START = datetime(2026, 10, 4, 0, 0, tzinfo=timezone.utc)
SERIAL = "SYNTHETIC-SERIAL-0042"
FINANCIAL = ("billing_eligible", "payment_control", "charger_control")
OWNER = "legacy_sigen"  # Every emitted OCPP shadow output (#117).
VERSION_ATTRS = {"subprotocol": "ocpp1.6", "offered_subprotocols": ["ocpp1.6"], "transport": "ws"}
KEYS_ATTRS = {
    "AuthorizeRemoteTxRequests": "0", "HeartbeatInterval": "3600",
    "MeterValueSampleInterval": "60", "TransactionMessageAttempts": "3",
    "TransactionMessageRetryInterval": "60", "SupportedFeatureProfiles": "Core",
    "readonly_keys": [], "unknown_keys": [
        "ClockAlignedDataInterval", "MeterValuesSampledData", "NumberOfConnectors"],
    "redacted_keys": [], "keys_truncated": False, "truncated_values": [],
    "measurands_configurable": False}
BOOT_ATTRS = {"charge_point_vendor": "SIGEN", "charge_point_model": "EVDC 25 7.5S2",
              "firmware_version": "V100R001C21SPC117", "charge_point_serial_number": SERIAL}


def fork_metadata(**boot):
    return {"version": {"state": "1.6", "attributes": dict(VERSION_ATTRS)},
            "configuration": {"state": "6", "attributes": dict(KEYS_ATTRS)},
            "boot": {"state": START.isoformat(), "attributes": {**BOOT_ATTRS, **boot}}}


def stamp(second):
    return (START + timedelta(seconds=second)).isoformat()


def snapshot(second, value="100", metadata=None, context_source="defaulted"):
    row = {"value": value, "unit": "kWh", "ha_updated_at": stamp(second),
           "context": "Sample.Periodic"}
    if context_source is not None:
        row["context_source"] = context_source
    return {"import": row, "transaction": {"value": "42"}, "status": {"value": "Charging"},
            "metadata": metadata or {}}


def run(ledger, seconds, **kwargs):
    for n, second in enumerate(seconds):
        ledger.observe(snapshot(second, value=str(100 + n / 10), **kwargs), stamp(second + .1))


@pytest.mark.parametrize("source,flag", [
    ("defaulted", "context_defaulted_by_integration"),
    (None, "context_source_unknown"), ("garbage", "context_source_unknown"), ("charger", None)])
def test_context_source_flags_without_regressing_acceptance(source, flag):
    ledger = ImportShadowLedger(BINDING)
    run(ledger, (0, 60, 120), context_source=source)
    s = ledger.summary()
    # Defaulted Sample.Periodic is still accepted: the live shadow keeps observing.
    assert s["state"] == "observing"
    assert s["current_span"]["sample_count"] == 2
    span_flags = s["current_span"]["provenance_flags"]
    others = {"context_defaulted_by_integration", "context_source_unknown"} - {flag}
    if flag:
        assert flag in span_flags and flag in s["quality_flags"]
    assert not others & set(span_flags) and not others & set(s["quality_flags"])
    expected = source if source in ("charger", "defaulted") else "unknown"
    assert s["current_span"]["provenance"]["context_source"] == expected
    assert ledger.data["events"][-1]["context_source"] == expected


def test_context_source_never_relaxes_sample_periodic_requirement():
    ledger = ImportShadowLedger(BINDING)
    run(ledger, (0, 60, 120), context_source="charger")
    row = snapshot(180, value="101", context_source="charger")
    row["import"]["context"] = "Sample.Clock"
    ledger.observe(row, stamp(180.1))
    assert ledger.summary()["current_span"] is None
    assert ledger.summary()["previous_span"]["end_reason"] == "invalid_or_stale_meter"


def test_provenance_snapshot_with_fork_metadata():
    ledger = ImportShadowLedger(BINDING)
    run(ledger, (0, 60, 120), metadata=fork_metadata())
    span = ledger.summary()["current_span"]
    assert span["provenance"] == {
        "protocol_version": "1.6", "subprotocol": "ocpp1.6", "transport": "ws",
        "feature_profiles": ["Core"], "meter_value_sample_interval_s": 60,
        "measurands_configurable": False, "vendor": "SIGEN", "model": "EVDC 25 7.5S2",
        "firmware": "V100R001C21SPC117", "meter_identity": "unavailable",
        "context_source": "defaulted"}
    assert span["protocol_version"] == "1.6"
    assert set(span["provenance_flags"]) == {
        "context_defaulted_by_integration", "transport_unencrypted", "meter_identity_unavailable"}
    assert SERIAL not in json.dumps(ledger.data)
    assert span["billing_eligible"] is False and span["settlement_owner"] == OWNER
    assert ledger.data["current"]["settlement_owner"] is False  # Stored encoding unchanged.


def test_provenance_degrades_when_metadata_absent_or_unavailable():
    absent = provenance({}, None)
    assert absent["protocol_version"] == "unverified"
    assert {v for k, v in absent.items() if k not in ("protocol_version", "meter_identity")} == {"unknown"}
    assert absent["meter_identity"] == "unavailable"
    assert provenance_flags(absent) == {
        "context_source_unknown", "protocol_version_unverified", "transport_unknown",
        "meter_identity_unavailable"}
    unavailable = fork_metadata()
    unavailable["version"]["state"] = "unavailable"
    unavailable["configuration"]["attributes"] = {"MeterValueSampleInterval": "-1",
                                                  "SupportedFeatureProfiles": 7,
                                                  "measurands_configurable": "false"}
    result = provenance(unavailable, "charger")
    assert result["protocol_version"] == "unverified" and result["transport"] == "unknown"
    assert result["meter_value_sample_interval_s"] == "unknown"
    assert result["feature_profiles"] == result["measurands_configurable"] == "unknown"
    assert result["vendor"] == "SIGEN"
    wss = fork_metadata(meter_type="AC", meter_serial_number="METER-SECRET")
    wss["version"]["attributes"]["transport"] = "wss"
    secure = provenance(wss, "charger")
    assert secure["meter_identity"] == "reported"
    assert provenance_flags(secure) == set()
    assert "METER-SECRET" not in json.dumps(secure)


def test_firmware_change_mid_span_journals_without_splitting():
    ledger = ImportShadowLedger(BINDING)
    run(ledger, (0, 60, 120), metadata=fork_metadata())
    span_id = ledger.summary()["current_span"]["span_id"]
    updated = fork_metadata(firmware_version="V100R001C21SPC200")
    ledger.observe(snapshot(180, value="100.3", metadata=updated), stamp(180.1))
    ledger.observe(snapshot(240, value="100.4", metadata=updated), stamp(240.1))
    span = ledger.summary()["current_span"]
    assert span["span_id"] == span_id and span["sample_count"] == 4
    assert span["observed_import_kwh"] == "0.3"
    assert span["provenance"]["firmware"] == "V100R001C21SPC117"  # opening snapshot kept
    assert span["provenance_changed"] is True
    assert ledger.summary()["provenance"]["firmware"] == "V100R001C21SPC200"
    changes = [e for e in ledger.data["events"] if e["kind"] == "provenance_changed"]
    assert len(changes) == 1  # unchanged later samples are not re-journaled
    assert changes[0]["changed_fields"] == ["firmware"]
    assert changes[0]["previous"] == {"firmware": "V100R001C21SPC117"}
    assert changes[0]["current"] == {"firmware": "V100R001C21SPC200"}
    assert changes[0]["span_id"] == span_id
    assert not any(e["reason"] == "provenance_changed" for e in ledger.data["events"]
                   if e["kind"] == "observation_boundary")


def test_lost_metadata_mid_span_accumulates_flags():
    ledger = ImportShadowLedger(BINDING)
    run(ledger, (0, 60, 120), metadata=fork_metadata(), context_source="charger")
    ledger.observe(snapshot(180, value="100.3", context_source=None), stamp(180.1))
    span = ledger.summary()["current_span"]
    assert span["sample_count"] == 3
    assert {"protocol_version_unverified", "context_source_unknown",
            "transport_unencrypted"} <= set(span["provenance_flags"])


def v1_store():
    ledger = ImportShadowLedger(BINDING)
    run(ledger, (0, 60, 120))
    ledger.close_span("identity_or_lifecycle_boundary", stamp(130))
    run(ledger, (200, 260))
    data = deepcopy(ledger.data)
    data["schema"] = 1
    for span in [*data["spans"], data["current"]]:
        for key in ("provenance", "provenance_flags", "provenance_changed"):
            del span[key]
        span["protocol_version"] = "unverified"
    for event in data["events"]:
        event.pop("context_source", None)
    return data


def test_v1_store_migrates_without_loss():
    old = v1_store()
    migrated = migrate(deepcopy(old))
    assert migrated["schema"] == 3 and migrated["migrated_from_schema"] == 1
    assert migrated["export"] is None
    assert migrated["events"] == old["events"]
    assert migrated["sequence"] == old["sequence"]
    for before, after in zip([*old["spans"], old["current"]],
                             [*migrated["spans"], migrated["current"]]):
        assert {k: after[k] for k in before} == before
        assert after["provenance"]["protocol_version"] == "unverified"
        assert after["provenance"]["context_source"] == "unknown"
        assert "provenance_not_recorded" in after["provenance_flags"]
        assert after["provenance_changed"] is False
    restored = ImportShadowLedger(BINDING, deepcopy(old))
    assert restored.data == migrated
    assert migrate(migrated) is migrated  # current schema untouched


@pytest.mark.parametrize("change", [
    {"schema": 4}, {"schema": 0}, {"schema": "2"}, {"spans": [{"span_id": 1}]}])
def test_malformed_or_future_store_rejected(change):
    with pytest.raises((ValueError, KeyError, TypeError)):
        ImportShadowLedger(BINDING, {**v1_store(), **change})
    current = ImportShadowLedger(BINDING)
    run(current, (0, 60, 120))
    broken = deepcopy(current.data)
    broken["current"]["provenance"] = None
    with pytest.raises(ValueError):
        ImportShadowLedger(BINDING, broken)


def config_entry(domain="ocpp", data=None, options=None):
    return ConfigEntry(version=1, minor_version=1, domain=domain, title="Synthetic shadow",
                       data=data or {}, source="user", unique_id=None, options=options or {},
                       discovery_keys=MappingProxyType({}), subentries_data=[])


async def setup_charger(hass, scope="ocpp.charger", cpid="charger"):
    """Measurands on a connector device; fork metadata on the charger device."""
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    if hasattr(dr, "async_setup"):
        dr.async_setup(hass)
    await dr.async_load(hass)
    await er.async_load(hass)
    native = config_entry()
    hass.config_entries._entries[native.entry_id] = native
    devices = dr.async_get(hass)
    connector = devices.async_get_or_create(
        config_entry_id=native.entry_id, identifiers={("ocpp", cpid + "-connector")})
    charger = devices.async_get_or_create(
        config_entry_id=native.entry_id, identifiers={("ocpp", cpid)})
    registry = er.async_get(hass)
    sources, metadata = {}, {}
    for key, metric in METRICS.items():
        row = registry.async_get_or_create(
            "sensor", "ocpp", f"{scope}.{metric}.sensor", config_entry=native,
            device_id=connector.id, suggested_object_id=f"{cpid}_{metric}")
        sources[key] = row.entity_id
        hass.states.async_set(row.entity_id, {
            "import": "100", "status": "Charging", "transaction": "42"}[key], {
            "unit_of_measurement": "kWh", "context": "Sample.Periodic",
            "context_source": "defaulted"})
    for key, slug in METADATA.items():
        row = registry.async_get_or_create(
            "sensor", "ocpp", f"ocpp.{cpid}.{slug}.sensor", config_entry=native,
            device_id=charger.id, suggested_object_id=f"{cpid}_{slug}")
        metadata[key] = row.entity_id
    hass.states.async_set(metadata["version"], "1.6", VERSION_ATTRS)
    hass.states.async_set(metadata["configuration"], "6", KEYS_ATTRS)
    hass.states.async_set(metadata["boot"], START.isoformat(), BOOT_ATTRS)
    return native, sources, metadata


def shadow_data(hass, sources):
    return {**{k + "_entity": v for k, v in sources.items()},
            "backend": "ocpp_import_shadow", "source_binding": source_binding(hass, sources)}


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["ocpp.charger", "ocpp.charger.conn1"])
async def test_metadata_binding_and_suggestion(tmp_path, scope):
    hass = HomeAssistant(str(tmp_path))
    try:
        native, sources, metadata = await setup_charger(hass, scope)
        binding = source_binding(hass, sources)
        assert charger_prefix(binding) == "ocpp.charger"
        assert suggest_metadata(hass, binding) == metadata
        bound = metadata_binding(hass, binding, metadata)
        assert {k: v["entity_id"] for k, v in bound.items()} == metadata
        assert bound["boot"]["unique_id"] == "ocpp.charger.boot_notification.sensor"
        assert bound["boot"]["device_id"] != binding["import"]["device_id"]
        assert metadata_binding(hass, binding, {"version": None}) == {}
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "other_charger", "other_entry", "wrong_slot", "disabled", "measurand", "not_ocpp"])
async def test_cross_charger_or_cross_entry_metadata_rejected(tmp_path, change):
    hass = HomeAssistant(str(tmp_path))
    try:
        native, sources, metadata = await setup_charger(hass)
        binding = source_binding(hass, sources)
        registry = er.async_get(hass)
        selected = dict(metadata)
        if change == "other_charger":
            registry.async_update_entity(
                metadata["boot"], new_unique_id="ocpp.charger2.boot_notification.sensor")
        elif change == "other_entry":
            other = config_entry()
            hass.config_entries._entries[other.entry_id] = other
            registry.async_update_entity(metadata["version"], config_entry_id=other.entry_id)
        elif change == "wrong_slot":
            selected = {**metadata, "version": metadata["boot"], "boot": metadata["version"]}
        elif change == "disabled":
            registry.async_update_entity(
                metadata["configuration"], disabled_by=er.RegistryEntryDisabler.USER)
        elif change == "measurand":
            selected = {**metadata, "version": sources["import"]}
        else:
            selected = {**metadata, "boot": registry.async_get_or_create(
                "sensor", "template", "ocpp.charger.boot_notification.sensor").entity_id}
        with pytest.raises(ValueError):
            metadata_binding(hass, binding, selected)
        flow = OCPPShadowOptionsFlow()
        entry = config_entry("bsv_settlement", shadow_data(hass, sources))
        hass.config_entries._entries[entry.entry_id] = entry
        flow.hass, flow.handler = hass, entry.entry_id
        result = await flow.async_step_init(
            {METADATA_FIELDS[k]: v for k, v in selected.items()})
        assert result["type"] == "form"
        assert result["errors"]["base"] == "invalid_ocpp_shadow_metadata"
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_config_and_options_flow_round_trip(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        native, sources, metadata = await setup_charger(hass)
        flow = BSVSettlementConfigFlow()
        flow.hass, flow.context = hass, {}
        form = await flow.async_step_ocpp_shadow({k + "_entity": v for k, v in sources.items()})
        assert form["step_id"] == "ocpp_shadow_metadata"
        suggested = {str(key): key.description["suggested_value"] for key in form["data_schema"].schema}
        assert suggested == {METADATA_FIELDS[k]: v for k, v in metadata.items()}
        created = await flow.async_step_ocpp_shadow_metadata(suggested)
        assert created["type"] == "create_entry"
        assert created["options"]["metadata_binding"]["version"]["entity_id"] == metadata["version"]
        assert "metadata_binding" not in created["data"]
        # Existing entry configured before metadata support: options start empty.
        entry = config_entry("bsv_settlement", created["data"])
        hass.config_entries._entries[entry.entry_id] = entry
        assert BSVSettlementConfigFlow.async_supports_options_flow(entry)
        assert not BSVSettlementConfigFlow.async_supports_options_flow(
            config_entry("bsv_settlement", {"backend": "sensor_proxy"}))
        options = BSVSettlementConfigFlow.async_get_options_flow(entry)
        options.hass, options.handler = hass, entry.entry_id
        form = await options.async_step_init()
        assert form["step_id"] == "init"
        assert {str(k): k.description["suggested_value"] for k in form["data_schema"].schema} == suggested
        export_form = await options.async_step_init(suggested)
        assert export_form["step_id"] == "export"
        saved = await options.async_step_export({})
        assert saved["type"] == "create_entry"
        assert {k: v for k, v in saved["data"].items()
                if not k.startswith("export_")} == created["options"]
        assert saved["data"]["export_binding"] == {}
        assert saved["data"]["export_reference_binding"] is None
        hass.config_entries.async_update_entry(entry, options=saved["data"])
        # Reopening shows the bound choice; clearing unbinds without touching sources.
        assert {str(k): k.description["suggested_value"]
                for k in (await options.async_step_init())["data_schema"].schema} == suggested
        await options.async_step_init({})
        cleared = await options.async_step_export({})
        assert cleared["data"]["metadata_binding"] == {}
        assert cleared["data"]["export_binding"] == {}
        assert entry.data["source_binding"] == source_binding(hass, sources)
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_coordinator_provenance_serial_and_financial_flags(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    coord = None
    try:
        native, sources, metadata = await setup_charger(hass)
        binding = source_binding(hass, sources)
        options = {**{METADATA_FIELDS[k]: v for k, v in metadata.items()},
                   "metadata_binding": metadata_binding(hass, binding, metadata)}
        entry = config_entry("bsv_settlement", shadow_data(hass, sources), options)
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        for value in ("100.1", "100.3"):
            hass.states.async_set(sources["import"], value, {
                "unit_of_measurement": "kWh", "context": "Sample.Periodic",
                "context_source": "defaulted"})
            await hass.async_block_till_done()
        sensor = OCPPShadowSensor(coord, entry, "shadow_status", "Status", None)
        attrs = sensor.extra_state_attributes
        assert sensor.native_value == "observing"
        assert attrs["provenance"]["protocol_version"] == "1.6"
        assert attrs["provenance"]["vendor"] == "SIGEN"
        assert attrs["current_span"]["provenance"]["transport"] == "ws"
        assert {"transport_unencrypted", "meter_identity_unavailable",
                "context_defaulted_by_integration"} <= set(attrs["quality_flags"])
        assert "metadata_not_bound" not in attrs["quality_flags"]
        assert not any(attrs[k] for k in FINANCIAL)
        assert attrs["settlement_owner"] == attrs["current_span"]["settlement_owner"] == OWNER
        assert attrs["current_span"]["billing_eligible"] is False
        assert SERIAL not in json.dumps(attrs, default=str)
        assert SERIAL not in json.dumps(coord.ledger.data)
        assert SERIAL not in json.dumps(coord.summary(), default=str)
        # Metadata identity drift degrades provenance rather than failing.
        er.async_get(hass).async_update_entity(
            metadata["version"], new_unique_id="ocpp.other.version_ocpp.sensor")
        coord.observe()
        summary = coord.summary()
        assert summary["state"] != "incompatible"
        assert "metadata_binding_changed" in summary["quality_flags"]
        assert summary["provenance"]["protocol_version"] == "unverified"
        await coord.close()
        saved = await ShadowStore(hass, entry.entry_id, binding).async_load()
        assert saved["schema"] == 3 and SERIAL not in json.dumps(saved)
    finally:
        if coord:
            await coord.close()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_upstream_build_without_metadata_stays_unverified(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    coord = None
    try:
        native, sources, metadata = await setup_charger(hass)
        for entity_id in metadata.values():
            hass.states.async_remove(entity_id)
        entry = config_entry("bsv_settlement", shadow_data(hass, sources))
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        for value in ("100.1", "100.3"):
            hass.states.async_set(sources["import"], value, {
                "unit_of_measurement": "kWh", "context": "Sample.Periodic"})
            await hass.async_block_till_done()
        summary = coord.summary()
        assert summary["state"] == "observing"
        assert summary["current_span"]["protocol_version"] == "unverified"
        assert {"metadata_not_bound", "context_source_unknown",
                "protocol_version_unverified"} <= set(summary["quality_flags"])
        assert not any(summary[k] for k in FINANCIAL) and summary["settlement_owner"] == OWNER
        with pytest.raises(HomeAssistantError, match="read-only"):
            await coord.execute("prepare_operator_payment", {})
    finally:
        if coord:
            await coord.close()
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_v1_store_file_migrated_and_future_file_not_rewritten(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    try:
        native, sources, metadata = await setup_charger(hass)
        data = shadow_data(hass, sources)
        old = v1_store()
        old["binding"] = data["source_binding"]
        storage = tmp_path / ".storage"
        storage.mkdir(exist_ok=True)
        entry = config_entry("bsv_settlement", data)
        path = storage / f"bsv_settlement.ocpp_shadow.{entry.entry_id}"
        path.write_text(json.dumps({"version": 1, "minor_version": 1,
                                    "key": path.name, "data": old}))
        coord = OCPPShadowCoordinator(hass, entry)
        await coord.load()
        assert coord.ledger.data["events"][:len(old["events"])] == old["events"]
        assert len(coord.ledger.data["spans"]) == 2  # v1 current closed by restart gap
        await coord.close()
        on_disk = json.loads(path.read_text())
        assert on_disk["version"] == 3 and on_disk["data"]["schema"] == 3
        assert on_disk["data"]["migrated_from_schema"] == 1

        for bad in ({"version": 4, "minor_version": 1, "key": path.name, "data": old},
                    {"version": 1, "minor_version": 1, "key": path.name,
                     "data": {**old, "binding": {"other": "charger"}}},
                    {"version": 2, "minor_version": 1, "key": path.name,
                     "data": {**old, "schema": 4}}):
            other = config_entry("bsv_settlement", data)
            bad_path = storage / f"bsv_settlement.ocpp_shadow.{other.entry_id}"
            bad_path.write_text(json.dumps(bad))
            before = bad_path.read_text()
            with pytest.raises((ValueError, HomeAssistantError)):
                await OCPPShadowCoordinator(hass, other).load()
            assert bad_path.read_text() == before
    finally:
        await hass.async_stop(force=True)
