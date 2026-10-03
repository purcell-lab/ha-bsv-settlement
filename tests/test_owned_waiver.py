"""Owned-charge waiver tests: no wallet broadcast and no loss of receipt evidence."""
import asyncio
import copy
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from bsv import P2PKH, Transaction, TransactionInput, TransactionOutput
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.owned_waiver import execute
from custom_components.bsv_settlement.session_review import now
from test_budget import no_network, OPEN_HASS
from test_collection_recovery import reserved
from test_collection import claim_data, payment
from test_session_review import source, session, prepare_data, review_approval
from test_mainnet import setup_wallet

pytestmark = pytest.mark.asyncio


async def manual(tmp_path):
    hass,entry,api=await setup_wallet(tmp_path)
    OPEN_HASS.append(hass)
    proxy=source(hass,session("0.76"))
    review=await api.reviews.prepare(prepare_data(),"admin")
    await api.reviews.approve(review_approval(review),"admin")
    rid=review["review_id"]
    api.saved["session_reviews"][rid]["expires_at"]=(now()-timedelta(minutes=1)).isoformat()
    return hass,api,proxy,{"review_id":rid}


async def confirmed_receipt(api,amount=76,address=None):
    tx=Transaction([TransactionInput(source_txid="11"*32)],
                   [TransactionOutput(P2PKH().lock(address or api.identity["address"]),satoshis=amount)])
    api.chain.request=AsyncMock(return_value=tx.hex())
    api.chain.details=AsyncMock(return_value={"txid":tx.txid(),"confirmations":10,"time":now().timestamp()})
    return {"received_txid":tx.txid(),"received_output_index":0}


async def args(api,data):
    p=await execute(api,"prepare_existing_charge_waiver",data,"admin")
    return data|{"expected_review_hash":p["review_hash"],"expected_amount_sats":p["amount_sats"],
                "reason":"Operator instructed terminal charge waiver; no refund",
                "confirm_waive_charge":True,"confirm_no_refund":True,
                "confirm_external_payments_need_separate_accounting":True,
                "confirm_received_funds_unallocated":True}


async def test_received_payment_is_preserved_unallocated_not_paid_or_refunded(tmp_path):
    hass,api,proxy,data=await manual(tmp_path)
    data|=await confirmed_receipt(api)
    before=copy.deepcopy(api.saved)
    a=await args(api,data)
    assert api.saved==before
    r=await execute(api,"waive_existing_charge",a,"admin")
    assert r["state"]=="waived" and r["received_funds"]["amount_sats"]==76
    point=data["received_txid"]+":0"
    assert api.saved["received_outpoints"][point].startswith("waiver_receipt:")
    assert api.saved["unallocated_receipts"][point]["state"]=="received_unallocated"
    assert not api.saved["unallocated_receipts"][point]["refund_authorised"]
    review=api.reviews.get(data["review_id"])
    assert review["state"]=="waived" and review["receipt"] is None
    assert review["payment_request"]==before["session_reviews"][data["review_id"]]["payment_request"]
    assert api.closures.summary()[0]["received_funds"]["outpoint"]==point
    assert not api.chain.posts
    with pytest.raises(WalletError,match="closed"):
        await api.reviews.verify_driver_payment({"review_id":data["review_id"]},"admin")
    with pytest.raises(WalletError):
        await execute(api,"waive_existing_charge",a,"admin")
    from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
    restored=MainnetWalletAPI(hass,api.entry);await restored.load()
    assert restored.saved["unallocated_receipts"][point]["state"]=="received_unallocated"
    assert restored.reviews.get(data["review_id"])["state"]=="waived"


async def test_held_claim_terminal_not_released_and_old_wallet_requests_blocked(tmp_path):
    _,api,proxy,row,driver,claim=await reserved(tmp_path)
    old=copy.deepcopy(api.collections.get(row))
    a=await args(api,{"budget_id":row["terms"]["budget_id"]})
    r=await execute(api,"waive_existing_charge",a,"admin")
    assert r["state"]=="waived" and r["external_wallet_outcome_not_proven"]
    saved=api.collections.get(row)
    for k,v in old.items():
        if k!="state":assert saved[k]==v
    assert saved["state"]=="waived" and row["state"]=="charge_waived"
    assert (await api.collections.status(row))["state"]=="waived"
    with pytest.raises(WalletError):
        await api.collections.claim(row,claim_data(old,row,driver))
    with pytest.raises(WalletError):
        await api.collections.authorise(row,claim|{"draft":{}})
    with pytest.raises(WalletError):
        await api.collections.report(row,claim|{"raw_tx":payment(api,driver).hex()})
    from custom_components.bsv_settlement.collection_recovery import execute as recover
    assert not (await recover(api.collections,"prepare_collection_recovery",
                             {"budget_id":row["terms"]["budget_id"]},"admin"))["eligible_for_review"]
    assert not api.chain.posts


@pytest.mark.parametrize("field",["submission_authorised_at","draft_hash","signed_raw","txid"])
async def test_any_signing_evidence_blocks_waiver(tmp_path,field):
    _,api,_,row,_,_=await reserved(tmp_path)
    api.collections.get(row)[field]="evidence"
    before=copy.deepcopy(api.saved)
    with pytest.raises(WalletError,match="cannot be waived"):
        await execute(api,"prepare_existing_charge_waiver",{"budget_id":row["terms"]["budget_id"]},"admin")
    assert api.saved==before


@pytest.mark.parametrize("field",["expected_review_hash","expected_amount_sats","reason","confirm_waive_charge",
    "confirm_no_refund","confirm_external_payments_need_separate_accounting","confirm_received_funds_unallocated"])
async def test_explicit_authority_and_receipt_acknowledgement_required(tmp_path,field):
    _,api,_,data=await manual(tmp_path)
    data|=await confirmed_receipt(api); a=await args(api,data);a.pop(field)
    before=copy.deepcopy(api.saved)
    with pytest.raises(WalletError):
        await execute(api,"waive_existing_charge",a,"admin")
    assert api.saved==before


@pytest.mark.parametrize("case",["stale","amount","save","cancel","anonymous","data","other_owner"])
async def test_waiver_is_compare_and_set_and_rolls_back(tmp_path,case):
    _,api,proxy,row,_,_=await reserved(tmp_path)
    a=await args(api,{"budget_id":row["terms"]["budget_id"]})
    if case=="stale":api.collections.get(row)["claimed_at"]=now().isoformat()
    if case=="amount":a["expected_amount_sats"]+=1
    if case=="save":api.store.async_save=AsyncMock(side_effect=OSError("fixture"))
    if case=="cancel":api.store.async_save=AsyncMock(side_effect=asyncio.CancelledError())
    if case=="data":proxy.data["latest_session"]["import_kwh"]+=1
    if case=="other_owner":api.saved["automatic_credit_index"][api.collections.key(row)]="other"
    before=copy.deepcopy(api.saved)
    with pytest.raises((WalletError,OSError,asyncio.CancelledError)):
        await execute(api,"waive_existing_charge",a,None if case=="anonymous" else "admin")
    assert api.saved==before
    assert not api.chain.posts


@pytest.mark.parametrize("case",["wrong_amount","wrong_index","unconfirmed","allocated","old","unavailable"])
async def test_bad_or_preallocated_receipt_blocks_without_changes(tmp_path,case):
    _,api,_,data=await manual(tmp_path)
    data|=await confirmed_receipt(api,77 if case=="wrong_amount" else 76)
    if case=="wrong_index":data["received_output_index"]=1
    if case=="unconfirmed":api.chain.details.return_value["confirmations"]=0
    if case=="allocated":api.saved["received_outpoints"][data["received_txid"]+":0"]="other"
    if case=="old":api.chain.details.return_value["time"]=1
    if case=="unavailable":api.chain.request.side_effect=WalletError("Provider unavailable")
    before=copy.deepcopy(api.saved)
    with pytest.raises(WalletError):
        await execute(api,"prepare_existing_charge_waiver",data,"admin")
    assert api.saved==before


async def test_waiver_keeps_receiving_registration_for_other_sessions(tmp_path):
    from test_auto_credit import ready
    _,api,_,row,_,_=await ready(tmp_path,"1.89")
    await api.collections.status(row)
    registration=api.ongoing_credits.verified_registration(row)
    a=await args(api,{"budget_id":row["terms"]["budget_id"]})
    await execute(api,"waive_existing_charge",a,"admin")
    assert api.ongoing_credits.verified_registration(row)==registration
    assert not api.chain.posts


@pytest.mark.parametrize("case", ["save", "cancel"])
async def test_receipt_and_outpoint_roll_back_together(tmp_path, case):
    _,api,_,data=await manual(tmp_path)
    data|=await confirmed_receipt(api)
    a=await args(api,data)
    before=copy.deepcopy(api.saved)
    api.store.async_save=AsyncMock(side_effect=(
        OSError("fixture") if case=="save" else asyncio.CancelledError()))
    with pytest.raises((OSError,asyncio.CancelledError)):
        await execute(api,"waive_existing_charge",a,"admin")
    assert api.saved==before
    assert not api.chain.posts


@pytest.mark.parametrize("case", ["both", "neither", "negative", "paid", "credit", "wrong_operator"])
async def test_ambiguous_or_noncharge_records_refused(tmp_path, case):
    _,api,_,data=await manual(tmp_path)
    target=api.reviews.get(data["review_id"])
    if case=="both":data["budget_id"]="another"
    if case=="neither":data={}
    if case=="negative":target["account"]["net_amount_aud"]="-0.76"
    if case=="paid":target["receipt"]={"txid":"11"*32}
    if case=="credit":target["direction"]="operator_to_driver"
    if case=="wrong_operator":target["recipient_address"]="different"
    before=copy.deepcopy(api.saved)
    with pytest.raises(WalletError):
        await execute(api,"prepare_existing_charge_waiver",data,"admin")
    assert api.saved==before


async def test_terminal_driver_endpoint_reads_but_refuses_wallet_mutations(tmp_path):
    from test_driver_http import client_for
    hass,api,_,row,_,_=await reserved(tmp_path)
    bid=row["terms"]["budget_id"]
    token=api.budgets.link_token(bid)
    a=await args(api,{"budget_id":bid})
    await execute(api,"waive_existing_charge",a,"admin")
    client,view=await client_for(hass,api)
    try:
        for action in ("read","collection_status"):
            response=await client.post(view.url,json={
                "action":action,"budget_id":bid,"token":token})
            body=await response.json()
            assert response.status==200
            if action=="read":
                assert body["state"]=="charge_waived"
                assert body["closure"]["state"]=="waived"
                assert not body["automatic_credit_enabled"]
            else:
                assert body["state"]=="waived"
        for action in ("approve","claim_collection","authorise_collection",
                       "register_credit_destination","pairing_create","report_collection"):
            response=await client.post(view.url,json={
                "action":action,"budget_id":bid,"token":token})
            assert response.status==400
        assert not api.chain.posts
    finally:
        await client.close()


@pytest.mark.parametrize("action",["prepare_existing_charge_waiver","waive_existing_charge"])
async def test_services_require_real_admin_context(tmp_path,action):
    from types import SimpleNamespace
    from homeassistant.core import Context
    from homeassistant.exceptions import HomeAssistantError
    from custom_components.bsv_settlement import async_setup
    from custom_components.bsv_settlement.coordinator import SettlementCoordinator
    hass,api,_,row,_,_=await reserved(tmp_path)
    coord=SettlementCoordinator(hass,api.entry,api)
    await async_setup(hass,{})
    hass.data["bsv_settlement"][api.entry.entry_id]=coord
    data={"budget_id":row["terms"]["budget_id"]}
    if action=="waive_existing_charge":data=await args(api,data)
    data["config_entry_id"]=api.entry.entry_id
    before=copy.deepcopy(api.saved)
    with pytest.raises(HomeAssistantError,match="administrator"):
        await hass.services.async_call("bsv_settlement",action,data,blocking=True)
    hass.auth=SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
    with pytest.raises(HomeAssistantError,match="administrator"):
        await hass.services.async_call("bsv_settlement",action,data,blocking=True,context=Context(user_id="non-admin"))
    assert api.saved==before
    hass.auth.async_get_user.return_value.is_admin=True
    result=await hass.services.async_call("bsv_settlement",action,data,blocking=True,
        return_response=True,context=Context(user_id="admin"))
    assert result.get("state",result.get("prior_state")) in ("waived","wallet_attempt_reserved")
    assert not api.chain.posts


async def test_split_checkpoint_stays_blocked_after_in_memory_rollback(tmp_path):
    _,api,_,data=await manual(tmp_path)
    data|=await confirmed_receipt(api)
    a=await args(api,data)
    before=copy.deepcopy(api.saved)
    api.store.ledger.async_save=AsyncMock(side_effect=OSError("fixture"))
    with pytest.raises(WalletError,match="checkpoint"):
        await execute(api,"waive_existing_charge",a,"admin")
    assert api.saved==before and api.store.blocked
    with pytest.raises(WalletError,match="checkpoint"):
        await api.store.async_save(api.saved)
    assert not api.chain.posts
