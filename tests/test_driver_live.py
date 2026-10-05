"""Driver telemetry does not create or broaden financial authority."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from custom_components.bsv_settlement.driver_live import ocpp_display
from custom_components.bsv_settlement.session_review import now
from test_budget import consent, no_network
from test_driver_http import reservation

pytestmark = pytest.mark.asyncio


async def approved(tmp_path):
    hass, api, proxy, _, row = await reservation(tmp_path)
    bid = row["terms"]["budget_id"]
    await api.budgets.execute("accept_session_budget", {
        "budget_id": bid, "receipt": consent(row)}, "admin")
    proxy.data["latest_session"].update(
        session_id="current", opened_at=now().isoformat(), ended_at=None,
        import_kwh=2.5, export_kwh=0.25, net_cost_aud=0.12)
    proxy.data["updated_at"] = now().isoformat()
    return hass, api, proxy, api.saved["session_budgets"][bid]


async def test_candidate_hint_is_exact_read_only_and_never_exposes_other_driver(tmp_path):
    _, api, proxy, row = await approved(tmp_path)
    before = deepcopy(api.saved)
    item = next(r for r in api.budgets.summary() if r["budget_id"] == row["terms"]["budget_id"])
    assert item["match_candidate_session_id"] == "current"
    assert item["session_id"] is None
    assert api.budgets.driver_view(row)["session"] is None
    assert api.saved == before
    assert api.chain.posts == []
    proxy.data["latest_session"]["ended_at"] = now().isoformat()
    assert api.budgets.match_candidate(row) is None
    proxy.data["latest_session"]["ended_at"] = None
    proxy.data["latest_session"]["opened_at"] = (now() - timedelta(days=1)).isoformat()
    assert api.budgets.match_candidate(row) is None
    proxy.data["latest_session"]["opened_at"] = now().isoformat()
    row["state"] = "revoked"
    assert api.budgets.match_candidate(row) is None


async def test_owned_receiving_route_exposes_live_totals_without_spending_binding(tmp_path, monkeypatch):
    _, api, proxy, row = await approved(tmp_path)
    monkeypatch.setattr(api.ongoing_credits, "driver_rows", lambda _: [{"session_id": "current"}])
    before = deepcopy(api.saved)
    view = api.budgets.driver_view(row)
    assert view["session"]["import_kwh"] == 2.5
    assert view["session"]["export_kwh"] == 0.25
    assert view["session_basis"] == "registered_receiving_route"
    assert view["session_updated_at"] == proxy.data["updated_at"]
    assert view["binding"] is None
    assert api.collections.session_id(row) is None
    assert api.saved == before
    monkeypatch.setattr(api.ongoing_credits, "driver_rows", lambda _: [{"session_id": "someone-else"}])
    assert api.budgets.driver_view(row)["session"] is None


async def test_matched_session_does_not_switch_to_newer_session(tmp_path):
    _, api, proxy, row = await approved(tmp_path)
    await api.budgets.bind(row, {"session_id": "current", "confirm_driver_present": True})
    proxy.data["previous_session"] = deepcopy(proxy.data["latest_session"])
    proxy.data["latest_session"] = deepcopy(proxy.data["latest_session"])
    proxy.data["latest_session"]["session_id"] = "other-driver"
    view = api.budgets.driver_view(row)
    assert view["session"]["session_id"] == "current"
    assert view["ocpp"]["available"] is False
    proxy.data["issues"] = ["unavailable"]
    assert api.budgets.driver_view(row)["session"] is None


async def test_ocpp_status_requires_unique_link_current_session_and_valid_binding(tmp_path, monkeypatch):
    hass, api, proxy, _ = await approved(tmp_path)
    binding = {"status": {"entity_id": "sensor.demo_ocpp_status"}}
    observer = SimpleNamespace(mode="ocpp_import_shadow", binding_error=False,
        sources={"status": "sensor.demo_ocpp_status"},
        entry=SimpleNamespace(data={"source_binding": binding}),
        data={"updated_at": now().isoformat(), "recorder": {"legacy_entry_id": "proxy-entry"}})
    hass.data["bsv_settlement"]["observer"] = observer
    hass.states.async_set("sensor.demo_ocpp_status", "Charging")
    monkeypatch.setattr("custom_components.bsv_settlement.ocpp_shadow.source_binding", lambda *_: binding)
    record = proxy.data["latest_session"]
    assert ocpp_display(api, "proxy-entry", record)["status"] == "Charging"
    hass.states.async_set("sensor.demo_ocpp_status", "SuspendedEV")
    assert ocpp_display(api, "proxy-entry", record)["status"] == "SuspendedEV"
    hass.states.async_set("sensor.demo_ocpp_status", "unavailable")
    assert not ocpp_display(api, "proxy-entry", record)["available"]
    hass.states.async_set("sensor.demo_ocpp_status", "Charging")
    observer.data["updated_at"] = (now() - timedelta(seconds=61)).isoformat()
    assert not ocpp_display(api, "proxy-entry", record)["available"]
    observer.data["updated_at"] = now().isoformat()
    observer.binding_error = True
    assert not ocpp_display(api, "proxy-entry", record)["available"]
    observer.binding_error = False
    hass.data["bsv_settlement"]["second"] = observer
    assert not ocpp_display(api, "proxy-entry", record)["available"]
    del hass.data["bsv_settlement"]["second"]
    observer.data["recorder"]["legacy_entry_id"] = "different-recorder"
    assert not ocpp_display(api, "proxy-entry", record)["available"]
    assert api.chain.posts == []
