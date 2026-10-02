"""Balance regression uses fictional transactions; never a live broadcaster."""
from types import SimpleNamespace

import pytest
from bsv import P2PKH, Transaction, TransactionInput, TransactionOutput

from custom_components.bsv_settlement import mainnet
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.sensor import SettlementSensor
from test_auto_credit import CreditChain, ready
from test_budget import no_network

pytestmark = pytest.mark.asyncio


class BalanceChain(CreditChain):
    def __init__(self, address):
        super().__init__(address)
        tx = Transaction([TransactionInput(source_txid="11" * 32)],
                         [TransactionOutput(P2PKH().lock(address), satoshis=5000)])
        self.raw = tx.hex()
        self.rows = [{"tx_hash": tx.txid(), "tx_pos": 0, "value": 5000,
                      "height": 1, "isSpentInMempoolTx": False}]
        self.small = {"tx_hash": "22" * 32, "tx_pos": 1, "value": 76,
                      "height": 1, "isSpentInMempoolTx": False}
        self.confirmed = False
        self.stale = False
        self.balance_failure = False
        self.balance_reads = 0

    async def unspent(self, address):
        self.balance_reads += 1
        if self.balance_failure:
            raise WalletError("Fictional balance outage")
        if not self.posts or self.stale:
            return [*self.rows, self.small]
        if not self.confirmed:
            return [self.small]
        tx = Transaction.from_hex(self.posts[0])
        return [self.small, {"tx_hash": tx.txid(), "tx_pos": 1,
                            "value": tx.outputs[1].satoshis,
                            "height": 2, "isSpentInMempoolTx": False}]

    async def details(self, txid):
        return {"txid": txid, "confirmations": int(self.confirmed)}


async def wallet(tmp_path):
    hass, api, _, row, _, _ = await ready(tmp_path, "-2.80")
    api.chain = BalanceChain(api.identity["address"])
    return hass, api, row


async def test_5076_to_76_pending_then_4786_on_confirmation(tmp_path):
    hass, api, row = await wallet(tmp_path)
    await api.refresh_balance_if_due()
    assert api.status()["balance_sats"] == 5076
    coordinator = SettlementCoordinator(hass, api.entry, api)
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    item = api.auto_credits.get(row)
    assert item["state"] == "submitted"
    sensor = SettlementSensor(coordinator, api.entry, "confirmed_wallet_balance", "Balance", "sat")
    assert sensor.native_value == 76
    assert sensor.extra_state_attributes["pending_change_sats"] == 4710
    assert api.status()["chain_error"] is None
    api.chain.confirmed = True
    coordinator.async_set_updated_data(await coordinator._async_update_data())
    assert item["state"] == "provider_confirmed"
    assert sensor.native_value == 4786
    assert sensor.extra_state_attributes["pending_change_sats"] == 0
    assert len(api.chain.posts) == 1


async def test_stale_provider_cannot_count_signed_input_as_spendable(tmp_path):
    _, api, _ = await wallet(tmp_path)
    api.chain.stale = True
    await api.auto_credits.tick()
    assert api.status()["balance_sats"] == 76
    assert api.status()["pending_change_sats"] == 4710
    assert len(api.chain.posts) == 1


async def test_confirmation_balance_failure_preserves_payment_and_backs_off(tmp_path, monkeypatch):
    _, api, row = await wallet(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(mainnet, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    await api.auto_credits.tick()
    api.chain.confirmed = True
    api.chain.balance_failure = True
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["state"] == "provider_confirmed"
    assert item["error"] is None
    assert api.status()["balance_sats"] is None
    assert api.status()["chain_error"] == "chain_check_failed"
    assert api.status()["chain_attempted_at"]
    reads = api.chain.balance_reads
    await api.refresh_balance_if_due()
    assert api.chain.balance_reads == reads
    api.chain.balance_failure = False
    clock[0] += 300
    await api.refresh_balance_if_due()
    assert api.status()["balance_sats"] == 4786
    assert api.status()["chain_error"] is None
    assert len(api.chain.posts) == 1


async def test_periodic_refresh_every_five_minutes_and_one_minute_pending(tmp_path, monkeypatch):
    _, api, _ = await wallet(tmp_path)
    clock = [1000.0]
    monkeypatch.setattr(mainnet, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    await api.refresh_balance_if_due()
    count = api.chain.balance_reads
    clock[0] += 299
    await api.refresh_balance_if_due()
    assert api.chain.balance_reads == count
    clock[0] += 1
    await api.refresh_balance_if_due()
    assert api.chain.balance_reads == count + 1
    await api.auto_credits.tick()
    count = api.chain.balance_reads
    clock[0] += 59
    await api.refresh_balance_if_due()
    assert api.chain.balance_reads == count
    clock[0] += 1
    await api.refresh_balance_if_due()
    assert api.chain.balance_reads == count + 1
    assert len(api.chain.posts) == 1


async def test_restart_refreshes_legacy_cached_balance_without_resubmission(tmp_path):
    hass, api, row = await wallet(tmp_path)
    await api.auto_credits.tick()
    api.chain.confirmed = True
    await api.auto_credits.tick()
    # Reproduce a store written by the old build: confirmed payment, stale 76.
    api.saved["chain"]["balance_sats"] = 76
    await api.store.async_save(api.saved)
    restored = mainnet.MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    coordinator = SettlementCoordinator(hass, api.entry, restored)
    result = await coordinator._async_update_data()
    assert result["health"]["balance_sats"] == 4786
    assert restored.auto_credits.get(row)["state"] == "provider_confirmed"
    assert len(api.chain.posts) == 1


async def test_unknown_broadcast_balance_refresh_never_rebroadcasts(tmp_path):
    _, api, row = await wallet(tmp_path)
    api.chain.fail = True
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["state"] == "broadcast_unknown"
    assert api.status()["balance_sats"] == 76
    assert api.status()["pending_change_sats"] == 4710
    error = item["error"]
    await api.refresh_chain(reconcile_payment=False)
    assert item["state"] == "broadcast_unknown" and item["error"] == error
    assert len(api.chain.posts) == 1


async def test_submission_refresh_failure_does_not_mark_success_unknown(tmp_path):
    _, api, row = await wallet(tmp_path)
    broadcast = api.chain.broadcast

    async def submitted_then_balance_outage(raw):
        result = await broadcast(raw)
        api.chain.balance_failure = True
        return result

    api.chain.broadcast = submitted_then_balance_outage
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["state"] == "submitted" and item["error"] is None
    assert api.status()["balance_sats"] is None
    assert api.status()["chain_error"] == "chain_check_failed"
    assert len(api.chain.posts) == 1
