"""Retention and expiry bounds of persisted stores (#6). Fictional data only.

Each test pins one bound: what is dropped, and that nothing financially
unresolved is dropped with it. Display windows are proved to be views only;
the saved records stay in the ledger. See docs/record-versioning.md.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import pytest_asyncio

pytest.importorskip("homeassistant")
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.bsv_settlement import proxy as proxy_module
from custom_components.bsv_settlement.auto_credit import AutomaticCredits
from custom_components.bsv_settlement.budget import SPENDING_STATE, SessionBudgets
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.ocpp_export_shadow_ledger import (
    MAX_EVENTS, MAX_SPANS, ExportShadowLedger, validate_section)
from custom_components.bsv_settlement.ongoing_credit import OngoingCredits
from custom_components.bsv_settlement.proxy import ProxyCoordinator, merge_rows, normalize
from custom_components.bsv_settlement.proxy_ledger import build_records
from custom_components.bsv_settlement.summary_window import (
    approval_unresolved, collection_unresolved, credit_unresolved, review_unresolved,
    route_unresolved, window)
from custom_components.bsv_settlement.tariff_provenance import MAX_SESSIONS, TariffProvenanceLedger
from test_ocpp_export_shadow import BINDING as EXPORT_BINDING, simulate, snap, stamp
from test_proxy import config_entry, load_registries

BASE = datetime(2026, 10, 1, 8, 0, tzinfo=timezone(timedelta(hours=10)))
WINDOW = 20  # Dashboard display window shared by the route summaries.


def t(minutes):
    return (BASE + timedelta(minutes=minutes)).isoformat()


def sessions(first, count):
    """Charging/Idle pairs ten minutes apart: one ended proxy session each."""
    return [row for n in range(first, first + count)
            for row in ({"t": t(10 * n), "value": "Charging"}, {"t": t(10 * n + 5), "value": "Idle"})]


def observations(count):
    meter = [{"t": t(-1), "value": "1", "unit": "MWh"}]
    return {"state": sessions(0, count), "import": deepcopy(meter), "export": deepcopy(meter),
            "import_price": [], "export_price": []}


def recorder(hass, rows=None, archive=None):
    """Real, unloaded ProxyCoordinator with every source present (no issues)."""
    proxy = ProxyCoordinator(hass, config_entry())
    units = {"import": "MWh", "export": "MWh", "import_price": "$/kWh", "export_price": "$/kWh"}
    for key, entity in proxy.sources.items():
        hass.states.async_set(entity, "Idle" if key == "state" else "1",
                              {"unit_of_measurement": units[key]} if key in units else {})
    if rows is not None:
        proxy.observations = rows
    proxy.archive = archive or []
    return proxy


@pytest_asyncio.fixture
async def hass(tmp_path):
    dt_util.set_default_time_zone(dt_util.get_time_zone("Australia/Brisbane"))
    instance = HomeAssistant(str(tmp_path / "ha"))
    await load_registries(instance)
    yield instance
    await instance.async_stop(force=True)


# --- Proxy summary archive -------------------------------------------------

@pytest.mark.asyncio
async def test_proxy_archive_keeps_exactly_newest_fifty_and_prunes_to_one_baseline(hass):
    proxy = recorder(hass, observations(60))
    data = await proxy._async_update_data()
    opened = [r["opened_at"] for r in proxy.archive]
    # 60 derived: latest and previous stay live, the 50 before them are archived.
    assert opened == [t(10 * n) for n in range(8, 58)]
    assert data["latest_session"]["opened_at"] == t(590)
    assert data["previous_session"]["opened_at"] == t(580)
    assert len({r["session_id"] for r in proxy.archive}) == 50
    assert not data["issues"]
    # Observations: one baseline per source before the cut, every row since.
    state = proxy.observations["state"]
    assert state[0] == {"t": t(575), "value": "Idle"}
    assert [r["t"] for r in state[1:5]] == [t(580), t(585), t(590), t(595)]
    for key in ("import", "export"):
        assert proxy.observations[key][0] == {"t": t(-1), "value": "1", "unit": "MWh"}
    # More sessions roll the window: oldest dropped first, no duplicates.
    proxy.observations["state"] = merge_rows(proxy.observations["state"], sessions(60, 3))
    data = await proxy._async_update_data()
    assert [r["opened_at"] for r in proxy.archive] == [t(10 * n) for n in range(11, 61)]
    assert data["latest_session"]["opened_at"] == t(620)
    # The same retained rows re-derive the same identities (stable session_id).
    again = build_records(observations(60), proxy.sources["state"], data["updated_at"])
    assert [r["session_id"] for r in again[11:58]] == [r["session_id"] for r in proxy.archive[:47]]


@pytest.mark.asyncio
async def test_rederived_session_replaces_its_archived_summary_in_place(hass):
    rows = observations(5)
    derived = build_records(rows, "sensor.proxy_state", t(100))
    stale = {**derived[1], "net_cost_aud_unrounded": "9.99", "quality_flags": ["stale"]}
    older = {**derived[0], "session_id": "sigen-proxy-fictional-older", "opened_at": t(-200)}
    proxy = recorder(hass, rows, archive=[older, stale])
    await proxy._async_update_data()
    ids = [r["session_id"] for r in proxy.archive]
    # Deduped by session_id; the re-derived record keeps the stale record's slot.
    assert ids == [older["session_id"], derived[1]["session_id"],
                   derived[0]["session_id"], derived[2]["session_id"]]
    assert proxy.archive[1]["quality_flags"] != ["stale"]
    # A summary that can no longer be re-derived is retained as stored, never rewritten.
    assert proxy.archive[0] == older


@pytest.mark.asyncio
async def test_previous_session_falls_back_to_the_archive(hass):
    archived = build_records(observations(2), "sensor.proxy_state", t(100))
    one = {**observations(0), "state": sessions(5, 1)}
    proxy = recorder(hass, one, archive=deepcopy(archived))
    data = await proxy._async_update_data()
    assert data["latest_session"]["opened_at"] == t(50)
    assert data["previous_session"] == archived[-1]
    # Two or fewer derived sessions never touch the archive or prune rows.
    assert proxy.archive == archived and proxy.observations["state"][0]["t"] == t(50)
    proxy = recorder(hass, observations(0), archive=deepcopy(archived))
    data = await proxy._async_update_data()
    assert data["latest_session"] is None and data["previous_session"] == archived[-1]


@pytest.mark.asyncio
async def test_observation_cap_is_a_persistent_degrading_issue(hass, monkeypatch):
    monkeypatch.setattr(proxy_module, "MAX_ROWS", 4)
    proxy = recorder(hass, observations(1))
    for n in range(5):
        proxy._append("import", {"t": t(20 + n), "value": f"1.00{n}", "unit": "MWh"})
    assert len(proxy.observations["import"]) == 4
    assert "observation_limit_reached" in proxy.issues
    data = await proxy._async_update_data()
    assert data["state"] == "degraded" and "observation_limit_reached" in data["issues"]
    assert data["latest_session"]["net_cost_aud"] is None
    # Transient issues are not persisted; the two persistent ones are.
    proxy.issues |= {"import:history_unavailable", "history_backfill_failed"}
    await proxy.persist()
    saved = await proxy.store.async_load()
    assert saved["persistent_issues"] == ["observation_limit_reached"]
    assert set(saved) == {"observations", "archive", "persistent_issues", "checkpoint_at",
                          "tariff_provenance"}
    # A checkpoint older than the 24 h backfill adds the restart-gap issue on load.
    saved["checkpoint_at"] = (dt_util.utcnow() - timedelta(hours=30)).isoformat()
    await proxy.store.async_save(saved)

    async def history(*args, **kwargs):
        return {}
    monkeypatch.setattr("homeassistant.components.recorder.get_instance",
                        lambda hass: SimpleNamespace(async_add_executor_job=history))
    restored = ProxyCoordinator(hass, proxy.entry)
    try:
        await restored.load()
        assert {"observation_limit_reached", "restart_gap_exceeds_24_hour_backfill"} <= restored.issues
        # Restored: the loss start is unknown. Fresh gap: from the checkpoint.
        assert restored.issue_window["observation_limit_reached"] == (None, None)
        assert restored.issue_window["restart_gap_exceeds_24_hour_backfill"][0] == saved["checkpoint_at"]
        restored._append("import", {"t": t(900), "value": "2", "unit": "MWh"})
        assert restored.issue_window["observation_limit_reached"] == (None, None)
        assert restored.archive == saved["archive"]
        await restored.persist()
        assert (await restored.store.async_load())["persistent_issues"] == [
            "observation_limit_reached", "restart_gap_exceeds_24_hour_backfill"]
    finally:
        await restored.close()


def wallet(hass, pins):
    """A loaded mainnet coordinator as the proxy sees it: only the pin API."""
    from custom_components.bsv_settlement.const import DOMAIN
    coordinator = SimpleNamespace(mode="embedded_mainnet", unresolved_proxy_sessions=lambda pid: set(pins))
    hass.data.setdefault(DOMAIN, {})["wallet-entry"] = coordinator
    return coordinator


def grow(proxy, first, count):
    proxy.observations["state"] = merge_rows(proxy.observations["state"], sessions(first, count))


@pytest.mark.asyncio
async def test_referenced_session_is_pinned_beyond_fifty_and_expires_once_resolved(hass):
    proxy = recorder(hass, observations(60))
    first = build_records(observations(60), proxy.sources["state"], t(0))[0]["session_id"]
    pins = {first}
    wallet(hass, pins)
    data = await proxy._async_update_data()
    ids = [r["session_id"] for r in proxy.archive]
    assert ids[0] == first and len(ids) == 51
    assert [r["opened_at"] for r in proxy.archive[1:]] == [t(10 * n) for n in range(8, 58)]
    assert data["archive_retention"]["pinned_beyond_window"] == 1 and not data["warnings"]
    grow(proxy, 60, 60)
    data = await proxy._async_update_data()
    assert proxy.archive[0]["session_id"] == first and len(proxy.archive) == 51
    assert [r["opened_at"] for r in proxy.archive[1:]] == [t(10 * n) for n in range(68, 118)]
    # Persisted as the same list of summary dicts: the previous release loads it.
    await proxy.persist()
    saved = await proxy.store.async_load()
    assert saved["archive"] == proxy.archive and set(saved) == {
        "observations", "archive", "persistent_issues", "checkpoint_at", "tariff_provenance"}
    assert all(set(r) == set(proxy.archive[-1]) for r in saved["archive"])
    # Resolution unpins it; the next prune applies the plain newest-50 window.
    pins.clear()
    grow(proxy, 120, 1)
    await proxy._async_update_data()
    assert first not in {r["session_id"] for r in proxy.archive}
    assert [r["opened_at"] for r in proxy.archive] == [t(10 * n) for n in range(69, 119)]


@pytest.mark.asyncio
async def test_unreferenced_archive_keeps_newest_fifty_with_a_wallet_loaded(hass):
    wallet(hass, {"sigen-proxy-not-retained"})
    proxy = recorder(hass, observations(60))
    data = await proxy._async_update_data()
    assert [r["opened_at"] for r in proxy.archive] == [t(10 * n) for n in range(8, 58)]
    assert data["warnings"] == ["referenced_session_not_retained"] and not data["issues"]
    assert data["archive_retention"]["referenced_not_retained"] == 1


@pytest.mark.asyncio
async def test_pinned_session_lookups_succeed_after_sixty_newer(hass):
    from custom_components.bsv_settlement.collection import DriverCollections
    from custom_components.bsv_settlement.const import DOMAIN
    from custom_components.bsv_settlement.session_closure import ClosedSessions
    from custom_components.bsv_settlement.session_review import SessionReviews
    proxy = recorder(hass, observations(3))
    proxy.async_set_updated_data(await proxy._async_update_data())
    pinned = proxy.archive[0]["session_id"]
    wallet(hass, {pinned})
    grow(proxy, 3, 60)
    proxy.async_set_updated_data(await proxy._async_update_data())
    assert pinned in {r["session_id"] for r in proxy.archive}
    hass.data[DOMAIN][proxy.entry.entry_id] = proxy

    async def refresh():
        proxy.async_set_updated_data(await proxy._async_update_data())
    proxy.async_request_refresh = refresh
    reviews = SessionReviews.__new__(SessionReviews)
    reviews.hass = hass
    with pytest.raises(Exception) as found:
        await reviews.source(proxy.entry.entry_id, pinned)
    # Found; refused only on its own (fictional, unpriced) account quality.
    assert "retained history" not in str(found.value)
    collections = DriverCollections(SimpleNamespace(saved={}, hass=hass))
    collections.session_id = lambda row: row["terms"]["session_id"]
    row = {"proxy_config_entry_id": proxy.entry.entry_id, "terms": {"session_id": pinned}}
    assert (await collections.source(row))["session_id"] == pinned
    api = SimpleNamespace(hass=hass, saved={"session_budgets": {}, "closed_sessions": {}})
    with pytest.raises(Exception) as found:
        await ClosedSessions(api).inspect({"proxy_config_entry_id": proxy.entry.entry_id,
                                           "session_id": pinned})
    assert "retained history" not in str(found.value)


@pytest.mark.asyncio
async def test_unknown_references_pause_eviction_and_warn(hass):
    from custom_components.bsv_settlement.const import DOMAIN
    proxy = recorder(hass, observations(60))
    await proxy._async_update_data()
    before = [r["session_id"] for r in proxy.archive]

    def broken(pid):
        raise RuntimeError("fictional ledger fault")
    hass.data.setdefault(DOMAIN, {})["wallet-entry"] = SimpleNamespace(
        mode="embedded_mainnet", unresolved_proxy_sessions=broken)
    grow(proxy, 60, 3)
    data = await proxy._async_update_data()
    assert [r["session_id"] for r in proxy.archive][:50] == before
    assert len(proxy.archive) == 53 and "session_references_unavailable" in data["warnings"]
    assert not data["issues"]


@pytest.mark.asyncio
async def test_unloaded_mainnet_entry_pauses_eviction(hass):
    from homeassistant.config_entries import ConfigEntries
    from test_recorder_readiness import config_entry as make_entry
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    pending = make_entry("bsv_settlement", {"backend": "embedded_mainnet"})
    hass.config_entries._entries[pending.entry_id] = pending
    proxy = recorder(hass, observations(60))
    await proxy._async_update_data()
    grow(proxy, 60, 2)
    data = await proxy._async_update_data()
    assert len(proxy.archive) == 52 and data["archive_retention"]["references_complete"] is False


@pytest.mark.asyncio
async def test_pinned_overflow_keeps_newest_pinned_raises_issue_and_warns_near_limit(hass, monkeypatch):
    monkeypatch.setattr(proxy_module, "MAX_PINNED", 7)
    proxy = recorder(hass, observations(60))
    every = {r["session_id"] for r in build_records(observations(60), proxy.sources["state"], t(0))}
    wallet(hass, every)
    await proxy._async_update_data()
    # 58 archivable: newest 50 plus the newest 7 of the 8 older pinned.
    assert [r["opened_at"] for r in proxy.archive] == [t(10 * n) for n in range(1, 58)]
    assert "pinned_session_limit_exceeded" in proxy.issues
    await proxy.persist()
    assert "pinned_session_limit_exceeded" in (await proxy.store.async_load())["persistent_issues"]
    data = await proxy._async_update_data()
    assert "pinned_session_limit_exceeded" in data["issues"] and data["state"] == "degraded"
    assert "pinned_sessions_near_limit" in data["warnings"]
    assert proxy_module.retain([{"session_id": str(n)} for n in range(6)], {"0", "1", "2"},
                               limit=2, max_pinned=2) == (
        [{"session_id": n} for n in ("1", "2", "4", "5")], [{"session_id": "0"}])


def test_wallet_references_count_only_non_terminal_records():
    from custom_components.bsv_settlement.coordinator import SettlementCoordinator
    from custom_components.bsv_settlement.session_references import unresolved_proxy_sessions
    future, past = "2999-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"

    def budget(bid, sid, expires=future, state="spending_authorised"):
        return {"proxy_config_entry_id": "P", "state": state,
                "terms": {"budget_id": bid, "session_id": sid, "expires_at": expires}}
    saved = {
        "session_reviews": {
            "r1": {"proxy_config_entry_id": "P", "state": "awaiting_account_approval",
                   "account": {"session_id": "s-review"}},
            "r2": {"proxy_config_entry_id": "P", "state": "cancelled", "account": {"session_id": "s-cancelled"}},
            "r3": {"proxy_config_entry_id": "P", "state": "credit_review_approved",
                   "credit_draft_id": "d1", "account": {"session_id": "s-credited"}},
            "r4": {"proxy_config_entry_id": "Q", "state": "awaiting_account_approval",
                   "account": {"session_id": "s-other-proxy"}},
            "r5": {"proxy_config_entry_id": "P", "state": "awaiting_account_approval",
                   "account_kind": "manual_energy_adjustment", "account": {"session_id": "s-adjust"}},
        },
        "payments": {"d1": {"state": "provider_confirmed"}},
        "session_budgets": {
            "b1": budget("b1", "s-budget"), "b2": budget("b2", "s-expired", past),
            "b3": budget("b3", "s-revoked", state="revoked"), "b4": budget("b4", "s-collected"),
            "b5": budget("b5", "s-stale-collection", past), "b6": budget("b6", "s-paying", past),
        },
        "driver_collections": {"b4": {"state": "provider_confirmed"}, "b5": {"state": "ready"},
                               "b6": {"state": "broadcast_unknown"}},
        "driver_collection_index": {"P|s-collected": "b4", "P|s-stale-collection": "b5",
                                    "P|s-paying": "b6"},
        "automatic_credits": {"c1": {"state": "credit_queued"}, "c2": {"state": "provider_confirmed"}},
        "automatic_credit_index": {"P|s-credit": "c1", "P|s-credit-done": "c2", "P|s-missing": "c9"},
        "ongoing_credit_routes": {"ongoing:P|s-route": {"state": "waiting_for_session_end"},
                                  "ongoing:P|s-route-none": {"state": "no_operator_credit"}},
        "monthly_authorities": {"schema": "monthly-authorities-v1",
                                "bindings": {"P|s-monthly": {"authority_id": "a"}}},
        "closed_sessions": {"P|s-closed": {"state": "waived"}},
    }
    api = SimpleNamespace(saved=saved, collections=SimpleNamespace(
        session_id=lambda row: row["terms"].get("session_id")))
    expected = {"s-review", "s-budget", "s-paying", "s-credit", "s-missing", "s-route", "s-monthly"}
    assert unresolved_proxy_sessions(api, "P") == expected
    coordinator = SettlementCoordinator.__new__(SettlementCoordinator)
    coordinator.api, coordinator.mode = api, "embedded_mainnet"
    assert coordinator.unresolved_proxy_sessions("P") == expected
    assert coordinator.unresolved_proxy_sessions("Q") == {"s-other-proxy"}


def capped(proxy):
    """Fill import to the (patched) cap of 8; the last two rows are lost."""
    for n in range(9):
        proxy._append("import", {"t": t(20 + n), "value": f"1.00{n}", "unit": "MWh"})
    assert "observation_limit_reached" in proxy.issues


@pytest.mark.asyncio
async def test_observation_limit_acknowledgement_needs_room_and_keeps_overlap_flagged(hass, monkeypatch):
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.exceptions import HomeAssistantError
    monkeypatch.setattr(proxy_module, "MAX_ROWS", 8)
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    proxy = recorder(hass, {**observations(0), "state": [
        {"t": t(15), "value": "Charging"}, {"t": t(30), "value": "Idle"}]})
    hass.config_entries._entries[proxy.entry.entry_id] = proxy.entry
    capped(proxy)
    assert proxy.issue_window["observation_limit_reached"] == (t(27), None)
    with pytest.raises(HomeAssistantError, match="administrator"):
        await proxy.execute("acknowledge_proxy_issue", {"issue": "observation_limit_reached"})
    with pytest.raises(HomeAssistantError, match="still holds 8 observations"):
        await proxy.execute("acknowledge_proxy_issue", {"issue": "observation_limit_reached"}, "admin-user")
    assert "observation_limit_reached" in proxy.issues
    with pytest.raises(HomeAssistantError, match="read-only"):
        await proxy.execute("acknowledge_proxy_issue_everything", {}, "admin-user")
    with pytest.raises(HomeAssistantError, match="cannot be acknowledged"):
        await proxy.execute("acknowledge_proxy_issue", {"issue": "state:unavailable"}, "admin-user")
    # Three more sessions prune import below the cap.
    grow(proxy, 5, 3)
    data = await proxy.execute("acknowledge_proxy_issue", {"issue": "observation_limit_reached"}, "admin-user")
    assert all(len(rows) < 8 for rows in proxy.observations.values())
    assert "observation_limit_reached" not in data["issues"] and data["state"] != "degraded"
    window = proxy.entry.data["recorder_issue_windows"][0]
    assert window["issue"] == "observation_limit_reached" and window["since"] == t(27)
    assert window["acknowledged_by"] == "admin-user"
    assert data["acknowledged_issues"] == [{k: window[k] for k in ("issue", "since", "until", "acknowledged_at")}]
    # The loss ran from the first refused row until the prune made room. Every
    # session in it (here all, as fictional time is in the past) stays flagged.
    assert window["until"] != window["acknowledged_at"]
    overlapping = next(r for r in proxy.archive if r["opened_at"] == t(15))
    assert "observation_limit_reached" in overlapping["quality_flags"]
    assert all("observation_limit_reached" in r["quality_flags"]
               for r in (data["latest_session"], data["previous_session"]))
    later = dt_util.parse_datetime(window["until"]) + timedelta(minutes=1)
    assert proxy._flag({"opened_at": later.isoformat(), "ended_at": None,
                        "quality_flags": []})["quality_flags"] == []
    assert proxy._flag({"opened_at": t(0), "ended_at": t(26),
                        "quality_flags": []})["quality_flags"] == []
    from custom_components.bsv_settlement.session_review import account_snapshot
    with pytest.raises(Exception, match="observation_limit_reached"):
        account_snapshot({**overlapping, "net_cost_aud_unrounded": "0", "unpriced_import_wh": "0",
                          "unpriced_export_wh": "0"})
    # Persisted: the store keeps no new keys, the entry keeps the window.
    await proxy.persist()
    saved = await proxy.store.async_load()
    assert "observation_limit_reached" not in saved["persistent_issues"]
    assert set(saved) == {"observations", "archive", "persistent_issues", "checkpoint_at", "tariff_provenance"}
    assert "observation_limit_reached" in next(
        r for r in saved["archive"] if r["opened_at"] == t(15))["quality_flags"]
    restarted = ProxyCoordinator(hass, proxy.entry)
    assert restarted.windows == proxy.windows
    assert "observation_limit_reached" in restarted._flag(
        {"opened_at": t(15), "ended_at": t(30), "quality_flags": []})["quality_flags"]
    with pytest.raises(HomeAssistantError, match="not raised"):
        await proxy.execute("acknowledge_proxy_issue", {"issue": "observation_limit_reached"}, "admin-user")


@pytest.mark.asyncio
async def test_restored_limit_without_window_flags_every_session_opened_before_acknowledgement(hass, monkeypatch):
    proxy = recorder(hass, observations(3))
    proxy.issues.add("observation_limit_reached")  # Restored from the store: start unknown.
    data = await proxy.execute("acknowledge_proxy_issue", {"issue": "observation_limit_reached"}, "admin")
    assert proxy.windows[0]["since"] is None
    assert "observation_limit_reached" in data["latest_session"]["quality_flags"]
    assert "observation_limit_reached" not in proxy._flag(
        {"opened_at": "2999-01-01T00:00:00+00:00", "ended_at": None, "quality_flags": []})["quality_flags"]


@pytest.mark.asyncio
async def test_restart_gap_acknowledgement_flags_only_sessions_spanning_the_gap(hass, monkeypatch):
    proxy = recorder(hass, observations(0))
    gap_from, gap_to = BASE + timedelta(minutes=100), BASE + timedelta(minutes=200)
    proxy.issues.add("restart_gap_exceeds_24_hour_backfill")
    proxy.issue_window["restart_gap_exceeds_24_hour_backfill"] = (gap_from.isoformat(), gap_to.isoformat())
    await proxy.execute("acknowledge_proxy_issue", {"issue": "restart_gap_exceeds_24_hour_backfill"}, "admin")
    assert "restart_gap_exceeds_24_hour_backfill" not in proxy.issues

    def flags(opened, ended):
        return proxy._flag({"opened_at": t(opened), "ended_at": ended and t(ended),
                            "quality_flags": []})["quality_flags"]
    assert flags(90, 110) == flags(150, None) == flags(190, 260) == ["restart_gap_exceeds_24_hour_backfill"]
    assert flags(10, 90) == flags(210, 230) == []


@pytest.mark.asyncio
async def test_restart_gap_window_recorded_on_load(hass, monkeypatch):
    proxy = recorder(hass, observations(1))
    await proxy.persist()
    saved = await proxy.store.async_load()
    checkpoint = (dt_util.utcnow() - timedelta(hours=30)).isoformat()
    await proxy.store.async_save({**saved, "checkpoint_at": checkpoint})

    async def history(*args, **kwargs):
        return {}
    monkeypatch.setattr("homeassistant.components.recorder.get_instance",
                        lambda hass: SimpleNamespace(async_add_executor_job=history))
    restored = ProxyCoordinator(hass, proxy.entry)
    await restored.load()
    try:
        since, until = restored.issue_window["restart_gap_exceeds_24_hour_backfill"]
        # Empty fictional history: the backfill fell short, so the gap runs to load time.
        assert since == checkpoint and abs(dt_util.parse_datetime(until) - dt_util.utcnow()) < timedelta(minutes=1)
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_pinned_limit_acknowledgement_and_admin_service_boundary(hass, monkeypatch):
    from unittest.mock import AsyncMock
    from homeassistant.core import Context
    from homeassistant.exceptions import HomeAssistantError
    from custom_components.bsv_settlement import async_setup
    from custom_components.bsv_settlement.const import DOMAIN
    proxy = recorder(hass, observations(3))
    proxy.issues.add("pinned_session_limit_exceeded")
    await async_setup(hass, {})
    hass.data[DOMAIN][proxy.entry.entry_id] = proxy
    call = {"config_entry_id": proxy.entry.entry_id, "issue": "pinned_session_limit_exceeded"}
    hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
    for ctx in (Context(), Context(user_id="non-admin")):
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call(DOMAIN, "acknowledge_proxy_issue", call, context=ctx, blocking=True)
    assert "pinned_session_limit_exceeded" in proxy.issues
    hass.auth.async_get_user.return_value.is_admin = True
    result = await hass.services.async_call(DOMAIN, "acknowledge_proxy_issue", call,
                                            context=Context(user_id="admin"), blocking=True,
                                            return_response=True)
    assert "pinned_session_limit_exceeded" not in result["issues"] and proxy.windows == []
    # Match by class name: a voluptuous shim in sys.modules can make `vol.Invalid` a different object.
    with pytest.raises(Exception) as refused:
        await hass.services.async_call(DOMAIN, "acknowledge_proxy_issue", {**call, "issue": "source_binding_changed"},
                                       context=Context(user_id="admin"), blocking=True)
    assert "Invalid" in {c.__name__ for c in type(refused.value).__mro__}


@pytest.mark.asyncio
async def test_expired_session_fails_closed_for_review_and_collection(hass):
    """Once a session leaves latest/previous/archive it cannot be re-sourced.

    Review and collection sourcing refuse explicitly; the ledger row stays and
    nothing is paid, re-priced or dropped.
    """
    from custom_components.bsv_settlement.api import WalletError
    from custom_components.bsv_settlement.const import DOMAIN
    from custom_components.bsv_settlement.session_review import SessionReviews
    proxy = recorder(hass, observations(60))
    proxy.async_set_updated_data(await proxy._async_update_data())
    gone = build_records(observations(60), proxy.sources["state"], t(700))[0]["session_id"]
    assert gone not in {r["session_id"] for r in proxy.archive}
    hass.data.setdefault(DOMAIN, {})[proxy.entry.entry_id] = proxy

    async def refresh():
        proxy.async_set_updated_data(await proxy._async_update_data())
    proxy.async_request_refresh = refresh
    reviews = SessionReviews.__new__(SessionReviews)
    reviews.hass = hass
    with pytest.raises(WalletError, match="retained history"):
        await reviews.source(proxy.entry.entry_id, gone)
    kept = proxy.archive[0]["session_id"]
    assert (await reviews.source(proxy.entry.entry_id, kept))["session_id"] == kept
    from custom_components.bsv_settlement.collection import DriverCollections
    collections = DriverCollections(SimpleNamespace(saved={}, hass=hass))
    row = {"proxy_config_entry_id": proxy.entry.entry_id,
           "terms": {"session_id": gone, "session_mode": "single_session"}}
    collections.session_id = lambda row: row["terms"]["session_id"]
    with pytest.raises(WalletError, match="not in retained history"):
        await collections.source(row)
    assert (await collections.source({**row, "terms": {"session_id": kept}}))["session_id"] == kept


# --- Display windows on wallet route summaries -------------------------------
# Every unresolved item stays visible however old; resolved items fill the
# rest of the 20-row window, newest kept, in insertion order (#105). Records
# are never touched; totals count the ledger, not the window.

def unresolved_first(count, state="broadcast_unknown"):
    """Oldest item is unresolved; newer ones are settled. Insertion order = age."""
    return {f"b{n:02d}": {"state": state if n == 0 else "provider_confirmed",
                          "budget_id": f"b{n:02d}", "session_id": f"s{n:02d}"}
            for n in range(count)}


def test_automatic_credit_summary_keeps_old_unresolved_and_caps_resolved():
    api = SimpleNamespace(saved={})
    credits = AutomaticCredits(api)
    api.saved["automatic_credits"] = unresolved_first(WINDOW + 5)
    before = deepcopy(api.saved)
    summary = credits.summary()
    shown = [p["budget_id"] for p in summary["payments"]]
    assert shown == ["b00"] + [f"b{n:02d}" for n in range(6, WINDOW + 5)]
    assert summary["payments_window"] == {"total": WINDOW + 5, "shown": WINDOW, "unresolved": 1}
    assert api.saved == before and api.saved["automatic_credits"]["b00"]["state"] == "broadcast_unknown"
    assert credits.get({"terms": {"budget_id": "b00"}})["state"] == "broadcast_unknown"


def test_payment_summary_keeps_old_unresolved_reviews_and_collections():
    reviews = {f"r{n:02d}": {"review_id": f"r{n:02d}", "expires_at": "2099-01-01T00:00:00+00:00",
                             "state": "awaiting_driver_payment" if n == 0 else "no_payment_due"}
               for n in range(WINDOW + 3)}
    collections = unresolved_first(WINDOW + 3, "submitted")
    api = SimpleNamespace(
        saved={"session_reviews": reviews, "driver_collections": collections,
               "session_budgets": {k: {"terms": {"transaction_id": "tx-" + k}} for k in collections}},
        reviews=SimpleNamespace(public=lambda r: {
            "account": {"session_id": "s-" + r["review_id"], "ocpp_transaction_id": "tx"},
            "state": r["state"], "direction": "operator_to_driver", "amount_sats": 1}),
        collections=SimpleNamespace(session_id=lambda row: "s"))
    before = deepcopy(api.saved)
    rows, counts = MainnetWalletAPI.payment_summary_window(api)
    assert rows == MainnetWalletAPI.payment_summary(api)
    assert [r["review_id"] for r in rows if "review_id" in r] == [
        "r00"] + [f"r{n:02d}" for n in range(4, WINDOW + 3)]
    assert [r["budget_id"] for r in rows if "budget_id" in r] == [
        "b00"] + [f"b{n:02d}" for n in range(4, WINDOW + 3)]
    assert counts == {"total": 2 * (WINDOW + 3), "shown": 2 * WINDOW, "unresolved": 2}
    assert api.saved == before and "r00" in api.saved["session_reviews"]
    assert "b00" in api.saved["driver_collections"]


def test_budget_summary_keeps_live_approvals_and_caps_resolved():
    api = SimpleNamespace(saved={"session_budgets": {}},
                          collections=SimpleNamespace(session_id=lambda row: None, get=lambda row: None))
    budgets = SessionBudgets(api)
    for n in range(WINDOW + 4):
        api.saved["session_budgets"][f"b{n:02d}"] = {
            "state": "awaiting_driver_consent" if n == 0 else "revoked", "terms": {
                "budget_id": f"b{n:02d}", "version": 1, "expires_at": "2099-01-01T00:00:00+00:00",
                "created_at": t(n), "max_total_sats": 10, "satoshis_per_aud": "100"}}
    api.saved["session_budgets"]["weekly-child"] = {
        "state": "awaiting_driver_consent", "weekly_parent_id": "b00", "terms": {}}
    before = deepcopy(api.saved)
    rows, counts = budgets.summary_window()
    assert [r["budget_id"] for r in rows] == ["b00"] + [f"b{n:02d}" for n in range(5, WINDOW + 4)]
    assert [r["budget_id"] for r in budgets.summary()] == [r["budget_id"] for r in rows]
    assert counts == {"total": WINDOW + 4, "shown": WINDOW, "unresolved": 1}
    assert api.saved == before and len(api.saved["session_budgets"]) == WINDOW + 5


def test_expired_approval_with_unresolved_collection_stays_visible():
    items = {"old": {"state": "broadcast_unknown"}, "dead": {"state": "ready"}}
    api = SimpleNamespace(saved={"session_budgets": {}}, collections=SimpleNamespace(
        session_id=lambda row: None, public=dict,
        get=lambda row: items.get(row["terms"]["budget_id"])))
    budgets = SessionBudgets(api)
    for key in ("old", "dead", *(f"n{n:02d}" for n in range(WINDOW))):
        api.saved["session_budgets"][key] = {"state": SPENDING_STATE, "terms": {
            "budget_id": key, "version": 2, "expires_at": "2000-01-01T00:00:00+00:00",
            "created_at": t(0), "max_total_sats": 10, "satoshis_per_aud": "100"}}
    before = deepcopy(api.saved)
    assert budgets.unresolved(api.saved["session_budgets"]["old"]) is True
    assert budgets.unresolved(api.saved["session_budgets"]["dead"]) is False
    rows, counts = budgets.summary_window()
    assert [r["budget_id"] for r in rows] == ["old"] + [f"n{n:02d}" for n in range(1, WINDOW)]
    assert rows[0]["state"] == "expired"
    assert counts == {"total": WINDOW + 2, "shown": WINDOW, "unresolved": 1}
    assert api.saved == before


def test_ongoing_routes_keep_old_unresolved_and_cap_resolved():
    api = SimpleNamespace(saved={"automatic_credits": {}})
    ongoing = OngoingCredits(api)
    api.auto_credits = SimpleNamespace(policy={"enabled": True})
    for n in range(WINDOW + 2):
        rid = f"ongoing:p|s{n:02d}"
        ongoing.routes[rid] = {
            "route_id": rid, "proxy_config_entry_id": "p", "session_id": f"s{n:02d}",
            "transaction_id": "tx", "state": "credit_queued" if n == 0 else "no_operator_credit",
            "satoshis_per_aud": "100", "assigned_at": t(n),
            "recipient": {"address": "fictional", "driver_identity": "02" + "11" * 32, "budget_id": "rb"}}
    before = deepcopy(api.saved)
    expected = ["s00"] + [f"s{n:02d}" for n in range(3, WINDOW + 2)]
    summary = ongoing.summary()
    assert [r["session_id"] for r in summary["sessions"]] == expected
    assert summary["sessions_window"] == {"total": WINDOW + 2, "shown": WINDOW, "unresolved": 1}
    assert [r["session_id"] for r in ongoing.driver_rows({"terms": {"budget_id": "rb"}})] == expected
    assert api.saved == before and ongoing.routes["ongoing:p|s00"]["state"] == "credit_queued"


def test_window_shows_every_unresolved_item_beyond_the_limit():
    rows = [{"n": n, "open": n % 2 == 0} for n in range(3 * WINDOW)]
    shown, counts = window(rows, lambda r: r["open"])
    assert [r["n"] for r in shown] == list(range(0, 3 * WINDOW, 2))
    assert counts == {"total": 3 * WINDOW, "shown": 3 * WINDOW // 2, "unresolved": 3 * WINDOW // 2}
    shown, counts = window([{"n": 1}, {"n": 2}], lambda r: r["missing"])  # Raises: fail visible.
    assert len(shown) == 2 and counts["unresolved"] == 2
    assert window([], lambda r: True) == ([], {"total": 0, "shown": 0, "unresolved": 0})


@pytest.mark.parametrize("state,expected", [
    ("credit_queued", True), ("credit_review_required", True), ("broadcast_unknown", True),
    ("submitted", True), ("provider_unconfirmed", True), ("provider_confirmed", False),
    ("fictional_future_state", True), (None, True)])
def test_credit_unresolved_states(state, expected):
    assert credit_unresolved({"state": state}) is expected


@pytest.mark.parametrize("route,item,expected", [
    ("waiting_for_session_end", None, True), ("credit_blocked", None, True),
    ("credit_queued", None, True), ("no_operator_credit", None, False),
    ("fictional_future_state", None, True),
    ("credit_queued", "provider_confirmed", False), ("provider_confirmed", "broadcast_unknown", True),
    ("no_operator_credit", "credit_review_required", True), ("x", "fictional_future_state", True)])
def test_route_unresolved_states(route, item, expected):
    assert route_unresolved({"state": route}, item and {"state": item}) is expected


@pytest.mark.parametrize("state,approval,expected", [
    ("ready", SPENDING_STATE, True), ("ready", "expired", False), ("ready", "revoked", False),
    ("ready", "charge_waived", False), ("ready", None, True),
    ("wallet_attempt_reserved", "expired", True), ("recovery_ready", "expired", True),
    ("submission_authorised", "expired", True), ("broadcast_unknown", "revoked", True),
    ("submitted", None, True), ("provider_unconfirmed", None, True),
    ("provider_confirmed", SPENDING_STATE, False), ("fictional_future_state", "expired", True)])
def test_collection_unresolved_states(state, approval, expected):
    assert collection_unresolved({"state": state}, approval) is expected


DRAFT = {"state": "credit_review_approved", "credit_draft_id": "d"}


@pytest.mark.parametrize("review,payment,expired,expected", [
    ({"state": "awaiting_account_approval"}, None, False, True),
    ({"state": "awaiting_account_approval"}, None, True, False),
    ({"state": "credit_review_approved"}, None, True, False),
    ({"state": "awaiting_driver_payment", "payment_request": {}}, None, True, True),
    ({"state": "awaiting_driver_payment", "payment_request": {}}, None, False, True),
    ({"state": "driver_payment_evidence_unavailable"}, None, False, True),
    ({"state": "driver_payment_provider_unconfirmed"}, None, False, True),
    ({"state": "driver_payment_provider_confirmed"}, None, False, False),
    ({"state": "no_payment_due"}, None, False, False),
    ({"state": "cancelled", "credit_draft_id": "d"}, {"state": "expired"}, False, False),
    (DRAFT, None, False, True),
    (DRAFT, {"state": "prepared"}, True, True),
    (DRAFT, {"state": "broadcast_unknown"}, False, True),
    (DRAFT, {"state": "submitted"}, False, True),
    (DRAFT, {"state": "provider_unconfirmed"}, False, True),
    (DRAFT, {"state": "provider_confirmed"}, False, False),
    (DRAFT, {"state": "expired"}, False, False),
    (DRAFT, {"state": "cancelled_driver_changed"}, False, False),
    (DRAFT, {"state": "fictional_future_state"}, False, True),
    ({"state": "fictional_future_state"}, None, True, True)])
def test_review_unresolved_states(review, payment, expired, expected):
    assert review_unresolved(review, payment, expired) is expected


@pytest.mark.parametrize("state,settlements,settled,expected", [
    ("awaiting_driver_consent", (), False, True), (SPENDING_STATE, (), False, True),
    (SPENDING_STATE, (False,), True, False), (SPENDING_STATE, (True,), True, True),
    ("expired", (), False, False), ("revoked", (False, False), False, False),
    ("charge_waived", (), False, False), ("expired", (False, True), False, True),
    ("fictional_future_state", (), False, True)])
def test_approval_unresolved_states(state, settlements, settled, expected):
    assert approval_unresolved(state, settlements, settled) is expected


# --- Other bounded evidence stores ------------------------------------------

def test_tariff_provenance_evicts_oldest_captured_session_beyond_cap():
    ledger = TariffProvenanceLedger()
    for n in range(MAX_SESSIONS + 2):
        assert ledger.observe(f"s{n:03d}", {"n": n}, f"{n:064x}", t(n))
    sessions_kept = ledger.data["sessions"]
    assert len(sessions_kept) == MAX_SESSIONS
    assert "s000" not in sessions_kept and "s001" not in sessions_kept
    assert ledger.lookup("s000", f"{0:064x}") is None  # Now reported as not recorded.
    # The session being captured is never the one evicted, even if backdated.
    assert ledger.observe("late", {"n": -1}, "f" * 64, t(-10))
    assert "late" in ledger.data["sessions"]


def test_export_shadow_journal_and_spans_trim_with_flags_and_reload():
    ledger = ExportShadowLedger(binding=EXPORT_BINDING)
    register = Decimal(10)
    for n in range(MAX_SPANS * 4 + 10):
        base = n * 1000
        rows = simulate([(base + s, -7200) for s in range(0, 241, 60)], start=register)
        register = rows[-1]["register"]
        for row in rows:
            ledger.observe(snap(row, tx=str(100 + n)), stamp(row["second"] + .1))
        ledger.observe(snap(dict(rows[-1], second=base + 300, flow="idle"), tx=str(100 + n),
                            status="Available"), stamp(base + 300.1))
    summary = ledger.summary()
    assert len(ledger.data["spans"]) == MAX_SPANS and summary["spans_trimmed"]
    assert len(ledger.data["events"]) == MAX_EVENTS and summary["journal_trimmed"]
    assert ledger.data["spans"][-1]["native_transaction_id"] == str(100 + MAX_SPANS * 4 + 9)
    assert ledger.data["spans"][0]["native_transaction_id"] == str(100 + MAX_SPANS * 3 + 10)
    assert validate_section(deepcopy(ledger.data)) == ledger.data
    assert all(s["billing_eligible"] is False and s["settlement_owner"] is None
               for s in ledger.data["spans"])


# --- Source entity renames ---------------------------------------------------

@pytest.mark.asyncio
async def test_renamed_proxy_source_degrades_and_is_not_followed(hass):
    """A rename reads as unavailable; the proxy never follows the new entity_id."""
    proxy = recorder(hass, observations(3))
    assert not (await proxy._async_update_data())["issues"]
    old = proxy.sources["state"]
    hass.states.async_remove(old)
    hass.states.async_set("sensor.renamed_state", "Charging")
    proxy.snapshot_current()
    data = await proxy._async_update_data()
    assert data["state"] == "degraded" and "state:unavailable" in data["issues"]
    assert data["latest_session"]["net_cost_aud"] is None
    assert proxy.sources["state"] == old == proxy.entry.data["state_entity"]
    assert normalize(hass.states.get("sensor.renamed_state")) not in proxy.observations["state"]
    # Session IDs hash the configured state entity_id with the opening time, so
    # a replacement entry on the new entity_id derives different IDs.
    assert {r["session_id"] for r in build_records(observations(3), "sensor.renamed_state", t(100))}.isdisjoint(
        r["session_id"] for r in build_records(observations(3), old, t(100)))


async def registered_site(hass, monkeypatch, **extra):
    """Registered entry and registry rows for four sources; export_price has none."""
    from homeassistant.config_entries import ConfigEntries
    from homeassistant.helpers import entity_registry as er
    hass.config_entries = ConfigEntries(hass, {})
    await hass.config_entries.async_initialize()
    entry = config_entry(**extra)
    hass.config_entries._entries[entry.entry_id] = entry
    registry = er.async_get(hass)
    for key in ("import", "export", "state", "import_price"):
        registry.async_get_or_create("sensor", "fictional_inverter", "uid-" + key,
                                     suggested_object_id="proxy_" + key)

    async def history(*args, **kwargs):
        return {}
    monkeypatch.setattr("homeassistant.components.recorder.get_instance",
                        lambda hass: SimpleNamespace(async_add_executor_job=history))
    proxy = ProxyCoordinator(hass, entry)
    recorder_states(hass, proxy)
    return proxy


def recorder_states(hass, proxy):
    units = {"import": "MWh", "export": "MWh", "import_price": "$/kWh", "export_price": "$/kWh"}
    for key, entity in proxy.sources.items():
        hass.states.async_set(entity, "Idle" if key == "state" else "1",
                              {"unit_of_measurement": units[key]} if key in units else {})


@pytest.mark.asyncio
async def test_proxy_adopts_source_identity_once_and_keeps_it_across_restart(hass, monkeypatch):
    proxy = await registered_site(hass, monkeypatch)
    entry = proxy.entry
    assert "source_identity" not in entry.data
    await proxy.load()
    try:
        pinned = entry.data["source_identity"]
        adopted = entry.data["source_binding_adopted_at"]
        assert pinned["import"] == {"entity_id": "sensor.proxy_import", "platform": "fictional_inverter",
                                    "unique_id": "uid-import", "config_entry_id": None}
        assert pinned["export_price"] == {"entity_id": "sensor.proxy_export_price", "unregistered": True}
        proxy.issues.clear()  # Fictional empty history; unrelated to identity.
        data = await proxy._async_update_data()
        assert not data["issues"] and data["state"] != "degraded"
        assert data["source_identity"] == {**{k: "pinned" for k in ("import", "export", "state", "import_price")},
                                           "export_price": "unregistered"}
        assert "export_price:source_unregistered" in data["warnings"]
        assert data["source_binding_adopted_at"] == adopted
    finally:
        await proxy.close()
    restarted = ProxyCoordinator(hass, entry)
    await restarted.load()
    try:
        assert entry.data["source_binding_adopted_at"] == adopted  # Adopted once only.
        assert restarted.pinned == pinned
        assert all(restarted.binding(k) in ("pinned", "unregistered") for k in restarted.sources)
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_different_sensor_on_pinned_entity_id_degrades_and_is_not_recorded(hass, monkeypatch):
    from homeassistant.helpers import entity_registry as er
    proxy = await registered_site(hass, monkeypatch)
    await proxy.load()
    try:
        proxy.observations = observations(3)
        proxy.issues.clear()
        assert not (await proxy._async_update_data())["issues"]
        registry = er.async_get(hass)
        registry.async_remove("sensor.proxy_import")
        hass.states.async_remove("sensor.proxy_import")  # Lets the new row take the ID.
        registry.async_get_or_create("sensor", "fictional_inverter", "uid-other-meter",
                                     suggested_object_id="proxy_import")
        assert registry.async_get("sensor.proxy_import").unique_id == "uid-other-meter"
        before = list(proxy.observations["import"])
        hass.states.async_set("sensor.proxy_import", "7", {"unit_of_measurement": "MWh"})
        await hass.async_block_till_done()
        data = await proxy._async_update_data()
        assert "source_binding_changed" in data["issues"] and data["state"] == "degraded"
        assert data["source_identity"]["import"] == "changed"
        assert data["latest_session"]["net_cost_aud"] is None
        assert proxy.observations["import"] == before
        # The pin is never re-adopted; a restart still degrades and skips backfill.
        await proxy.close()
        again = ProxyCoordinator(hass, proxy.entry)
        await again.load()
        assert "import:history_unavailable" not in again.issues
        again.issues.clear()
        assert "source_binding_changed" in (await again._async_update_data())["issues"]
        await again.close()
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_pinned_source_renamed_reads_as_unavailable_not_changed(hass, monkeypatch):
    from homeassistant.helpers import entity_registry as er
    proxy = await registered_site(hass, monkeypatch)
    await proxy.load()
    try:
        proxy.observations = observations(3)
        proxy.issues.clear()
        er.async_get(hass).async_update_entity("sensor.proxy_state", new_entity_id="sensor.renamed_state")
        hass.states.async_remove("sensor.proxy_state")
        hass.states.async_set("sensor.renamed_state", "Charging")
        await hass.async_block_till_done()
        data = await proxy._async_update_data()
        assert data["source_identity"]["state"] == "missing"
        assert "state:unavailable" in data["issues"] and "source_binding_changed" not in data["issues"]
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_unregistered_pin_taken_by_registered_sensor_is_changed(hass, monkeypatch):
    from homeassistant.helpers import entity_registry as er
    proxy = await registered_site(hass, monkeypatch)
    await proxy.load()
    try:
        hass.states.async_remove("sensor.proxy_export_price")
        er.async_get(hass).async_get_or_create("sensor", "other", "uid-x",
                                               suggested_object_id="proxy_export_price")
        hass.states.async_set("sensor.proxy_export_price", "1", {"unit_of_measurement": "$/kWh"})
        assert proxy.binding("export_price") == "changed"
    finally:
        await proxy.close()


@pytest.mark.asyncio
async def test_proxy_config_flow_pins_registry_identity_at_creation(hass, monkeypatch):
    from custom_components.bsv_settlement.config_flow import BSVSettlementConfigFlow
    proxy = await registered_site(hass, monkeypatch)
    flow = BSVSettlementConfigFlow()
    flow.hass = hass
    flow.context = {}
    result = await flow.async_step_proxy(dict(proxy.entry.data) | {"name": "Fictional"})
    assert result["type"] == "create_entry"
    identity = result["data"]["source_identity"]
    assert identity["state"]["unique_id"] == "uid-state" and identity["export_price"]["unregistered"]
    assert "source_binding_adopted_at" not in result["data"]


@pytest.mark.asyncio
async def test_renamed_ocpp_shadow_source_is_incompatible_and_refuses_reload(tmp_path):
    from homeassistant.helpers import entity_registry as er
    from custom_components.bsv_settlement.ocpp_shadow import OCPPShadowCoordinator, source_binding
    from test_ocpp_shadow import config_entry as shadow_entry, setup_sources
    instance = HomeAssistant(str(tmp_path / "shadow"))
    coord = None
    try:
        sources = await setup_sources(instance)
        entry = shadow_entry("bsv_settlement", {
            **{k + "_entity": v for k, v in sources.items()}, "backend": "ocpp_import_shadow",
            "source_binding": source_binding(instance, sources)})
        coord = OCPPShadowCoordinator(instance, entry)
        await coord.load()
        er.async_get(instance).async_update_entity(
            sources["import"], new_entity_id="sensor.renamed_import")
        await instance.async_block_till_done()
        coord.observe()
        summary = coord.summary()
        assert summary["state"] == "incompatible"
        assert "source_binding_changed" in summary["quality_flags"]
        await coord.close()
        coord = None
        with pytest.raises(ValueError, match="enabled OCPP connector metrics"):
            await OCPPShadowCoordinator(instance, entry).load()
    finally:
        if coord:
            await coord.close()
        await instance.async_stop(force=True)
