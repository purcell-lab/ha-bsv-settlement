"""Persisted, versioned tariff provenance: evidence only, never a pricing input."""
import copy
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from custom_components.bsv_settlement.proxy_ledger import build_records, instant
from custom_components.bsv_settlement.tariff_provenance import (
    MAX_VERSIONS, NOT_RECORDED, SCHEMA, TariffProvenanceLedger, capture, digest,
    source_row, summary, verify)

FIXTURES = Path(__file__).parent / "golden" / "fixtures"
SOURCES = {"import_price": "sensor.fictional_import_price",
           "export_price": "sensor.fictional_export_price"}


def load(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def captured(name):
    fixture = load(name)
    record = build_records(fixture["observations"], fixture["state_entity"], fixture["as_of"])[0]
    return fixture, record, capture(fixture["observations"], [record], SOURCES)[record["session_id"]]


def seconds(interval):
    return (instant(interval["end"]) - instant(interval["start"])).total_seconds()


def test_each_priced_interval_records_source_price_unit_bounds_flag_time_and_digest():
    fixture, record, body = captured("g01_brisbane_import_debit")
    assert body["schema"] == SCHEMA and body["session_id"] == record["session_id"]
    imp = body["directions"]["import"]
    assert imp["source_entity"] == "sensor.fictional_import_price"
    assert [i["published_price"] for i in imp["intervals"]] == ["0.3412", "0.4123", "0.2875"]  # window only
    for interval in imp["intervals"]:
        assert interval["status"] == "resolved" and interval["unit"] == "$/kWh"
        assert interval["estimate_flag"] == "final" and interval["price_basis"] == "final"
        source = interval["source"]
        assert interval["retrieved_at"] == source["t"]
        assert source["start"] == interval["start"] and source["end"] == interval["end"]
        assert source["row_digest"] == digest({k: v for k, v in source.items() if k != "row_digest"})
        assert source in [source_row(r) for r in fixture["observations"]["import_price"]]
    # Provenance never changes the account it describes.
    assert build_records(fixture["observations"], fixture["state_entity"], fixture["as_of"])[0] == record


def test_dst_start_interval_is_thirty_real_minutes_and_offsets_are_preserved():
    _, _, body = captured("g05_sydney_dst_start")
    intervals = body["directions"]["import"]["intervals"]
    straddle = next(i for i in intervals if i["start"] == "2026-10-04T01:30:00+10:00")
    assert straddle["end"] == "2026-10-04T03:00:00+11:00" and seconds(straddle) == 1800
    assert all(seconds(i) == 1800 for i in intervals)


def test_dst_end_repeated_local_hour_keeps_distinct_intervals():
    _, record, body = captured("g06_sydney_dst_end_repeated_hour")
    starts = [i["start"] for i in body["directions"]["import"]["intervals"]]
    assert "2026-04-05T02:30:00+11:00" in starts and "2026-04-05T02:30:00+10:00" in starts
    assert len(starts) == len(set(starts))
    prices = {i["start"]: i for i in body["directions"]["import"]["intervals"]}
    assert prices["2026-04-05T02:00:00+11:00"]["zero_price"] is True
    assert prices["2026-04-05T02:30:00+11:00"]["negative_price"] is True
    assert body["directions"]["export"]["negative_price_interval_count"] == 1
    assert record["status"] == "ended_observed"


def test_negative_import_and_export_prices_are_counted_not_hidden():
    _, _, body = captured("g03_brisbane_mixed_negative_prices")
    assert body["directions"]["import"]["negative_price_interval_count"] == 2
    assert body["directions"]["export"]["negative_price_interval_count"] == 2


def test_overlapping_estimates_record_winner_basis_and_competition():
    _, record, body = captured("g04_brisbane_overlapping_estimate_and_final")
    intervals = body["directions"]["import"]["intervals"]
    by_start = {i["start"]: i for i in intervals}
    assert by_start["2026-10-09T14:00:00+10:00"]["price_basis"] == "final"
    assert by_start["2026-10-09T14:00:00+10:00"]["observations_covering_interval"] == 2
    late = by_start["2026-10-09T14:15:00+10:00"]
    assert late["price_basis"] == "estimate" and late["estimate_flag"] == "estimate"
    revised = by_start["2026-10-09T14:30:00+10:00"]
    assert revised["published_price"] == "0.3450"
    assert revised["retrieved_at"] == "2026-10-09T14:25:00+10:00"
    assert body["directions"]["import"]["estimate_interval_count"] == 2
    assert "import:estimated_tariff" in body["quality_flags"]
    assert Decimal(body["estimated_rate_wh"]["import"]) > 0


def test_conflicting_finals_are_recorded_as_ambiguous_without_choosing():
    fixture = load("g08_half_cent_debit_rounding")
    rows = fixture["observations"]["import_price"]
    rows.append(rows[0] | {"value": "0.2600", "t": rows[0]["t"].replace(":59:", ":58:"),
                           "start": rows[0]["start"].replace("08:00", "08:05")})
    record = build_records(fixture["observations"], fixture["state_entity"], fixture["as_of"])[0]
    body = capture(fixture["observations"], [record], SOURCES)[record["session_id"]]
    ambiguous = [i for i in body["directions"]["import"]["intervals"] if i["status"] == "ambiguous"]
    assert ambiguous and ambiguous[0]["published_price"] is None
    assert ambiguous[0]["candidate_prices"] == ["0.2500", "0.2600"]
    assert record["net_cost_aud_unrounded"] is None  # still unpriced; nothing waived


def test_open_sessions_are_not_captured():
    fixture = load("g01_brisbane_import_debit")
    fixture["observations"]["state"] = fixture["observations"]["state"][:-1]
    record = build_records(fixture["observations"], fixture["state_entity"], fixture["as_of"])[0]
    assert capture(fixture["observations"], [record], SOURCES) == {}


def test_versions_are_append_only_and_linked_to_the_account_digest():
    _, record, body = captured("g04_brisbane_overlapping_estimate_and_final")
    ledger = TariffProvenanceLedger()
    assert ledger.observe(record["session_id"], body, "a" * 64, "2026-10-09T05:00:00+00:00")
    assert not ledger.observe(record["session_id"], copy.deepcopy(body), "a" * 64, "later")
    first = copy.deepcopy(ledger.versions(record["session_id"])[0])
    revised = copy.deepcopy(body)
    revised["directions"]["import"]["intervals"][2]["price_basis"] = "final"
    assert ledger.observe(record["session_id"], revised, "b" * 64, "2026-10-09T06:00:00+00:00")
    assert ledger.versions(record["session_id"])[0] == first
    assert [v["version"] for v in ledger.versions(record["session_id"])] == [1, 2]
    assert ledger.lookup(record["session_id"], "a" * 64)["provenance"] == body
    assert ledger.lookup(record["session_id"], "c" * 64) is None
    assert ledger.lookup(record["session_id"], None) is None
    assert verify(ledger.lookup(record["session_id"], "b" * 64))
    tampered = ledger.lookup(record["session_id"], "b" * 64)
    tampered["provenance"]["directions"]["import"]["intervals"][0]["published_price"] = "0.01"
    assert not verify(tampered)


def test_version_limit_is_flagged_and_never_overwrites():
    _, record, body = captured("g01_brisbane_import_debit")
    ledger = TariffProvenanceLedger()
    for index in range(MAX_VERSIONS):
        assert ledger.observe(record["session_id"], body, f"{index:064x}", str(index))
    before = copy.deepcopy(ledger.versions(record["session_id"]))
    assert ledger.observe(record["session_id"], body, "f" * 64, "late")
    assert not ledger.observe(record["session_id"], body, "e" * 64, "later")
    assert ledger.versions(record["session_id"]) == before
    assert ledger.data["sessions"][record["session_id"]]["version_limit_reached"] is True


def test_legacy_and_invalid_stores_are_never_invented_or_rewritten():
    assert TariffProvenanceLedger(None).data["sessions"] == {}
    invalid = {"schema": "something-else", "sessions": {"x": 1}}
    ledger = TariffProvenanceLedger(invalid)
    assert ledger.invalid_store and ledger.stored() == invalid
    assert not ledger.observe("x", {"a": 1}, "d" * 64, "now")
    assert ledger.stored() == invalid
    assert summary(None) == NOT_RECORDED and summary(None)["status"] == "not_recorded"


@pytest.mark.asyncio
async def test_review_freezes_provenance_outside_terms_and_legacy_reviews_show_not_recorded(
        tmp_path, monkeypatch):
    pytest.importorskip("homeassistant")
    from unittest.mock import AsyncMock
    from test_mainnet import setup_wallet
    from custom_components.bsv_settlement.session_review import account_snapshot
    from custom_components.bsv_settlement.session_review import digest as account_digest
    monkeypatch.setattr("custom_components.bsv_settlement.mainnet.async_get_clientsession",
                        lambda hass: None)
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        fixture, record, body = captured("g04_brisbane_overlapping_estimate_and_final")
        ledger = TariffProvenanceLedger()
        ledger.observe(record["session_id"], body, account_digest(account_snapshot(record)), "t1")
        proxy = SimpleNamespace(
            mode="sensor_proxy", archive=[], async_request_refresh=AsyncMock(),
            data={"latest_session": record, "previous_session": None, "issues": []},
            tariff_provenance=lambda sid, d, detail=False: (
                ledger.lookup(sid, d) if detail else summary(ledger.lookup(sid, d))))
        hass.data.setdefault("bsv_settlement", {})["proxy-entry"] = proxy
        hass.states.async_set("sensor.demo_rate", "2537.5", {"unit_of_measurement": "sat/AUD"})
        review = await api.reviews.execute("prepare_session_review", {
            "proxy_config_entry_id": "proxy-entry", "session_id": record["session_id"],
            "conversion_rate_entity": "sensor.demo_rate"}, "admin")
        compact = review["tariff_provenance"]
        assert compact["status"] == "recorded" and compact["estimated"] is True
        assert compact["directions"]["import"]["estimate_interval_count"] == 2
        assert "intervals" not in json.dumps(compact)
        stored = api.saved["session_reviews"][review["review_id"]]
        assert stored["tariff_provenance"] is None  # Earlier releases read only this null.
        assert "tariff_provenance" not in review["frozen_terms"]
        assert digest(review["frozen_terms"]) == review["terms_hash"]
        # Later evidence changes cannot alter what was frozen into the review.
        ledger.data["sessions"].clear()
        full = await api.reviews.execute("session_review_status", {
            "review_id": review["review_id"], "include_tariff_provenance": True}, "admin")
        assert full["tariff_provenance"]["provenance"] == body
        assert full["tariff_provenance"]["digest_verified"] is True
        assert api.identity["secret_hex"] not in json.dumps(full)
        # A review frozen before the archive kept the full version inline; still shown.
        stored["tariff_provenance"] = copy.deepcopy(full["tariff_provenance"])
        for key in ("status", "digest_verified", "reference"):
            stored["tariff_provenance"].pop(key)
        inline = await api.reviews.execute("session_review_status", {
            "review_id": review["review_id"], "include_tariff_provenance": True}, "admin")
        assert inline["tariff_provenance"]["provenance"] == body
        assert api.reviews.latest()["tariff_provenance"]["digest_verified"] is True
        # A review stored before provenance existed shows "not recorded".
        stored.pop("tariff_provenance")
        stored.pop("tariff_provenance_ref")
        legacy = await api.reviews.execute("session_review_status", {
            "review_id": review["review_id"], "include_tariff_provenance": True}, "admin")
        assert legacy["tariff_provenance"] == NOT_RECORDED
        assert api.reviews.latest()["tariff_provenance"]["status"] == "not_recorded"
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_real_ha_recorder_persists_provenance_privately_across_restart(tmp_path, monkeypatch):
    pytest.importorskip("homeassistant")
    from homeassistant.core import HomeAssistant, State
    from homeassistant.util import dt as dt_util
    from custom_components.bsv_settlement.proxy import ProxyCoordinator
    from custom_components.bsv_settlement.session_review import account_snapshot
    from custom_components.bsv_settlement.session_review import digest as account_digest
    from test_proxy import config_entry, fixture as proxy_fixture
    hass = HomeAssistant(str(tmp_path / "ha"))
    dt_util.set_default_time_zone(dt_util.get_time_zone("Australia/Brisbane"))
    entry = config_entry()
    raw = {}
    for key, rows in proxy_fixture().items():
        entity = entry.data[key + "_entity"]
        raw[entity] = [State(entity, r["value"], {
            **({"unit_of_measurement": r["unit"]} if "unit" in r else {}),
            **{field: r[k] for k, field in (("start", "start_time"), ("end", "end_time"),
                                           ("estimate", "estimate")) if k in r}},
            last_changed=instant(r["t"]), last_updated=instant(r["t"])) for r in rows]
        hass.states.async_set(entity, raw[entity][-1].state, dict(raw[entity][-1].attributes))

    async def get_history(*args, **kwargs):
        return raw
    monkeypatch.setattr("homeassistant.components.recorder.get_instance",
                        lambda hass: SimpleNamespace(async_add_executor_job=get_history))
    coords = []
    try:
        c = ProxyCoordinator(hass, entry)
        coords.append(c)
        await c.load()
        c.async_set_updated_data(await c._async_update_data())
        record = c.data["latest_session"]
        key = account_digest(account_snapshot(record))
        compact = c.tariff_provenance(record["session_id"], key)
        assert compact["status"] == "recorded" and compact["account_digest"] == key
        full = c.tariff_provenance(record["session_id"], key, detail=True)
        assert verify(full)
        assert full["provenance"]["directions"]["export"]["intervals"][0]["negative_price"] is True
        # Provenance stays in private storage, not in sensor state attributes.
        assert "tariff_provenance" not in json.dumps(c.data, default=str)
        assert c.tariff_provenance(record["session_id"], "0" * 64) == NOT_RECORDED
        await c.close()
        saved = await c.store.async_load()
        assert saved["tariff_provenance"]["sessions"][record["session_id"]]["versions"] == [full]
        restored = ProxyCoordinator(hass, entry)
        coords.append(restored)
        await restored.load()
        assert restored.tariff_provenance(record["session_id"], key, detail=True) == full
        # A store written before provenance existed loads as "not recorded".
        legacy = {k: v for k, v in saved.items() if k != "tariff_provenance"}
        await restored.store.async_save(legacy)
        old = ProxyCoordinator(hass, entry)
        coords.append(old)
        await old.load()
        assert old.tariff_provenance(record["session_id"], key) == NOT_RECORDED
    finally:
        for item in coords:
            await item.close()
        await hass.async_stop(force=True)


def test_adr_current_policy_estimates_settle_disclosed_but_gaps_still_block():
    """Pins docs/adr/estimated-tariff-finalisation.md option A (status quo, #68).

    If the owner chooses option B, this test must change together with the
    settlement code; it must never be edited to pass silently.
    """
    pytest.importorskip("homeassistant")
    from custom_components.bsv_settlement.api import WalletError
    from custom_components.bsv_settlement.session_review import account_snapshot
    fixture, record, body = captured("g04_brisbane_overlapping_estimate_and_final")
    account = account_snapshot(record)
    assert "import:estimated_tariff" in account["quality_flags"]
    assert body["directions"]["import"]["estimate_interval_count"] == 2
    rows = fixture["observations"]["import_price"]
    fixture["observations"]["import_price"] = [r for r in rows if r["value"] not in ("0.3000",)]
    gap = build_records(fixture["observations"], fixture["state_entity"], fixture["as_of"])[0]
    assert Decimal(gap["unpriced_import_wh"]) > 0
    with pytest.raises(WalletError, match="unpriced|quality"):
        account_snapshot(gap)
