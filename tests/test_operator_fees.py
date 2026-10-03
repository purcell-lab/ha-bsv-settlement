"""Fee-aware operator signing: fake chain only, never network or live funds."""
import copy
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from bsv import Transaction
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.fees import (
    MAX_SIGNED_BYTES, fee_for_bytes, quote, validate, now)
from custom_components.bsv_settlement.mainnet import WoCClient
from test_auto_credit import ready
from test_budget import no_network
from test_mainnet import setup_wallet, approval
from test_operator_credit_recovery import prepared, approval as recovery_approval


def policy(rate=100, minimum=100):
    return {"fee_unit": "sat/KB", "fee": rate, "mempool_min_fee": minimum}


@pytest.mark.asyncio
async def test_provider_endpoint_and_decimal_rounding():
    chain = WoCClient(None)
    chain.request = AsyncMock(return_value=policy())
    q = await quote(chain)
    chain.request.assert_awaited_once_with("GET", "/feerecommendation")
    assert q["fee_sats"] == 23 and q["estimated_signed_bytes"] == 227
    assert q["rate_sat_per_kb"] == "100"
    validate(q, 23)
    with pytest.raises(WalletError, match="below"):
        validate(q, 22)
    assert fee_for_bytes(Decimal("100"), 225) == 23
    assert fee_for_bytes(Decimal("0.1"), 227) == 1
    assert fee_for_bytes(Decimal("100.01"), 227) == 23


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    {}, None, [], {"fee_unit": "sat/B", "fee": 100, "mempool_min_fee": 100},
    policy(None), policy(True), policy(-1), policy(float("nan")),
    policy(float("inf")), policy("100"), policy(100, None), policy(100, False),
    policy(0, 0),
])
async def test_bad_quotes_fail_without_fallback(value):
    chain = type("Fake", (), {"fee_policy": AsyncMock(return_value=value)})()
    with pytest.raises(WalletError):
        await quote(chain)


@pytest.mark.asyncio
@pytest.mark.parametrize("seconds", [61, -1])
async def test_stale_or_future_quote_blocks(seconds):
    chain = type("Fake", (), {"fee_policy": AsyncMock(return_value=policy())})()
    q = await quote(chain)
    q["observed_at"] = (now() - timedelta(seconds=seconds)).isoformat()
    with pytest.raises(WalletError, match="stale"):
        validate(q, 23)


@pytest.mark.asyncio
async def test_higher_of_recommendation_and_mempool_minimum():
    chain = type("Fake", (), {"fee_policy": AsyncMock(return_value=policy(40, 200))})()
    q = await quote(chain)
    assert q["fee_sats"] == 46 and q["rate_sat_per_kb"] == "200"


@pytest.mark.asyncio
async def test_automatic_credit_uses_live_fee_and_real_signed_size(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path, "-1.19")
    api.chain.fee_policy = AsyncMock(return_value=policy())
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["amount_sats"] == 119 and item["fee_sats"] == 23
    assert item["fee_quote"]["rate_sat_per_kb"] == "100"
    assert api.chain.fee_policy.await_count == 2
    tx = Transaction.from_hex(api.chain.posts[0])
    assert len(tx.hex()) // 2 <= MAX_SIGNED_BYTES
    assert 50000 - sum(o.satoshis for o in tx.outputs) == 23
    assert tx.outputs[0].satoshis == 119
    assert api.auto_credits.summary()["fee_sats"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("amount,posts", [("-9.77", 1), ("-9.78", 0)])
async def test_total_cap_includes_the_quote_not_a_fixed_fee(tmp_path, amount, posts):
    _, api, _, _, _, _ = await ready(tmp_path, amount)
    api.chain.fee_policy = AsyncMock(return_value=policy())
    await api.auto_credits.tick()
    assert len(api.chain.posts) == posts


@pytest.mark.asyncio
@pytest.mark.parametrize("rate,fee,posts", [(3881,881,1),(3882,882,0)])
async def test_no_independent_fee_ceiling_only_remaining_total(tmp_path, rate, fee, posts):
    _, api, _, row, _, _ = await ready(tmp_path, "-1.19")
    api.chain.fee_policy = AsyncMock(return_value=policy(rate,rate))
    await api.auto_credits.tick()
    assert len(api.chain.posts) == posts
    if posts:
        item = api.auto_credits.get(row)
        assert item["fee_sats"] == fee
        assert item["amount_sats"] + fee == 1000


@pytest.mark.asyncio
async def test_sub_ten_sat_fee_still_obeys_total_cap(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path, "-9.99")
    api.chain.fee_policy = AsyncMock(return_value=policy(1, 1))
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["amount_sats"] == 999 and item["fee_sats"] == 1
    assert len(api.chain.posts) == 1


@pytest.mark.asyncio
async def test_quote_outage_stops_new_work_but_does_not_break_signed_reconciliation(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path)
    api.chain.fee_policy = AsyncMock(side_effect=WalletError("Fee provider unavailable"))
    await api.auto_credits.tick()
    assert not api.chain.posts
    api.chain.fee_policy = AsyncMock(return_value=policy())
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    original = {k: item[k] for k in ("txid", "signed_raw", "fee_sats")}
    api.chain.fee_policy = AsyncMock(side_effect=WalletError("Fee provider unavailable"))
    await api.auto_credits.tick()
    assert item["state"] == "provider_confirmed"
    assert {k: item[k] for k in original} == original
    assert len(api.chain.posts) == 1
    api.chain.fee_policy.assert_not_awaited()


@pytest.mark.asyncio
async def test_quote_rise_before_signing_pauses_unsigned_then_reprices_within_cap(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path)
    api.chain.fee_policy = AsyncMock(side_effect=[policy(), policy(200, 200)])
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert not api.chain.posts and not item.get("txid")
    api.chain.fee_policy = AsyncMock(return_value=policy(200, 200))
    await api.auto_credits.tick()
    assert len(api.chain.posts) == 1 and item["fee_sats"] == 46


@pytest.mark.asyncio
async def test_legacy_unsigned_item_can_reprice_but_amount_and_account_never_change(tmp_path):
    _, api, proxy, row, _, _ = await ready(tmp_path)
    funds = api.chain.rows
    api.chain.rows = []
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["fee_sats"] == 10
    frozen = (item["source_hash"], item["amount_sats"], item["recipient_address"])
    api.chain.rows = funds
    api.chain.fee_policy = AsyncMock(return_value=policy())
    await api.auto_credits.tick()
    assert item["fee_sats"] == 23 and len(api.chain.posts) == 1
    assert (item["source_hash"], item["amount_sats"], item["recipient_address"]) == frozen


@pytest.mark.asyncio
async def test_recovery_never_silently_raises_reviewed_fee(tmp_path):
    _, api, _, _, route, review = await prepared(tmp_path)
    assert review["fee_sats"] == 10
    api.chain.fee_policy = AsyncMock(return_value=policy())
    before = copy.deepcopy(route["manual_recovery"])
    with pytest.raises(WalletError, match="below"):
        await api.credit_recovery.broadcast(recovery_approval(review), "admin")
    assert route["manual_recovery"]["terms"] == before["terms"]
    assert not api.chain.posts


@pytest.mark.asyncio
async def test_manual_exact_fee_cannot_bypass_current_floor(tmp_path):
    _, _, api = await setup_wallet(tmp_path)
    api.chain.fee_policy = AsyncMock(return_value=policy())
    with pytest.raises(WalletError, match="below"):
        await api.prepare_payment({"reference": "manual-low-fee", "amount_sats": 119, "fee_sats": 10})
    draft = await api.prepare_payment({"reference": "manual-valid-fee", "amount_sats": 119, "fee_sats": 23})
    api.chain.fee_policy = AsyncMock(return_value=policy(200, 200))
    with pytest.raises(WalletError, match="below"):
        await api.broadcast_payment(approval(draft), "admin")
    assert not api.chain.posts and api.public_payment(api.saved["payments"][draft["draft_id"]])["fee_sats"] == 23


@pytest.mark.asyncio
async def test_actual_size_over_bound_fails_before_network_write(tmp_path, monkeypatch):
    _, api, _, _, _, _ = await ready(tmp_path)
    from custom_components.bsv_settlement import auto_credit
    original = auto_credit.build_transaction
    def too_large(*args):
        signed = original(*args)
        signed["raw"] += "00" * 300
        return signed
    monkeypatch.setattr(auto_credit, "build_transaction", too_large)
    await api.auto_credits.tick()
    assert not api.chain.posts


@pytest.mark.asyncio
async def test_recovery_preparation_and_send_use_the_same_dynamic_fee(tmp_path):
    from test_ongoing_credit import setup
    _, api, _, _, _, _ = await setup(tmp_path, "-1.19")
    await api.ongoing_credits.configure({"enabled": False}, "admin")
    route = next(iter(api.ongoing_credits.routes.values()))
    api.chain.fee_policy = AsyncMock(return_value=policy())
    review = await api.credit_recovery.prepare({"credit_id": route["route_id"]}, "admin")
    assert review["fee_sats"] == 23 and review["total_sats"] == 142
    assert review["fee_quote"]["rate_sat_per_kb"] == "100"
    await api.credit_recovery.broadcast(recovery_approval(review), "admin")
    tx = Transaction.from_hex(api.chain.posts[0])
    assert 50000 - sum(o.satoshis for o in tx.outputs) == 23
    assert tx.outputs[0].satoshis == 119


@pytest.mark.asyncio
async def test_quote_outage_never_replaces_pending_old_fee_transaction(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path, "-0.38")
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["fee_sats"] == 10
    frozen = {k: item[k] for k in ("signed_raw", "txid", "fee_sats", "amount_sats")}
    api.chain.details = AsyncMock(return_value={"txid": item["txid"], "confirmations": 0})
    api.chain.fee_policy = AsyncMock(side_effect=WalletError("No quote"))
    await api.auto_credits.tick()
    assert item["state"] == "provider_unconfirmed"
    assert {k: item[k] for k in frozen} == frozen
    api.chain.fee_policy.assert_not_awaited()
    assert len(api.chain.posts) == 1
