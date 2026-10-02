"""Fictional stores only. A local witness cannot detect coherent rollback."""
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.ledger_checkpoint import MARKER, fingerprint
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from test_auto_credit import ready
from test_budget import no_network

pytestmark = pytest.mark.asyncio


async def reload(api):
    restored = MainnetWalletAPI(api.hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    return restored


async def test_matching_restart_preserves_identity_exact_signed_bytes_and_reservation(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path)
    await api.auto_credits.tick()
    original = copy.deepcopy(api.auto_credits.get(row))
    restored = await reload(api)
    assert restored.identity == api.identity
    assert restored.auto_credits.get(row) == original
    await restored.auto_credits.tick()
    assert len(api.chain.posts) == 1
    assert (original["source_txid"], original["source_index"]) in restored.auto_credits.used()
    assert Path(api.store.witness.path).stat().st_mode & 0o777 == 0o600
    assert "secret" not in json.dumps(await api.store.witness.async_load())


async def test_old_ledger_with_retained_new_witness_fails_before_provider_access(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path)
    old = copy.deepcopy(api.saved)
    await api.auto_credits.tick()
    await api.store.ledger.async_save(old)  # Simulated stale ledger-only restore.
    with pytest.raises(WalletError, match="restore"):
        await reload(api)
    assert len(api.chain.posts) == 1


@pytest.mark.parametrize("missing", ["ledger", "witness", "both"])
async def test_missing_established_store_never_initialises_empty_wallet(tmp_path, missing):
    _, api, _, _, _, _ = await ready(tmp_path)
    if missing in ("ledger", "both"):
        await api.store.ledger.async_remove()
    if missing in ("witness", "both"):
        await api.store.witness.async_remove()
    with pytest.raises(WalletError, match="restore"):
        await reload(api)
    assert not api.chain.posts


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("version", True), ("sequence", 0), ("sequence", True),
    ("identity", "fictional-wrong-key"), ("digest", "0" * 64),
])
async def test_invalid_or_foreign_checkpoint_fails_closed(tmp_path, field, value):
    _, api, _, _, _, _ = await ready(tmp_path)
    witness = await api.store.witness.async_load()
    witness[field] = value
    await api.store.witness.async_save(witness)
    with pytest.raises(WalletError, match="restore"):
        await reload(api)
    assert not api.chain.posts


@pytest.mark.parametrize("cancel", [False, True])
async def test_witness_ahead_of_failed_ledger_blocks_instance_and_restart(tmp_path, cancel):
    _, api, _, row, _, _ = await ready(tmp_path)
    failure = asyncio.CancelledError() if cancel else OSError("fictional disk failure")
    api.store.ledger.async_save = AsyncMock(side_effect=failure)
    with pytest.raises(asyncio.CancelledError if cancel else WalletError):
        await api.store.async_save({**api.saved, "fictional_change": 1})
    assert api.store.blocked and not api.chain.posts
    with pytest.raises(WalletError, match="checkpoint"):
        await api.store.async_save(api.saved)
    with pytest.raises(WalletError, match="restore"):
        await reload(api)


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_witness_write_blocks_memory_but_matching_disk_can_reload(tmp_path, cancel):
    _, api, _, _, _, _ = await ready(tmp_path)
    failure = asyncio.CancelledError() if cancel else OSError("fictional disk failure")
    api.store.witness.async_save = AsyncMock(side_effect=failure)
    with pytest.raises(asyncio.CancelledError if cancel else WalletError):
        await api.store.async_save({**api.saved, "fictional_change": 1})
    assert api.store.blocked
    restored = await reload(api)
    assert "fictional_change" not in restored.saved and not api.chain.posts


async def test_legacy_adoption_is_one_time_baseline_not_freshness_attestation(tmp_path):
    _, api, _, _, _, _ = await ready(tmp_path)
    legacy = copy.deepcopy(api.saved)
    legacy.pop(MARKER)
    await api.store.ledger.async_save(legacy)
    await api.store.witness.async_remove()
    entry_data = dict(api.entry.data)
    entry_data.pop(MARKER)
    api.hass.config_entries.async_update_entry(api.entry, data=entry_data)
    restored = await reload(api)
    assert restored.saved[MARKER] == 1 and restored.entry.data[MARKER] == 1
    expected = copy.deepcopy(legacy)
    expected[MARKER] = 1
    assert restored.saved == expected
    assert (await restored.store.witness.async_load())["digest"] == fingerprint(expected)
    assert not api.chain.posts


async def test_legacy_missing_ledger_is_rejected_before_adoption(tmp_path):
    _, api, _, _, _, _ = await ready(tmp_path)
    await api.store.ledger.async_remove()
    await api.store.witness.async_remove()
    data = dict(api.entry.data)
    data.pop(MARKER)
    api.hass.config_entries.async_update_entry(api.entry, data=data)
    with pytest.raises(WalletError, match="restore"):
        await reload(api)
    assert await api.store.ledger.async_load() is None
    assert not api.chain.posts


async def test_coherent_rollback_is_explicitly_outside_local_witness_assurance(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path)
    old_ledger = copy.deepcopy(api.saved)
    old_witness = await api.store.witness.async_load()
    await api.auto_credits.tick()
    await api.store.ledger.async_save(old_ledger)
    await api.store.witness.async_save(old_witness)
    restored = await reload(api)
    assert restored.auto_credits.get(row) is None
    # Never tick or enable a restored production copy based on this check.
    # Both old files agree, so independent retained evidence remains mandatory.
    assert len(api.chain.posts) == 1


async def test_snapshot_is_detached_before_witness_await_and_noop_does_not_write(tmp_path):
    _, api, _, _, _, _ = await ready(tmp_path)
    data = copy.deepcopy(api.saved)
    data["test"] = {"value": 1}
    save_witness = api.store.witness.async_save
    async def mutate_after_hash(witness):
        data["test"]["value"] = 2
        await save_witness(witness)
    api.store.witness.async_save = mutate_after_hash
    await api.store.async_save(data)
    disk = await api.store.ledger.async_load()
    witness = await api.store.witness.async_load()
    assert disk["test"]["value"] == 1 and witness["digest"] == fingerprint(disk)
    api.store.witness.async_save = AsyncMock(side_effect=AssertionError("No-op wrote"))
    await api.store.async_save(disk)


async def test_concurrent_saves_leave_one_matching_checkpoint_and_ledger(tmp_path):
    _, api, _, _, _, _ = await ready(tmp_path)
    sequence = api.store.sequence
    await asyncio.gather(
        api.store.async_save({**api.saved, "fictional_serial": 1}),
        api.store.async_save({**api.saved, "fictional_serial": 2}))
    witness = await api.store.witness.async_load()
    ledger = await api.store.ledger.async_load()
    assert witness["sequence"] == sequence + 2
    assert witness["digest"] == fingerprint(ledger)
    assert ledger["fictional_serial"] in (1, 2)
    assert not api.chain.posts
