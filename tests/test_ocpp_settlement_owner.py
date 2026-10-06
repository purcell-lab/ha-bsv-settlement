"""Issue #117: one settlement_owner encoding on every OCPP shadow output.

Stored spans keep their legacy encoding (import False, export None); stores are
never rewritten. Outputs always say ``legacy_sigen``.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components.bsv_settlement.ocpp_export_shadow_ledger import (
    ExportShadowLedger, validate_section)
from custom_components.bsv_settlement.ocpp_shadow_ledger import ImportShadowLedger
from custom_components.bsv_settlement.sensor import (
    OCPPExportShadowSensor, OCPPLifecycleSensor, OCPPShadowSensor, RecorderReadinessSensor)
from test_ocpp_export_shadow import BINDING as EXPORT_BINDING, end, feed, warmed
from test_ocpp_shadow_provenance import BINDING, run

OWNER = "legacy_sigen"


def import_store(owner):
    ledger = ImportShadowLedger(BINDING)
    run(ledger, (0, 60, 120))
    ledger.close_span("fixture", "2026-10-04T00:03:00+00:00")
    run(ledger, (180, 240))
    saved = deepcopy(ledger.data)
    for span in [*saved["spans"], saved["current"]]:
        assert span["settlement_owner"] is False  # Encoding written by every release so far.
        span["settlement_owner"] = owner
    return saved


def export_section(owner):
    ledger, rows = warmed()
    feed(ledger, rows)
    end(ledger, 781)
    section = deepcopy(ledger.data)
    assert section["spans"] and section["spans"][-1]["settlement_owner"] is None
    for span in section["spans"]:
        span["settlement_owner"] = owner
    return section


@pytest.mark.parametrize("owner", [False, None, OWNER])
def test_stored_import_spans_load_and_emit_legacy_sigen(owner):
    saved = import_store(owner)
    raw = json.dumps(saved, sort_keys=True)
    ledger = ImportShadowLedger(BINDING, saved)
    summary = ledger.summary()
    assert summary["settlement_owner"] == OWNER
    assert summary["current_span"]["settlement_owner"] == OWNER
    assert summary["previous_span"]["settlement_owner"] == OWNER
    # Emitting never rewrites the loaded or caller's store.
    assert json.dumps(saved, sort_keys=True) == raw
    assert ledger.data["spans"][-1]["settlement_owner"] == owner


@pytest.mark.parametrize("owner", [False, None, OWNER])
def test_stored_export_spans_load_and_emit_legacy_sigen(owner):
    section = export_section(owner)
    raw = json.dumps(section, sort_keys=True)
    assert validate_section(deepcopy(section)) == section
    ledger = ExportShadowLedger(section, binding=EXPORT_BINDING)
    summary = ledger.summary()
    assert summary["settlement_owner"] == summary["last_span"]["settlement_owner"] == OWNER
    assert json.dumps(section, sort_keys=True) == raw


@pytest.mark.parametrize("owner", [True, "ocpp", "", 0])
def test_other_stored_owner_values_still_refused(owner):
    with pytest.raises(ValueError):
        ImportShadowLedger(BINDING, import_store(owner))
    with pytest.raises(ValueError):
        validate_section(export_section(owner))


def entity(cls, data, key):
    """Call the attribute property on a stand-in entity; no HA instance needed."""
    stand_in = SimpleNamespace(coordinator=SimpleNamespace(data=data), key=key)
    for name in ("export", "recorder", "lifecycle"):
        prop = getattr(cls, name, None)
        if isinstance(prop, property):
            setattr(stand_in, name, prop.fget(stand_in))
    return cls.extra_state_attributes.fget(stand_in)


@pytest.mark.parametrize("empty", [False, True])
def test_every_ocpp_shadow_sensor_emits_legacy_sigen(empty):
    imported = ImportShadowLedger(BINDING, import_store(False)).summary()
    exported = ExportShadowLedger(export_section(None), binding=EXPORT_BINDING).summary()
    data = None if empty else {**imported, "export_shadow": exported,
                               "recorder": {"settlement_owner": OWNER}, "lifecycle": {}}
    sensors = [(OCPPShadowSensor, "shadow_status"), (OCPPShadowSensor, "shadow_import"),
               (OCPPExportShadowSensor, "export_last_span"),
               (OCPPExportShadowSensor, "export_quality"),
               (RecorderReadinessSensor, "session_recorder"),
               (RecorderReadinessSensor, "recorder_readiness"),
               (RecorderReadinessSensor, "recorder_reconciliation"),
               (OCPPLifecycleSensor, "ocpp_lifecycle")]
    for cls, key in sensors:
        attrs = entity(cls, data, key)
        assert attrs["settlement_owner"] == OWNER, (cls.__name__, key)
        assert not attrs["billing_eligible"]
        for span_key in ("current_span", "previous_span"):
            if attrs.get(span_key):
                assert attrs[span_key]["settlement_owner"] == OWNER
    assert False not in _owners(imported) and None not in _owners(exported)


def _owners(value):
    if isinstance(value, dict):
        return [v for k, v in value.items() if k == "settlement_owner"] + [
            o for v in value.values() for o in _owners(v)]
    if isinstance(value, list):
        return [o for v in value for o in _owners(v)]
    return []
