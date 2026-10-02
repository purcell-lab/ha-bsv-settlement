"""Recovery display must reflect current readiness without weakening authority."""
import copy

import pytest

from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from test_auto_credit import ready
from test_budget import no_network
from test_ongoing_credit import config

pytestmark = pytest.mark.asyncio


async def open_route(tmp_path):
    hass, api, proxy, row, _, _ = await ready(tmp_path)
    proxy.data["latest_session"].update(ended_at=None, status="active_observed")
    await api.ongoing_credits.configure(config(row), "admin")
    return hass, api, proxy, row, next(iter(api.ongoing_credits.routes.values()))


async def test_recovered_open_session_clears_stale_blocker_and_persists(tmp_path):
    hass, api, proxy, row, route = await open_route(tmp_path)
    original = copy.deepcopy(route)
    proxy.data["issues"] = ["source unavailable"]
    await api.ongoing_credits.tick()
    assert route["state"] == "credit_blocked" and route["error"]
    proxy.data["issues"] = []
    await api.ongoing_credits.tick()
    assert route["state"] == "waiting_for_session_end"
    assert "error" not in route
    assert route["recipient"] == original["recipient"]
    assert route["satoshis_per_aud"] == original["satoshis_per_aud"]
    assert not api.chain.posts and not api.saved["automatic_credits"]
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    assert restored.ongoing_credits.routes[route["route_id"]] == route


async def test_recovery_cannot_hide_revoked_registration(tmp_path):
    _, api, proxy, row, route = await open_route(tmp_path)
    proxy.data["issues"] = ["source unavailable"]
    await api.ongoing_credits.tick()
    row["state"] = "revoked"
    proxy.data["issues"] = []
    await api.ongoing_credits.tick()
    assert route["state"] == "credit_blocked"
    assert "revoked" in route["error"]
    assert not api.chain.posts


async def test_reopened_frozen_account_remains_queued_for_review(tmp_path):
    _, api, proxy, row, _, _ = await ready(tmp_path)
    await api.ongoing_credits.configure(config(row), "admin")
    api.chain.rows = []  # Freeze a credit account without signing or spending.
    await api.ongoing_credits.tick()
    route = next(iter(api.ongoing_credits.routes.values()))
    item = api.ongoing_credits.get(api.ongoing_credits.wrapper(route))
    frozen = item["source_hash"]
    proxy.data["latest_session"].update(ended_at=None, status="active_observed")
    await api.ongoing_credits.tick()
    assert route["state"] == "credit_queued"
    assert "reopened" in route["error"]
    assert item["source_hash"] == frozen
    assert not api.chain.posts
