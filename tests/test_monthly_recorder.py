"""Real adapter with fictional HA recorder data, never live wallet evidence."""
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.monthly_recorder import RetainedSessionAccounts
from test_session_review import session

pytestmark = pytest.mark.asyncio


def fixture():
    record = session()
    recorder = SimpleNamespace(mode="sensor_proxy", last_update_success=True,
        data={"latest_session": record, "previous_session": None, "issues": [],
              "updated_at": "2026-10-06T00:00:00+00:00"},
        archive=[], async_request_refresh=AsyncMock())
    hass = SimpleNamespace(data={"bsv_settlement": {"recorder": recorder}})
    binding = dict(account_key="recorder|session-1", station_id="station",
        transaction_id=record["ocpp_transaction_id"], opened_at=record["opened_at"])
    adapter = RetainedSessionAccounts(hass, {"station": "recorder"},
        clock=lambda: datetime(2026, 10, 6, tzinfo=timezone.utc))
    return adapter, recorder, binding


async def test_exact_retained_record_and_copy():
    adapter, recorder, binding = fixture()
    expected = copy.deepcopy(recorder.data["latest_session"])
    recorder.archive = [expected]
    recorder.data["latest_session"] = session(session_id="newer")
    result = await adapter(binding)
    assert result == expected
    result["import_kwh"] = 900
    assert recorder.archive[0]["import_kwh"] == 1
    recorder.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize("change", [
    {"account_key": "other|session-1"}, {"account_key": "recorder|missing"},
    {"account_key": "recorder|session-1|extra"}, {"station_id": "other"},
    {"transaction_id": "different"}, {"opened_at": "2026-10-01T00:00:00+00:00"},
])
async def test_identity_mismatch_never_falls_back(change):
    adapter, _, binding = fixture()
    with pytest.raises(WalletError):
        await adapter(binding | change)


@pytest.mark.parametrize("change", [
    {"ended_at": None}, {"status": "active_observed"},
    {"unpriced_import_wh": "1"}, {"net_cost_aud_unrounded": None},
    {"quality_flags": ["import:missing_counter_baseline"]},
    {"ended_at": "2027-01-01T00:00:00+00:00"},
])
async def test_invalid_account_refused(change):
    adapter, recorder, binding = fixture()
    recorder.data["latest_session"].update(change)
    with pytest.raises(WalletError):
        await adapter(binding)


@pytest.mark.parametrize("mode", ["ocpp_observer", "mock", "embedded_mainnet"])
async def test_non_authoritative_sources_refused(mode):
    adapter, recorder, binding = fixture()
    recorder.mode = mode
    with pytest.raises(WalletError):
        await adapter(binding)
    recorder.async_request_refresh.assert_not_awaited()


async def test_refresh_failure_stale_and_replaced():
    adapter, recorder, binding = fixture()
    recorder.async_request_refresh.side_effect = OSError("offline")
    with pytest.raises(WalletError, match="refresh failed"):
        await adapter(binding)
    recorder.async_request_refresh.side_effect = None
    recorder.last_update_success = False
    with pytest.raises(WalletError, match="stale"):
        await adapter(binding)
    recorder.last_update_success = True
    async def replace():
        adapter.hass.data["bsv_settlement"]["recorder"] = object()
    recorder.async_request_refresh.side_effect = replace
    with pytest.raises(WalletError, match="replaced"):
        await adapter(binding)


async def test_conflicting_duplicate_refused_identical_allowed():
    adapter, recorder, binding = fixture()
    recorder.archive = [copy.deepcopy(recorder.data["latest_session"])]
    await adapter(binding)
    recorder.archive[0]["net_cost_aud_unrounded"] = "8"
    with pytest.raises(WalletError, match="Conflicting"):
        await adapter(binding)


@pytest.mark.parametrize("amount", ["-1.89", "0", "1.89"])
async def test_preserves_direction_without_selecting_payment_route(amount):
    adapter, recorder, binding = fixture()
    recorder.data["latest_session"]["net_cost_aud_unrounded"] = amount
    assert (await adapter(binding))["net_cost_aud_unrounded"] == amount


async def test_source_issues_refused():
    adapter, recorder, binding = fixture()
    recorder.data["issues"] = ["source unavailable"]
    with pytest.raises(WalletError, match="history"):
        await adapter(binding)


async def test_mapping_is_copied_and_duplicate_recorders_refused():
    adapter, recorder, _ = fixture()
    mapping = {"station": "recorder"}
    other = RetainedSessionAccounts(adapter.hass, mapping)
    mapping["station"] = "changed"
    assert other.stations["station"] == "recorder"
    with pytest.raises(WalletError):
        RetainedSessionAccounts(adapter.hass, {"a": "recorder", "b": "recorder"})


async def test_real_monthly_reservation_freezes_retained_account():
    from test_monthly_authority import bound, transition
    svc, clock, terms = await bound()
    binding = svc.snapshot()["bindings"]["proxy|session"]
    record = await svc.resolve_record(binding)
    recorder = SimpleNamespace(mode="sensor_proxy", last_update_success=True,
        data={"latest_session": record, "previous_session": None, "issues": [],
              "updated_at": clock[0].isoformat()},
        archive=[], async_request_refresh=AsyncMock())
    hass = SimpleNamespace(data={"bsv_settlement": {"proxy": recorder}})
    svc.resolve_record = RetainedSessionAccounts(hass, {"station-1": "proxy"},
                                                 clock=lambda: clock[0])
    await transition(svc, terms, "reserve", attempt_id="a", account_id="proxy|session",
                     debit_sats=100, fee_reserve_sats=23)
    final = svc.snapshot()["bindings"]["proxy|session"]["final_account"]
    assert final["debit_sats"] == 100
    record["net_cost_aud_unrounded"] = "2"
    with pytest.raises(WalletError, match="Frozen"):
        await svc._account(svc.snapshot()["bindings"]["proxy|session"])


@pytest.mark.parametrize("updated", [None, "invalid", "2026-10-06T00:00:00",
    "2026-10-05T23:59:29+00:00", "2026-10-06T00:00:01+00:00"])
async def test_successful_refresh_cannot_mask_stale_or_future_data(updated):
    adapter, recorder, binding = fixture()
    recorder.data["updated_at"] = updated
    with pytest.raises(WalletError, match="not fresh"):
        await adapter(binding)
