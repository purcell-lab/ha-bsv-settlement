"""Record registry, fail-closed versions and the hash-chained audit log.

Fictional identities and providers only; no network, wallet or chain access.
"""
import ast
import copy
import json
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.bsv_settlement import audit as audit_module
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.audit import (
    GENESIS, MARKER as AUDIT_MARKER, digest, project, ref, transitions, verify)
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.embedded import EmbeddedWalletAPI
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.records import (
    LEDGER_NAMESPACES, STORES, RecordVersionError, VersionedStore, check_keys,
    inspect_directory, spec_for_key, storage_key)
from test_auto_credit import ready
from test_budget import no_network  # noqa: F401  (autouse: no client session, closes hass)
from test_embedded import make_entry, make_hass

pytestmark = pytest.mark.asyncio
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "custom_components" / "bsv_settlement"


async def reload(api):
    restored = MainnetWalletAPI(api.hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    return restored


async def paid(tmp_path):
    hass, api, proxy, row, driver, data = await ready(tmp_path)
    await api.auto_credits.tick()
    await api.auto_credits.tick()
    assert api.auto_credits.get(row)["state"] == "provider_confirmed"
    assert len(api.chain.posts) == 1
    return api, row


def audit_file(api):
    return Path(api.store.audit.store.path)


# --- Registry coverage --------------------------------------------------------

def test_every_store_construction_in_the_integration_uses_the_registry():
    bare, names = [], set()
    for path in SOURCE.glob("*.py"):
        if path.name == "records.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
                if func == "Store":
                    bare.append(path.name)
                if func == "VersionedStore" and node.args[1:] and isinstance(node.args[1], ast.Constant):
                    names.add(node.args[1].value)
            if isinstance(node, ast.ClassDef):
                bases = {getattr(b, "id", None) for b in node.bases}
                assert "Store" not in bases, path.name
    assert not bare, bare
    # Subclasses register via super().__init__(hass, "<name>", entry_id).
    for path in SOURCE.glob("*.py"):
        names.update(re.findall(r'super\(\).__init__\(hass, "([a-z_]+)"', path.read_text()))
    assert names <= set(STORES) and names | {"wallet_audit"} == set(STORES)


def test_every_wallet_ledger_namespace_referenced_in_source_is_registered():
    pattern = re.compile(
        r'(?:api\.saved|self\.saved)(?:\[|\.get\(|\.setdefault\(|\.pop\()"([a-z_]+)"')
    found = set()
    for name in ("embedded.py", "mainnet.py"):
        found |= set(pattern.findall((SOURCE / name).read_text()))
    for path in SOURCE.glob("*.py"):
        if path.name not in ("coordinator.py", "proxy.py", "embedded.py", "mainnet.py"):
            found |= set(re.findall(r'api\.saved(?:\[|\.get\(|\.setdefault\(|\.pop\()"([a-z_]+)"',
                                    path.read_text()))
    from custom_components.bsv_settlement.monthly_ownership import KEY
    found.add(KEY)
    assert found <= set(LEDGER_NAMESPACES), sorted(found - set(LEDGER_NAMESPACES))
    assert len(found) >= 25


def test_registry_versions_match_owner_constants_and_keys_resolve():
    from custom_components.bsv_settlement import ledger_checkpoint, ocpp_shadow
    from custom_components.bsv_settlement.recorder_reconciliation import SCHEMA
    assert STORES["ocpp_shadow"].version == ocpp_shadow.STORE_VERSION == 3
    assert STORES["recorder_reconciliation"].version == SCHEMA == 1
    from custom_components.bsv_settlement import provenance_archive
    assert STORES["provenance_archive"].version == provenance_archive.STORE_VERSION == 1
    assert ledger_checkpoint.VERSION == 1 and audit_module.VERSION == 1
    keys = set()
    for name, spec in STORES.items():
        key = storage_key(name, None if "{entry_id}" not in spec.key else "01FICTIONALENTRY")
        assert spec_for_key(key) == (name, spec)
        keys.add(key)
        # Converters exist only where declared; everything else refuses.
        assert all(old < spec.version for old in spec.migrations)
    assert len(keys) == len(STORES)
    assert spec_for_key("bsv_settlement.unknown.01FICTIONALENTRY") is None
    assert STORES["operator_key"].secret and STORES["operator_key"].private


async def test_live_shaped_ledger_and_every_saved_key_are_registered_and_load_unchanged(tmp_path):
    api, _ = await paid(tmp_path)
    paths = [Path(api.store.ledger.path), Path(api.store.witness.path), audit_file(api),
             Path(api.key_store.path)]
    before = [p.read_bytes() for p in paths]
    check_keys("wallet_ledger", api.saved)
    for p in paths:
        raw = json.loads(p.read_text())
        assert (raw["version"], raw["minor_version"]) == (1, 1)
    restored = await reload(api)
    assert restored.saved == api.saved
    assert [p.read_bytes() for p in paths] == before  # Loading rewrites nothing.
    rows = inspect_directory(paths[0].parent)
    assert rows and all(r["result"] == "ok" for r in rows), rows


# --- Fail-closed versions ----------------------------------------------------

def envelope(path, **changes):
    raw = json.loads(path.read_text())
    raw.update(changes)
    path.write_text(json.dumps(raw))
    return path.read_bytes()


@pytest.mark.parametrize("change", [
    {"version": 2}, {"minor_version": 2}, {"version": "1"}, {"version": True}, {"data": None, "x": 1}])
@pytest.mark.parametrize("target", ["ledger", "witness"])
async def test_unknown_or_future_wallet_versions_are_refused_and_left_in_place(tmp_path, change, target):
    api, _ = await paid(tmp_path)
    path = Path(getattr(api.store, target).path)
    if "x" in change:
        path.write_text(json.dumps({"version": 1, "key": path.name}))  # No data member.
        written = path.read_bytes()
    else:
        written = envelope(path, **change)
    with pytest.raises(WalletError, match="restore"):
        await reload(api)
    assert path.read_bytes() == written
    assert len(api.chain.posts) == 1


async def test_corrupt_ledger_is_refused_in_place_not_renamed_or_reset(tmp_path):
    api, _ = await paid(tmp_path)
    path = Path(api.store.ledger.path)
    path.write_text('{"version": 1, "data": {')
    for _ in range(2):  # HA would rename it and the next start would be empty.
        with pytest.raises(WalletError, match="restore"):
            await reload(api)
        assert path.read_text() == '{"version": 1, "data": {'
    assert not list(path.parent.glob("*.corrupt*"))


async def test_unknown_ledger_namespace_from_a_newer_release_is_refused(tmp_path):
    api, _ = await paid(tmp_path)
    await api.store.async_save({**api.saved, "future_namespace": {}})
    with pytest.raises(WalletError, match="restore"):
        await reload(api)
    assert "future_namespace" in json.loads(Path(api.store.ledger.path).read_text())["data"]


@pytest.mark.parametrize("name,entry_id", [
    ("coordinator", "e1"), ("proxy", "e1"), ("grouped_wallet_test", None),
    ("recorder_reconciliation", "e1"), ("operator_key", "e1"), ("wallet_audit", "e1")])
async def test_each_registered_store_refuses_newer_minor_corrupt_and_unknown(tmp_path, name, entry_id):
    hass = HomeAssistant(str(tmp_path))
    try:
        store = VersionedStore(hass, name, entry_id)
        path = Path(store.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        spec = STORES[name]
        for bad in ({"version": spec.version, "minor_version": spec.minor_version + 1},
                    {"version": spec.version + 1, "minor_version": 1},
                    {"version": spec.version - 1, "minor_version": 1}):
            path.write_text(json.dumps({**bad, "key": store.key, "data": {}}))
            with pytest.raises((RecordVersionError, ValueError)):
                await VersionedStore(hass, name, entry_id).async_load()
            assert json.loads(path.read_text())["version"] == bad["version"]
        path.write_text("not json")
        with pytest.raises(RecordVersionError, match="corrupt"):
            await VersionedStore(hass, name, entry_id).async_load()
        assert path.read_text() == "not json"
        # Legacy envelopes without minor_version are HA's normal 1.x format.
        path.write_text(json.dumps({"version": spec.version, "key": store.key, "data": {"k": 1}}))
        assert await VersionedStore(hass, name, entry_id).async_load() == {"k": 1}
        if spec.keys is not None:
            with pytest.raises(RecordVersionError, match="unknown"):
                check_keys(name, {"unknown_future_key": 1})
    finally:
        await hass.async_stop(force=True)


async def test_coordinator_refuses_unknown_keys_instead_of_resetting(tmp_path):
    entry = make_entry()
    hass = await make_hass(tmp_path, entry)
    try:
        coordinator = SettlementCoordinator(hass, entry, EmbeddedWalletAPI(hass, entry))
        await coordinator.store.async_save({"sessions": {"s": {}}, "latest": "s", "extra": 1})
        fresh = SettlementCoordinator(hass, entry, EmbeddedWalletAPI(hass, entry))
        with pytest.raises(RecordVersionError):
            await fresh.load()
        await coordinator.store.async_save({"sessions": {"s": {}}, "latest": "s"})
        await fresh.load()
        assert fresh.saved == {"sessions": {"s": {}}, "latest": "s"}
    finally:
        await hass.async_stop(force=True)


async def test_ocpp_v2_store_migration_round_trip_is_stable(tmp_path):
    from test_ocpp_export_shadow import import_v2_data
    from custom_components.bsv_settlement.ocpp_shadow import ShadowStore
    hass = HomeAssistant(str(tmp_path))
    try:
        store = ShadowStore(hass, "e1", {"connector": "synthetic"})
        path = Path(store.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        old = import_v2_data()
        path.write_text(json.dumps({"version": 2, "minor_version": 1, "key": store.key, "data": old}))
        migrated = await store.async_load()
        await hass.async_block_till_done()
        assert migrated["schema"] == 3 and migrated["migrated_from_schema"] == 2
        assert json.loads(path.read_text())["version"] == 3
        again = await ShadowStore(hass, "e1", {"connector": "synthetic"}).async_load()
        assert again == migrated
        assert {k: v for k, v in old.items() if k != "schema"} == {
            k: migrated[k] for k in old if k != "schema"}
    finally:
        await hass.async_stop(force=True)


def test_inspect_script_reports_unregistered_and_refused_files_read_only(tmp_path):
    storage = tmp_path / ".storage"
    storage.mkdir()
    good = storage / "bsv_settlement.proxy.e1"
    good.write_text(json.dumps({"version": 1, "minor_version": 1, "key": good.name, "data": {
        "observations": {}, "archive": [], "persistent_issues": [], "checkpoint_at": None}}))
    future = storage / "bsv_settlement.embedded.e1"
    future.write_text(json.dumps({"version": 1, "minor_version": 9, "key": future.name, "data": {}}))
    (storage / "bsv_settlement.unknown.e1").write_text("{}")
    (storage / "core.config").write_text("{}")
    before = {p.name: p.read_bytes() for p in storage.iterdir()}
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "inspect_records.py"), str(storage)],
                            capture_output=True, text=True, cwd=ROOT)
    assert result.returncode == 1, result.stderr
    rows = {r["file"]: r["result"] for r in json.loads(result.stdout)}
    assert rows == {"bsv_settlement.embedded.e1": rows["bsv_settlement.embedded.e1"],
                    "bsv_settlement.proxy.e1": "ok", "bsv_settlement.unknown.e1": "unregistered"}
    assert rows["bsv_settlement.embedded.e1"].startswith("refused")
    assert {p.name: p.read_bytes() for p in storage.iterdir()} == before


# --- Audit log ---------------------------------------------------------------

async def test_audit_chain_records_payment_transitions_and_verifies(tmp_path):
    api, row = await paid(tmp_path)
    doc = json.loads(audit_file(api).read_text())["data"]
    verify(doc, api.entry.data["operator_public_key"])
    assert api.entry.data[AUDIT_MARKER] == 1
    entries = doc["entries"]
    assert entries[0]["event"] == "baseline" and entries[0]["prev"] == GENESIS
    credit = [e for e in entries if e["ns"] == "automatic_credits"]
    assert [e["event"] for e in credit][0] == "created"
    assert credit[-1]["to"] == "provider_confirmed"
    budget_id = row["terms"]["budget_id"]
    assert any(e["ref"] == ref("session_budgets", budget_id) for e in entries)
    assert all(e["ledger_sequence"] <= api.store.sequence for e in entries)
    assert entries[-1]["ledger_digest"] in (api.store.last_digest, *[e["ledger_digest"] for e in entries])
    assert api.status()["record_audit"]["state"] == "ready"


async def test_audit_contains_no_secrets_raw_transactions_addresses_or_links(tmp_path):
    api, _ = await paid(tmp_path)
    text = audit_file(api).read_text()
    leaves = set()

    def walk(value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(k)
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str) and len(value) >= 16:
            leaves.add(value)
    walk(api.saved)
    walk(api.identity)
    leaves.discard(api.identity["public_key"])  # Public identity binds the log.
    # Enumerated family/state names (letters and underscores) are the payload.
    leaked = [v for v in leaves if v in text and not re.fullmatch(r"[a-z]+(_[a-z]+)*", v)]
    assert not leaked, leaked[:3]
    for word in ("secret", "signed_raw", "token", "http", api.identity["address"]):
        assert word not in text


async def test_restart_replay_is_identical_and_never_pays_again(tmp_path):
    api, row = await paid(tmp_path)
    ledger, audit = Path(api.store.ledger.path).read_bytes(), audit_file(api).read_bytes()
    first = await reload(api)
    second = await reload(first)
    for restored in (first, second):
        assert restored.saved == api.saved
        assert restored.store.audit.doc == api.store.audit.doc
    await second.auto_credits.tick()
    assert len(api.chain.posts) == 1
    assert Path(api.store.ledger.path).read_bytes() == ledger
    assert audit_file(api).read_bytes() == audit


async def test_tampered_chain_is_reported_never_repaired_and_settlement_continues(tmp_path):
    api, row = await paid(tmp_path)
    path = audit_file(api)
    raw = json.loads(path.read_text())
    raw["data"]["entries"][1]["to"] = "waived"
    path.write_text(json.dumps(raw))
    tampered = path.read_bytes()
    restored = await reload(api)
    summary = restored.status()["record_audit"]
    assert summary["state"] == "broken" and "verification" in summary["problem"]
    await restored.store.async_save({**restored.saved, "chain": {**restored.saved["chain"], "error": "x"}})
    assert path.read_bytes() == tampered
    assert len(api.chain.posts) == 1


@pytest.mark.parametrize("damage", ["drop_middle", "reorder", "baseline", "anchor", "identity", "version"])
async def test_chain_damage_variants_are_detected(tmp_path, damage):
    api, _ = await paid(tmp_path)
    path = audit_file(api)
    raw = json.loads(path.read_text())
    doc = raw["data"]
    if damage == "drop_middle":
        del doc["entries"][2]
    elif damage == "reorder":
        doc["entries"][1], doc["entries"][2] = doc["entries"][2], doc["entries"][1]
    elif damage == "baseline":
        doc["baseline"]["payments"] = {"0" * 32: "provider_confirmed"}
    elif damage == "anchor":
        doc["anchor"] = {"seq": 0, "hash": "1" * 64}
    elif damage == "identity":
        doc["identity"] = "02" + "11" * 32
    else:
        raw["minor_version"] = 2
    path.write_text(json.dumps(raw))
    if damage != "version":
        with pytest.raises(ValueError):
            verify(doc, api.entry.data["operator_public_key"])
    restored = await reload(api)
    assert restored.store.audit.state == "broken"
    assert path.read_text() == json.dumps(raw)


async def test_missing_established_audit_log_is_reported_not_recreated(tmp_path):
    api, _ = await paid(tmp_path)
    await api.store.audit.store.async_remove()
    restored = await reload(api)
    assert restored.store.audit.state == "missing"
    await restored.store.async_save({**restored.saved, "chain": {**restored.saved["chain"], "error": "x"}})
    assert not audit_file(api).exists()


async def test_failed_append_never_raises_or_pays_and_catches_up_on_next_save(tmp_path):
    hass, api, proxy, row, driver, data = await ready(tmp_path)
    original = api.store.audit.store.async_save
    api.store.audit.store.async_save = AsyncMock(side_effect=OSError("fictional disk failure"))
    await api.auto_credits.tick()
    await api.auto_credits.tick()
    assert api.store.audit.state == "write_failed"
    assert api.auto_credits.get(row)["state"] == "provider_confirmed"
    assert len(api.chain.posts) == 1
    api.store.audit.store.async_save = original
    await api.store.async_save({**api.saved, "chain": {**api.saved["chain"], "error": "x"}})
    doc = api.store.audit.doc
    verify(doc, api.entry.data["operator_public_key"])
    assert api.store.audit.state == "ready"
    assert doc["entries"][-1]["to"] == "provider_confirmed" or any(
        e["ns"] == "automatic_credits" and e["to"] == "provider_confirmed" for e in doc["entries"])
    assert doc["baseline"] == project(api.saved)


async def test_transitions_missed_across_restart_are_recorded_on_load(tmp_path):
    hass, api, proxy, row, driver, data = await ready(tmp_path)
    api.store.audit.store.async_save = AsyncMock(side_effect=OSError("fictional disk failure"))
    await api.auto_credits.tick()
    restored = await reload(api)
    entries = restored.store.audit.doc["entries"]
    assert any(e["ns"] == "automatic_credits" and e["event"] == "created" for e in entries)
    assert restored.store.audit.doc["baseline"] == project(restored.saved)
    assert len(api.chain.posts) == 1


def test_projection_and_transitions_are_pure_deterministic_and_replayable():
    old = {"payments": {"p1": {"state": "prepared", "signed_raw": "00" * 40}},
           "session_budgets": {"b1": {"state": "awaiting_driver_consent"}},
           "automatic_credit_policy": {"enabled": False},
           "monthly_authorities": {"authorities": {"a1": {"cancel_proof": None}}, "bindings": {}}}
    new = copy.deepcopy(old)
    new["payments"]["p1"]["state"] = "submitted"
    new["payments"]["p2"] = {"state": "Not A State!"}
    del new["session_budgets"]["b1"]
    new["automatic_credit_policy"]["enabled"] = True
    new["monthly_authorities"]["authorities"]["a1"]["cancel_proof"] = {"sig": "x"}
    new["monthly_authorities"]["bindings"]["acct"] = {"authority_id": "a1"}
    a, b = project(old), project(new)
    rows = transitions(a, b)
    assert rows == transitions(project(copy.deepcopy(old)), project(copy.deepcopy(new)))
    assert {(r[0], r[1], r[4]) for r in rows} == {
        ("state", "payments", "submitted"), ("created", "payments", "unrecognised"),
        ("removed", "session_budgets", None), ("state", "automatic_credit_policy", "enabled"),
        ("state", "monthly_authorities.authorities", "cancelled"),
        ("created", "monthly_authorities.bindings", "bound")}
    replayed = copy.deepcopy(a)
    for _, ns, key, _, after in rows:
        if after is None:
            del replayed[ns][key]
        else:
            replayed.setdefault(ns, {})[key] = after
    replayed = {ns: rows for ns, rows in replayed.items() if rows}
    assert replayed == b and digest(replayed) == digest(b)
    assert "00" * 40 not in json.dumps(rows)


async def test_retention_is_bounded_and_trimmed_chain_still_verifies(tmp_path, monkeypatch):
    api, _ = await paid(tmp_path)
    monkeypatch.setattr(audit_module, "MAX_ENTRIES", 5)
    for n in range(4):
        windows = {f"w{n}{i}": {"state": "open"} for i in range(3)}
        await api.store.async_save({**api.saved, "public_registration_windows": windows})
    doc = api.store.audit.doc
    assert len(doc["entries"]) == 5 and doc["anchor"]["seq"] > 0
    verify(doc, api.entry.data["operator_public_key"])
    restored = await reload(api)
    assert restored.store.audit.state == "ready"


def history(api):
    return [(e["event"], e["ns"], e["from"], e["to"]) for e in api.store.audit.doc["entries"]]


async def test_restart_at_every_credit_transition_retains_one_account_and_its_history(tmp_path):
    straight, row = await paid(tmp_path / "straight")
    _, api, _, row, _, _ = await ready(tmp_path / "restarted")
    seen = []
    for _ in range(2):
        api = await reload(api)  # Restart before each transition...
        assert history(api)[:len(seen)] == seen  # ...never rewrites earlier history.
        await api.auto_credits.tick()
        seen = history(api)
        verify(api.store.audit.doc, api.entry.data["operator_public_key"])
    api = await reload(api)
    assert api.auto_credits.get(row)["state"] == "provider_confirmed"
    assert len(api.chain.posts) == 1
    assert len([k for k in api.saved["automatic_credits"]]) == 1
    # Interrupted and uninterrupted runs record the same transition sequence.
    assert history(api) == history(straight)
