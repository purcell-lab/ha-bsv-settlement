"""Settlement records freeze a fixed-size provenance reference; the full version is archived separately.

Evidence only: amounts, signed terms, recipients and payment flow never change.
"""
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from bsv import Transaction
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.provenance_archive import (
    MAX_UNREFERENCED, NOT_RECORDED_REF, STORE_VERSION, ProvenanceArchive, reference)
from custom_components.bsv_settlement.records import STORES
from custom_components.bsv_settlement.session_review import account_snapshot, digest
from custom_components.bsv_settlement.tariff_provenance import (
    MAX_SESSIONS, NOT_RECORDED, TariffProvenanceLedger, summary, verify)
from test_budget import no_network  # noqa: F401 - autouse fixture
from test_tariff_provenance import captured

pytestmark = pytest.mark.asyncio
REF_KEYS = {"status", "schema", "version", "captured_at", "account_digest", "provenance_digest",
            "version_digest", "directions", "estimated"}


def attach(proxy, records=()):
    """Give a fictional recorder the real provenance ledger and lookup contract."""
    ledger = TariffProvenanceLedger()
    proxy.tariff_provenance = lambda sid, d, detail=False: (
        ledger.lookup(sid, d) if detail else summary(ledger.lookup(sid, d)))
    for record in records:
        observe(ledger, record)
    return ledger


def observe(ledger, record, captured_at="2026-10-01T00:00:00+00:00", fixture="g01_brisbane_import_debit"):
    body = copy.deepcopy(captured(fixture)[2]) | {"session_id": record["session_id"]}
    key = digest(account_snapshot(record))
    assert ledger.observe(record["session_id"], body, key, captured_at)
    return key


def expire(ledger, session_id):
    """Push more than MAX_SESSIONS newer sessions so the recorder drops this one."""
    body = captured("g02_brisbane_v2g_export_credit")[2]
    for n in range(MAX_SESSIONS + 1):
        ledger.observe(f"later-{n}", body, f"{n:064x}", f"2026-11-{1 + n // 24:02d}T{n % 24:02d}:00:00+00:00")
    assert not ledger.versions(session_id)


async def budget_status(api, row, detail=False):
    return await api.budgets.execute("session_budget_status", {
        "budget_id": row["terms"]["budget_id"], "include_tariff_provenance": detail}, "admin")


def ref_matches(api, item, version):
    """Ledger holds only the reference; the archive holds exactly the captured version."""
    ref = item["tariff_provenance_ref"]
    assert set(ref) == REF_KEYS and ref == reference(version)
    assert not item.get("tariff_provenance") and "intervals" not in json.dumps(ref)
    assert api.provenance_archive.get(ref["version_digest"]) == version
    return ref


async def test_collection_reference_outside_signed_quote_survives_expiry_restart_and_legacy(tmp_path):
    from test_collection import ready, claim_data, payment
    from custom_components.bsv_settlement.collection import transaction_shape
    hass, api, proxy, row, driver = await ready(tmp_path)
    record = proxy.data["latest_session"]
    ledger = attach(proxy)
    key = observe(ledger, record)
    result = await api.collections.status(row)
    item = api.collections.get(row)
    assert result["state"] == "ready" and "tariff_provenance" not in json.dumps(result)  # driver view
    assert item["source_hash"] == key
    version = ledger.lookup(record["session_id"], key)
    ref = ref_matches(api, item, version)
    assert "tariff_provenance" not in item["quote"]["payload"]
    quote = json.loads(item["quote"]["payload"])
    assert quote["amount_sats"] == 189 and quote["recipient_address"] == api.identity["address"]
    # Recorder expiry cannot alter the frozen evidence.
    expire(ledger, record["session_id"])
    compact = (await budget_status(api, row))["tariff_provenance"]["collection"]
    assert compact == ref
    full = (await budget_status(api, row, True))["tariff_provenance"]["collection"]
    assert full["provenance"] == version["provenance"] and full["digest_verified"] is True
    assert full["reference"] == ref
    archived = Path(api.provenance_archive.store.path).read_text()
    assert api.identity["secret_hex"] not in archived and api.identity["address"] not in archived
    assert '"intervals"' not in Path(api.store.ledger.path).read_text()
    # Payment proceeds exactly as before.
    args = claim_data(item, row, driver)
    await api.collections.claim(row, args)
    tx = payment(api, driver)
    await api.collections.authorise(row, args | {"draft": transaction_shape(tx)})
    assert (await api.collections.report(row, args | {"raw_tx": tx.hex()}))["state"] == "provider_confirmed"
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    saved_row = restored.saved["session_budgets"][row["terms"]["budget_id"]]
    assert restored.collections.get(saved_row)["tariff_provenance_ref"] == ref
    again = (await budget_status(restored, saved_row, True))["tariff_provenance"]["collection"]
    assert again["provenance"] == version["provenance"] and again["digest_verified"] is True
    # A record written before this field existed shows "not recorded".
    restored.collections.get(saved_row).pop("tariff_provenance_ref")
    legacy = await budget_status(restored, saved_row, True)
    assert legacy["tariff_provenance"]["collection"] == NOT_RECORDED


async def test_ledger_reference_is_small_and_fixed_size_whatever_the_interval_count(tmp_path):
    _, record, body = captured("g01_brisbane_import_debit")
    sizes = []
    for count in (1, 2000):
        big = copy.deepcopy(body)
        for direction in big["directions"].values():
            direction["intervals"] = [copy.deepcopy(direction["intervals"][0])] * count
            direction["interval_count"] = direction["estimate_interval_count"] = 9999 if count > 1 else 1
        ledger = TariffProvenanceLedger()
        ledger.observe("s", big, "a" * 64, "2026-10-01T00:00:00.000001+00:00")
        version = ledger.lookup("s", "a" * 64)
        ref = reference(version)
        assert set(ref) == REF_KEYS
        sizes.append((len(json.dumps(version)), len(json.dumps(ref))))
    (small_version, small_ref), (big_version, big_ref) = sizes
    assert big_version > 1_000_000 > small_version
    assert small_ref <= big_ref <= 640 and big_ref - small_ref <= 16  # only integer widths differ


async def test_missing_provenance_records_marker_and_quote_is_byte_identical(tmp_path):
    from test_collection import ready
    quotes = []
    for name, with_ledger in (("with", True), ("without", False)):
        _, api, proxy, row, _ = await ready(tmp_path / name)
        proxy.data["latest_session"]["ended_at"] = "2026-10-02T10:00:00+10:00"  # pinned fixture
        if with_ledger:
            observe(attach(proxy), proxy.data["latest_session"])
        await api.collections.status(row)
        item = api.collections.get(row)
        assert item["state"] == "ready"
        quotes.append((json.loads(item["quote"]["payload"]), item, api))
    (signed, frozen, _), (plain, missing, bare) = quotes
    assert missing["tariff_provenance_ref"] == NOT_RECORDED_REF
    assert bare.provenance_archive.data["versions"] == {}
    assert frozen["tariff_provenance_ref"]["status"] == "recorded"
    assert signed.pop("created_at") and plain.pop("created_at")
    for q in (signed, plain):
        for k in ("budget_id", "invitation_hash", "driver_identity", "expires_at",
                  "operator_identity", "recipient_address"):
            q.pop(k)
    assert signed == plain and signed["amount_sats"] == 189
    assert frozen["source_hash"] == missing["source_hash"]
    assert set(frozen) == set(missing)


async def test_archive_is_written_before_the_referencing_ledger_save(tmp_path):
    from test_collection import ready
    _, api, proxy, row, _ = await ready(tmp_path)
    observe(attach(proxy), proxy.data["latest_session"])
    seen, original = [], api.store.async_save

    async def save(data):
        item = data.get("driver_collections", {}).get(row["terms"]["budget_id"])
        if item:
            seen.append(api.provenance_archive.get(item["tariff_provenance_ref"]["version_digest"]))
        return await original(data)
    api.store.async_save = save
    await api.collections.status(row)
    assert seen and seen[0] is not None and verify(seen[0])


async def test_archive_failure_shows_archive_missing_and_never_blocks_payment(tmp_path):
    from test_auto_credit import ready
    _, api, proxy, row, _, _ = await ready(tmp_path)
    attach(proxy, [proxy.data["latest_session"]])
    api.provenance_archive.store.async_save = AsyncMock(side_effect=OSError("fictional disk"))
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["state"] == "submitted" and len(api.chain.posts) == 1
    assert item["tariff_provenance_ref"]["status"] == "recorded"
    assert api.provenance_archive.data["versions"] == {}
    full = (await budget_status(api, row, True))["tariff_provenance"]["automatic_credit"]
    assert full["status"] == "archive_missing" and full["version_digest"]


@pytest.mark.parametrize("content", ['{"version": 1, "data": {', json.dumps(
    {"version": 1, "minor_version": 9, "key": "x", "data": {"schema": "x", "versions": {}}})])
async def test_refused_archive_file_is_never_rewritten_and_payment_proceeds(tmp_path, content):
    from test_auto_credit import ready
    hass, api, proxy, row, _, _ = await ready(tmp_path)
    path = Path(api.provenance_archive.store.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    assert restored.provenance_archive.state == "refused"
    attach(proxy, [proxy.data["latest_session"]])
    await restored.auto_credits.tick()
    saved_row = restored.saved["session_budgets"][row["terms"]["budget_id"]]
    assert restored.auto_credits.get(saved_row)["state"] == "submitted"
    assert path.read_text() == content
    full = (await budget_status(restored, saved_row, True))["tariff_provenance"]["automatic_credit"]
    assert full["status"] == "archive_missing"


async def test_archive_dedupes_and_never_evicts_referenced_versions(tmp_path):
    from test_mainnet import setup_wallet
    from test_budget import OPEN_HASS
    hass, entry, api = await setup_wallet(tmp_path)
    OPEN_HASS.append(hass)
    _, record, body = captured("g01_brisbane_import_debit")
    ledger = TariffProvenanceLedger()
    ledger.observe("kept", body, "a" * 64, "2026-10-01T00:00:00+00:00")
    kept = ledger.lookup("kept", "a" * 64)
    ref = reference(kept)
    api.saved["driver_collections"] = {"b": {"tariff_provenance_ref": ref}}
    api.saved["automatic_credits"] = {"c": {"tariff_provenance_ref": copy.deepcopy(ref)}}
    archive = api.provenance_archive
    assert await archive.put(api.saved, kept) and await archive.put(api.saved, kept)
    assert list(archive.data["versions"]) == [ref["version_digest"]]
    for n in range(MAX_UNREFERENCED + 5):
        ledger.observe(f"loose-{n}", body, f"{n:064x}", f"2026-10-02T00:00:{n:02d}+00:00")
        assert await archive.put(api.saved, ledger.lookup(f"loose-{n}", f"{n:064x}"))
    assert ref["version_digest"] in archive.data["versions"]
    assert len(archive.data["versions"]) == MAX_UNREFERENCED + 2  # + referenced + newest (save pending)
    reloaded = ProvenanceArchive(hass, entry.entry_id)
    await reloaded.load()
    assert reloaded.state == "ok" and reloaded.get(ref["version_digest"]) == kept
    assert STORES["provenance_archive"].version == STORE_VERSION == 1


async def test_automatic_credit_reference_and_signed_amounts_unchanged(tmp_path):
    from test_auto_credit import ready
    for name, with_ledger in (("with", True), ("without", False)):
        hass, api, proxy, row, _, _ = await ready(tmp_path / name)
        record = proxy.data["latest_session"]
        ledger = attach(proxy) if with_ledger else None
        key = observe(ledger, record) if ledger else digest(account_snapshot(record))
        await api.auto_credits.tick()
        item = api.auto_credits.get(row)
        assert item["state"] == "submitted" and item["source_hash"] == key
        assert item["amount_sats"] == 189 and item["fee_sats"] == 10
        tx = Transaction.from_hex(api.chain.posts[0])
        assert [o.satoshis for o in tx.outputs] == [189, 49801]
        assert "tariff_provenance" not in json.dumps(api.auto_credits.public(item))  # driver view
        if not with_ledger:
            assert item["tariff_provenance_ref"] == NOT_RECORDED_REF
            assert (await budget_status(api, row))["tariff_provenance"]["automatic_credit"] == NOT_RECORDED
            continue
        ref = ref_matches(api, item, ledger.lookup(record["session_id"], key))
        expire(ledger, record["session_id"])
        restored = MainnetWalletAPI(hass, api.entry)
        await restored.load()
        saved_row = restored.saved["session_budgets"][row["terms"]["budget_id"]]
        full = (await budget_status(restored, saved_row, True))["tariff_provenance"]["automatic_credit"]
        assert full["status"] == "recorded" and full["account_digest"] == key and full["digest_verified"]
        assert full["reference"] == ref


async def test_collection_recovery_keeps_frozen_reference(tmp_path):
    from test_collection import ready, claim_data
    from test_collection_recovery import recovery_data
    from custom_components.bsv_settlement.collection_recovery import execute
    _, api, proxy, row, driver = await ready(tmp_path)
    observe(attach(proxy), proxy.data["latest_session"])
    item = await api.collections.status(row)
    await api.collections.claim(row, claim_data(item, row, driver))
    frozen = copy.deepcopy(api.collections.get(row)["tariff_provenance_ref"])
    assert frozen["status"] == "recorded"
    await execute(api.collections, "recover_driver_collection", await recovery_data(api, row), "admin")
    assert api.collections.get(row)["state"] == "recovery_ready"
    assert api.collections.get(row)["tariff_provenance_ref"] == frozen
    assert not api.chain.posts


async def test_ongoing_credit_and_original_recipient_recovery_freeze_reference(tmp_path):
    from test_ongoing_credit import setup
    hass, api, proxy, row, _, _ = await setup(tmp_path / "ongoing")
    ledger = attach(proxy, [proxy.data["latest_session"]])
    await api.ongoing_credits.tick()
    route = next(iter(api.ongoing_credits.routes.values()))
    item = api.ongoing_credits.get(api.ongoing_credits.wrapper(route))
    assert item["amount_sats"] == 189 and len(api.chain.posts) == 1
    ref_matches(api, item, ledger.lookup(route["session_id"], item["source_hash"]))
    listed = (await budget_status(api, row))["tariff_provenance"]["ongoing_credits"]
    assert [r["credit_id"] for r in listed] == [route["route_id"]]
    assert listed[0]["tariff_provenance"]["status"] == "recorded"
    assert "tariff_provenance" not in json.dumps(api.ongoing_credits.driver_rows(row))
    # Recovery review creates the record itself; the reference stays outside review_hash terms.
    hass, api, proxy, row, _, _ = await setup(tmp_path / "recovery", "-1.19")
    ledger = attach(proxy, [proxy.data["latest_session"]])
    await api.ongoing_credits.configure({"enabled": False}, "admin")
    route = next(iter(api.ongoing_credits.routes.values()))
    review = await api.credit_recovery.prepare({"credit_id": route["route_id"]}, "admin")
    item = api.saved["automatic_credits"][route["route_id"]]
    assert review["amount_sats"] == 119 and review["total_sats"] == 129
    ref_matches(api, item, ledger.lookup(route["session_id"], item["source_hash"]))
    terms = route["manual_recovery"]["terms"]
    assert "tariff_provenance" not in json.dumps(terms)
    assert digest(terms) == route["manual_recovery"]["review_hash"]


async def test_weekly_child_collection_freezes_reference_and_parent_lists_it(tmp_path, monkeypatch):
    from test_weekly_mandate import setup, session
    from custom_components.bsv_settlement.weekly import ticket
    _, api, proxy, row, _, _, clock = await setup(tmp_path, monkeypatch)
    record = session(proxy, clock)
    ledger = attach(proxy, [record])
    child = await ticket(api, row, "new-1")
    quoted = await api.collections.status(child)
    assert json.loads(quoted["quote"]["payload"])["amount_sats"] == 400
    item = api.collections.get(child)
    ref_matches(api, item, ledger.lookup("new-1", item["source_hash"]))
    listed = (await budget_status(api, row))["tariff_provenance"]["multi_session_collections"]
    assert [(r["session_id"], r["tariff_provenance"]["status"]) for r in listed] == [("new-1", "recorded")]


async def test_lookup_fault_never_blocks_payment(tmp_path):
    from test_auto_credit import ready
    _, api, proxy, row, _, _ = await ready(tmp_path)

    def broken(*args, **kwargs):
        raise RuntimeError("fictional recorder fault")
    proxy.tariff_provenance = broken
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["state"] == "submitted" and item["tariff_provenance_ref"] == NOT_RECORDED_REF
    assert len(api.chain.posts) == 1


async def test_manual_review_keeps_null_inline_field_for_downgrade(tmp_path):
    from test_budget import OPEN_HASS
    from test_mainnet import setup_wallet
    from test_session_review import source, session, prepare_data
    hass, _, api = await setup_wallet(tmp_path)
    OPEN_HASS.append(hass)
    proxy = source(hass, session())
    review = await api.reviews.execute("prepare_session_review", prepare_data(), "admin")
    stored = api.saved["session_reviews"][review["review_id"]]
    assert stored["tariff_provenance"] is None and stored["tariff_provenance_ref"] == NOT_RECORDED_REF
    assert review["tariff_provenance"] == NOT_RECORDED
    ledger = attach(proxy, [proxy.data["latest_session"]])
    await api.reviews.execute("cancel_session_review", {"review_id": review["review_id"]}, "admin")
    again = await api.reviews.execute("prepare_session_review", prepare_data(), "admin")
    stored = api.saved["session_reviews"][again["review_id"]]
    assert stored["tariff_provenance"] is None
    ref_matches(api, stored, ledger.lookup("session-1", stored["source_hash"]))
