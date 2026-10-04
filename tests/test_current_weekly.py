"""Fresh current-plus-future authority; fictional wallets and no real network."""
import copy
from datetime import timedelta

import pytest
from bsv import PrivateKey
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.weekly import ticket, summary, candidates, guard_child
from test_budget import invitation, consent, no_network
from test_auto_credit import proof
from test_weekly_mandate import setup, session

pytestmark = pytest.mark.asyncio


async def current_setup(tmp_path, closed=True):
    _, api, proxy, data, original = await invitation(tmp_path)
    await api.budgets.execute("revoke_session_budget",
        {"budget_id": original["terms"]["budget_id"]}, "admin")
    await api.auto_credits.configure(True, "admin")
    proxy.data["latest_session"]["net_cost_aud"] = 1.89
    from custom_components.bsv_settlement.session_review import now
    if closed:
        proxy.data["latest_session"]["ended_at"] = (now()-timedelta(seconds=1)).isoformat()
    args = {k:v for k,v in data.items() if k not in ("session_id", "valid_minutes")}
    args.update(multi_session=True, initial_session_id="session-1")
    return api, proxy, args


async def sign(api, row):
    driver = PrivateKey()
    await api.budgets.accept(row, consent(row, driver), "driver")
    await api.auto_credits.register(row, proof(api, row, driver))


async def test_completed_current_and_future_share_fresh_aggregate(tmp_path):
    api, proxy, args = await current_setup(tmp_path)
    result = await api.budgets.create(args, "admin")
    row = api.saved["session_budgets"][result["terms"]["budget_id"]]
    initial = row["terms"]["included_session"]
    assert initial["amount_sats"] == 189
    assert row["terms"]["payment_authority"]["included_session_id"] == "session-1"
    assert row["receipt"] is None
    from custom_components.bsv_settlement.enrolment import available
    assert available(api) is None  # Account-bearing invitation must stay private.
    assert not api.chain.posts
    await sign(api,row)
    assert [r["session_id"] for r in await candidates(api,row)] == ["session-1"]
    child = await ticket(api,row,"session-1")
    assert (await api.collections.status(child))["state"] == "ready"
    assert summary(api,row)["remaining_sats"] == 801  # 189 charge + 10 fee reservation.
    guard_child(api,child)
    assert await ticket(api,row,"session-1") is child
    from custom_components.bsv_settlement.session_review import now
    original=copy.deepcopy(proxy.data["latest_session"])
    proxy.archive.append(original)
    future=copy.deepcopy(original)
    future.update(session_id="future-1",ocpp_transaction_id="future-tx",
                  opened_at=now().isoformat(),net_cost_aud=0.10,net_cost_aud_unrounded="0.10")
    future["ended_at"]=now().isoformat()
    proxy.data["latest_session"]=future
    second=await ticket(api,row,"future-1")
    assert (await api.collections.status(second))["state"]=="ready"
    assert summary(api,row)["remaining_sats"]==781
    from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
    restored=MainnetWalletAPI(api.hass,api.entry)
    await restored.load()
    root=restored.saved["session_budgets"][row["terms"]["budget_id"]]
    assert root["terms"]["included_session"]==initial
    assert summary(restored,root)["remaining_sats"]==781
    assert not api.chain.posts


async def test_open_current_included_without_backdating_other_accounts(tmp_path):
    api, proxy, args = await current_setup(tmp_path,closed=False)
    result = await api.budgets.create(args,"admin")
    row=api.saved["session_budgets"][result["terms"]["budget_id"]]
    assert row["terms"]["included_session"]["account"] is None
    await sign(api,row)
    with pytest.raises(WalletError):
        await ticket(api,row,"session-1")
    from custom_components.bsv_settlement.session_review import now
    proxy.data["latest_session"]["ended_at"]=now().isoformat()
    child=await ticket(api,row,"session-1")
    assert (await api.collections.status(child))["state"]=="ready"
    assert not api.chain.posts


@pytest.mark.parametrize("problem",["wrong_id","manual","collection","credit","waived","quality","cap","not_multi"])
async def test_current_inclusion_fails_closed(tmp_path,problem):
    api, proxy, args=await current_setup(tmp_path)
    key="proxy-entry|session-1"
    if problem=="wrong_id":args["initial_session_id"]="different"
    elif problem=="not_multi":args["multi_session"]=False
    elif problem=="quality":proxy.data["latest_session"]["quality_flags"]=["import:missing_counter_baseline"]
    elif problem=="cap":args["max_total_sats"]=189
    else:
        index={"manual":"session_review_index","collection":"driver_collection_index",
               "credit":"automatic_credit_index","waived":"closed_sessions"}[problem]
        api.saved.setdefault(index,{})[key]="other-owner"
    with pytest.raises(WalletError):
        await api.budgets.create(args,"admin")
    assert not api.chain.posts


async def test_frozen_closed_account_change_rejected_before_and_after_quote(tmp_path):
    api, proxy, args=await current_setup(tmp_path)
    result=await api.budgets.create(args,"admin")
    row=api.saved["session_budgets"][result["terms"]["budget_id"]]
    await sign(api,row)
    original=copy.deepcopy(proxy.data["latest_session"])
    proxy.data["latest_session"]["import_kwh"]+=1
    with pytest.raises(WalletError,match="account changed"):
        await ticket(api,row,"session-1")
    proxy.data["latest_session"]=original
    child=await ticket(api,row,"session-1")
    await api.collections.status(child)
    child["terms"]["closed_session_review"]["amount_sats"]+=1
    with pytest.raises(WalletError,match="account changed"):
        guard_child(api,child)
    assert not api.chain.posts


async def test_superseded_weekly_can_renew_without_changing_any_old_record(tmp_path,monkeypatch):
    _,api,_,parent,driver,args,clock=await setup(tmp_path,monkeypatch)
    with pytest.raises(WalletError,match="signed approval"):
        await api.budgets.create(args,"admin")
    clock[0]+=timedelta(seconds=1)
    newer=await api.budgets.create(args|{"multi_session":False},"admin")
    row=api.saved["session_budgets"][newer["terms"]["budget_id"]]
    await api.budgets.accept(row,consent(row,driver),"driver")
    await api.auto_credits.register(row,proof(api,row,driver))
    before=copy.deepcopy(api.saved["session_budgets"])
    fresh=await api.budgets.create(args,"admin")
    assert fresh["terms"]["budget_id"] not in before
    assert fresh["state"]=="awaiting_driver_consent"
    assert all(api.saved["session_budgets"][key]==value for key,value in before.items())
    assert summary(api,parent)["active"] is False
    assert not api.chain.posts


async def test_explicit_previous_completed_account_survives_session_rollover(tmp_path):
    api,proxy,args=await current_setup(tmp_path)
    previous=copy.deepcopy(proxy.data["latest_session"])
    current=copy.deepcopy(previous)
    current.update(session_id="new-current",ocpp_transaction_id="new-current-tx",ended_at=None)
    proxy.data.update(latest_session=current,previous_session=previous)
    result=await api.budgets.create(args,"admin")
    row=api.saved["session_budgets"][result["terms"]["budget_id"]]
    assert row["terms"]["included_session"]["session_id"]=="session-1"
    assert row["terms"]["included_session"]["amount_sats"]==189
    await sign(api,row)
    child=await ticket(api,row,"session-1")
    assert (await api.collections.status(child))["state"]=="ready"
    assert not any(r.get("weekly_parent_id") and r["terms"]["session_id"]=="new-current"
                   for r in api.saved["session_budgets"].values())
    assert not api.chain.posts


@pytest.mark.parametrize("problem",["archived","previous_open","previous_paid","previous_missing_baseline"])
async def test_rollover_does_not_admit_arbitrary_or_owned_history(tmp_path,problem):
    api,proxy,args=await current_setup(tmp_path)
    previous=copy.deepcopy(proxy.data["latest_session"])
    current=copy.deepcopy(previous)
    current.update(session_id="new-current",ocpp_transaction_id="new-current-tx",ended_at=None)
    proxy.data.update(latest_session=current,previous_session=previous)
    if problem=="archived":
        proxy.archive.append(previous)
        proxy.data["previous_session"]=None
    elif problem=="previous_open":previous["ended_at"]=None
    elif problem=="previous_paid":api.saved.setdefault("session_review_index",{})["proxy-entry|session-1"]="paid"
    else:previous["quality_flags"]=["import:missing_counter_baseline"]
    with pytest.raises(WalletError):
        await api.budgets.create(args,"admin")
    assert not api.chain.posts
