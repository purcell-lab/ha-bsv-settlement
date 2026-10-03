"""Aggregate consent tests: fictional wallets, no network or real funds."""
import copy
import json
from datetime import timedelta

import pytest
from bsv import PrivateKey
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.budget import canonical, message_hash
from custom_components.bsv_settlement.collection import transaction_shape
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import now
from custom_components.bsv_settlement.weekly import (
    ticket, remaining, verify_parent, guard_child, candidates, summary,
)
from test_budget import invitation, consent, no_network
from test_auto_credit import proof
from test_collection import CollectionChain, claim_data, payment

pytestmark = pytest.mark.asyncio


async def setup(tmp_path, monkeypatch, fee=10):
    clock = [now()]
    for module in ("budget", "weekly", "collection", "auto_credit", "ongoing_credit"):
        monkeypatch.setattr(f"custom_components.bsv_settlement.{module}.now", lambda: clock[0])
    hass, api, proxy, data, old = await invitation(tmp_path)
    await api.auto_credits.configure(True, "admin")
    args = {k: v for k, v in data.items() if k not in ("session_id", "valid_minutes")}
    args.update(multi_session=True, max_fee_sats=fee)
    result = await api.budgets.create(args, "admin")
    row = api.saved["session_budgets"][result["terms"]["budget_id"]]
    driver = PrivateKey()
    await api.budgets.accept(row, consent(row, driver), "driver")
    await api.auto_credits.register(row, proof(api, row, driver))
    api.chain = CollectionChain(driver.address())
    return hass, api, proxy, row, driver, args, clock


def session(proxy, clock, sid="new-1", amount="4.00"):
    previous = copy.deepcopy(proxy.data["latest_session"])
    if previous:
        proxy.archive.append(previous)
    clock[0] += timedelta(seconds=1)
    record = copy.deepcopy(previous)
    record.update(session_id=sid, ocpp_transaction_id="tx-"+sid,
                  opened_at=clock[0].isoformat(), ended_at=None,
                  net_cost_aud_unrounded=amount, net_cost_aud=amount)
    clock[0] += timedelta(seconds=1)
    record["ended_at"] = clock[0].isoformat()
    proxy.data["latest_session"] = record
    return record


async def prepare(api, parent, driver, sid, amount, fee=5):
    child = await ticket(api, parent, sid)
    quoted = await api.collections.status(child)
    assert quoted["state"] == "ready", quoted
    args = claim_data(quoted, child, driver)
    await api.collections.claim(child, args)
    tx = payment(api, driver, amount=amount, fee=fee)
    await api.collections.authorise(child, args | {"draft": transaction_shape(tx)})
    return child, args, tx


async def test_fresh_seven_day_signature_and_no_existing_approval_upgrade(tmp_path, monkeypatch):
    _, api, _, row, _, args, _ = await setup(tmp_path, monkeypatch)
    t = row["terms"]
    assert t["version"] == 3 and t["max_total_sats"] == 1000
    assert json.loads(row["receipt"]["payload"])["action"] == "authorise_multi_session_aggregate_spending"
    assert t["payment_authority"]["credits_replenish_budget"] is False
    assert t["payment_authority"]["max_payments"] is None
    from datetime import datetime
    assert datetime.fromisoformat(t["expires_at"]) - datetime.fromisoformat(t["created_at"]) == timedelta(days=7)
    assert any(r["terms"]["version"] == 2 for r in api.saved["session_budgets"].values())
    verify_parent(api, row)
    for patch in ({"valid_minutes":10081}, {"session_id":"session-1"}, {"multi_session":"yes"}):
        with pytest.raises(WalletError):
            await api.budgets.create(args | patch, "admin")
    assert not api.chain.posts


async def test_two_sessions_share_total_including_actual_fees_and_restart(tmp_path, monkeypatch):
    hass, api, proxy, row, driver, _, clock = await setup(tmp_path, monkeypatch)
    session(proxy, clock)
    child = await ticket(api, row, "new-1")
    await api.collections.status(child)
    assert remaining(api, row) == 590  # 400 + maximum 10 fee reserved
    child, args, tx = await prepare(api, row, driver, "new-1", 400)
    assert remaining(api, row) == 595  # actual fee is 5, not 10
    await api.collections.report(child, args | {"raw_tx":tx.hex()})
    await api.collections.report(child, args | {"raw_tx":tx.hex()})
    assert len(api.chain.posts) == 1
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    root = restored.saved["session_budgets"][row["terms"]["budget_id"]]
    assert remaining(restored, root) == 595
    session(proxy, clock, "new-2", "5.90")
    second = await ticket(restored, root, "new-2")
    q = await restored.collections.status(second)
    assert json.loads(q["quote"]["payload"])["max_fee_sats"] == 5
    assert remaining(restored, root) == 0
    assert summary(restored, root)["committed_sats"] == 1000
    session(proxy, clock, "new-3", "0.01")
    with pytest.raises(WalletError, match="exhausted"):
        await ticket(restored, root, "new-3")


@pytest.mark.parametrize("state", ["wallet_attempt_reserved","broadcast_unknown","waived","provider_unconfirmed"])
async def test_uncertainty_or_waiver_does_not_refill_allowance(tmp_path, monkeypatch, state):
    _, api, proxy, row, _, _, clock = await setup(tmp_path, monkeypatch)
    session(proxy, clock)
    child = await ticket(api, row, "new-1")
    await api.collections.status(child)
    api.collections.get(child)["state"] = state
    api.saved["automatic_credits"]["unrelated-credit"] = {"amount_sats":999}
    assert remaining(api, row) == 590
    assert summary(api,row)["credits_replenish_budget"] is False


@pytest.mark.parametrize("problem", ["historical","quality","open","negative","manual","missing_registration"])
async def test_only_clean_closed_post_registration_positive_accounts(tmp_path, monkeypatch, problem):
    _, api, proxy, row, _, _, clock = await setup(tmp_path, monkeypatch)
    record = session(proxy, clock)
    if problem == "historical":
        record["opened_at"] = (clock[0] - timedelta(days=1)).isoformat()
    elif problem == "quality":
        record["quality_flags"] = ["meter_reset"]
    elif problem == "open":
        record["ended_at"] = None
    elif problem == "negative":
        record["net_cost_aud_unrounded"] = "-1.00"
    elif problem == "manual":
        api.saved["session_review_index"]["proxy-entry|new-1"] = "other"
    else:
        row.pop("credit_destination")
    with pytest.raises((WalletError, ValueError, TypeError)):
        await ticket(api, row, "new-1")
    assert not api.saved["driver_collections"] and not api.chain.posts


@pytest.mark.parametrize("stop", ["expiry","revoke","new_driver","changed_terms"])
async def test_stop_after_signing_preserves_hold_and_no_new_broadcast(tmp_path, monkeypatch, stop):
    _, api, proxy, row, driver, args, clock = await setup(tmp_path, monkeypatch)
    session(proxy, clock)
    child, claim, tx = await prepare(api, row, driver, "new-1", 400)
    if stop == "expiry":
        clock[0] += timedelta(days=8)
    elif stop == "revoke":
        row["state"] = "revoked"
    elif stop == "changed_terms":
        row["terms"]["max_total_sats"] = 2000
    else:
        clock[0] += timedelta(seconds=1)
        fresh = await api.budgets.create(args | {"multi_session":False}, "admin")
        newer = api.saved["session_budgets"][fresh["terms"]["budget_id"]]
        different = PrivateKey()
        await api.budgets.accept(newer, consent(newer,different), "driver")
        await api.auto_credits.register(newer,proof(api,newer,different))
        newer["state"] = "revoked"  # Never fall back to the old driver.
    with pytest.raises(WalletError):
        await api.collections.report(child, claim | {"raw_tx":tx.hex()})
    assert not api.chain.posts and remaining(api,row) >= 0
    assert api.collections.get(child)["state"] == "submission_authorised"


async def test_expired_parent_can_reconcile_existing_transaction_without_repayment(tmp_path, monkeypatch):
    _, api, proxy, row, driver, _, clock = await setup(tmp_path, monkeypatch)
    session(proxy, clock)
    child, claim, tx = await prepare(api,row,driver,"new-1",400)
    api.chain.fail = True
    result = await api.collections.report(child,claim | {"raw_tx":tx.hex()})
    assert result["state"] == "broadcast_unknown"
    clock[0] += timedelta(days=8)
    assert await ticket(api,row,"new-1") is child
    api.chain.fail = False
    assert (await api.collections.reconcile(child))["state"] == "provider_confirmed"
    assert len(api.chain.posts) == 1


async def test_child_tampering_and_standalone_consent_rejected(tmp_path, monkeypatch):
    _, api, proxy, row, driver, _, clock = await setup(tmp_path, monkeypatch)
    session(proxy, clock)
    child = await ticket(api,row,"new-1")
    with pytest.raises(WalletError,match="standalone"):
        await api.budgets.accept(child,consent(child,driver),"driver")
    child["invitation"]["signature"] = "00"*64
    with pytest.raises((WalletError,ValueError)):
        guard_child(api,child)
    assert not api.chain.posts


async def test_summary_retains_root_after_many_derived_tickets(tmp_path, monkeypatch):
    _, api, proxy, row, _, _, clock = await setup(tmp_path,monkeypatch)
    for n in range(22):
        sid=f"future-{n}"
        session(proxy,clock,sid,"0.01")
        await ticket(api,row,sid)  # No quote or reservation.
    assert any(r["budget_id"]==row["terms"]["budget_id"] for r in api.budgets.summary())
    assert len(await candidates(api,row)) == 22
    assert remaining(api,row) == 1000 and not api.chain.posts


async def test_http_concurrent_sessions_cannot_double_reserve_allowance(tmp_path, monkeypatch):
    import asyncio
    from test_driver_http import client_for, prices
    hass, api, proxy, row, _, _, clock = await setup(tmp_path,monkeypatch,fee=1000)
    session(proxy,clock,"first","4.00")
    session(proxy,clock,"second","4.00")
    prices(hass)
    client,view=await client_for(hass,api)
    access={"budget_id":row["terms"]["budget_id"],
            "token":api.budgets.link_token(row["terms"]["budget_id"])}
    try:
        responses=await asyncio.gather(*[
            client.post(view.url,json=access|{"action":"collection_status","session_id":sid})
            for sid in ("first","second")])
        results=[await r.json() for r in responses]
        assert sum(r.status==200 and result.get("state")=="ready"
                   for r,result in zip(responses,results)) == 1
        assert remaining(api,row)==0 and len(api.saved["driver_collections"])==1
        read=await client.post(view.url,json=access|{"action":"read"})
        assert (await read.json())["multi_session"]["remaining_sats"]==0
        denied=await client.post(view.url,json=access|{"token":"bad","action":"collection_status"})
        assert denied.status==400
        assert not api.chain.posts
    finally:
        await client.close()


async def test_weekly_python_typescript_consent_interoperability(tmp_path,monkeypatch):
    import asyncio
    from pathlib import Path
    _,api,_,row,_,_,_=await setup(tmp_path,monkeypatch)
    helper=Path(__file__).resolve().parents[1]/"frontend/driver/cross-sdk.cjs"
    proc=await asyncio.create_subprocess_exec("node",str(helper),
        stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
    out,error=await proc.communicate(json.dumps(row["invitation"]).encode())
    assert proc.returncode==0,error.decode()
    receipt=json.loads(out)
    row["receipt"]=None
    result=await api.budgets.accept(row,receipt,"driver")
    assert result["state"]=="spending_authorised_wallet_permission_required"


async def test_weekly_receiving_expires_and_does_not_refill_debit_total(tmp_path,monkeypatch):
    from test_ongoing_credit import config
    _,api,proxy,row,_,_,clock=await setup(tmp_path,monkeypatch)
    await api.ongoing_credits.configure(config(row,None),"admin")
    session(proxy,clock,"credit","-0.38")
    # Routes can be assessed without requiring a driver debit budget reservation.
    recipient=api.ongoing_credits.latest()
    route=await api.ongoing_credits.assign(proxy.data["latest_session"],recipient)
    api.ongoing_credits.guard(api.ongoing_credits.wrapper(route))
    assert remaining(api,row)==1000
    clock[0]+=timedelta(days=8)
    with pytest.raises(WalletError,match="expired"):
        api.ongoing_credits.latest()
    with pytest.raises(WalletError):
        api.ongoing_credits.guard(api.ongoing_credits.wrapper(route))
    assert remaining(api,row)==1000 and not api.chain.posts
