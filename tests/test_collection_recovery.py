"""Held claims may be released only before a permit, after explicit review."""
import asyncio
import copy
import json
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.collection_recovery import execute, record_failure
from custom_components.bsv_settlement.collection import transaction_shape
from custom_components.bsv_settlement.session_review import now
from test_budget import no_network
from test_collection import ready, claim_data, payment, authorised

pytestmark = pytest.mark.asyncio


async def reserved(tmp_path):
    hass, api, proxy, row, driver = await ready(tmp_path)
    item = await api.collections.status(row)
    args = claim_data(item, row, driver)
    await api.collections.claim(row, args)
    return hass, api, proxy, row, driver, args


async def recovery_data(api, row):
    plan = await execute(api.collections, "prepare_collection_recovery",
                         {"budget_id": row["terms"]["budget_id"]}, "admin")
    return {
        "budget_id": plan["budget_id"], "expected_quote_hash": plan["quote_hash"],
        "expected_claimed_at": plan["claimed_at"], "evidence_reference": "fictional review 001",
        "confirm_driver_wallet_checked": True, "confirm_recipient_history_checked": True,
        "confirm_unsigned_draft_cancelled_or_absent": True, "confirm_old_driver_pages_closed": True,
    }


async def test_first_failure_survives_repeated_reports_and_restart(tmp_path):
    hass, api, _, row, _, args = await reserved(tmp_path)
    diagnostic = {"event_id":"11111111-2222-4333-8444-555555555555",
                  "stage":"create_draft", "code":"network_request_failed"}
    await record_failure(api.collections, row, args | {"diagnostic": diagnostic})
    first = copy.deepcopy(api.collections.get(row)["diagnostic"])
    await record_failure(api.collections, row, args | {"diagnostic": diagnostic})
    await record_failure(api.collections, row, args | {"diagnostic": diagnostic | {
        "event_id":"22222222-2222-4333-8444-555555555555", "stage":"submit_payment"}})
    assert api.collections.get(row)["diagnostic"] == first
    assert first["verified"] is False
    assert api.collections.get(row)["state"] == "wallet_attempt_reserved"
    from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored_row = restored.saved["session_budgets"][row["terms"]["budget_id"]]
    assert restored.collections.get(restored_row)["diagnostic"] == first
    assert args["attempt_token"] not in json.dumps(api.saved)
    assert not api.chain.posts


@pytest.mark.parametrize("patch", [
    {"stage":"not_a_stage"}, {"code":"seed phrase"}, {"message":"arbitrary secret"},
    {"event_id":"https://private.invalid/token"}, {"stage":""}, {"stage":[]}, {"code":{}},
])
async def test_diagnostic_is_bounded_schema_not_arbitrary_wallet_data(tmp_path, patch):
    _, api, _, row, _, args = await reserved(tmp_path)
    report={"event_id":"11111111-2222-4333-8444-555555555555",
            "stage":"claim_collection","code":"network_request_failed"} | patch
    with pytest.raises(WalletError):
        await record_failure(api.collections,row,args|{"diagnostic":report})
    assert "diagnostic" not in api.collections.get(row)


async def test_review_is_read_only_and_recovery_rotates_quote_without_changing_account(tmp_path):
    _, api, _, row, driver, args = await reserved(tmp_path)
    before=copy.deepcopy(api.saved)
    data=await recovery_data(api,row)
    assert api.saved==before
    with pytest.raises(WalletError,match="administrator"):
        await execute(api.collections,"recover_driver_collection",data,None)
    result=await execute(api.collections,"recover_driver_collection",data,"admin")
    assert result["state"]=="recovery_ready" and result["payment_sent"] is False
    old=json.loads(before["driver_collections"][data["budget_id"]]["quote"]["payload"])
    new=json.loads(result["quote"]["payload"])
    for k in old:
        if k!="created_at":assert new[k]==old[k]
    assert result["quote"]["hash"]!=data["expected_quote_hash"]
    assert len(api.saved["collection_recoveries"])==1
    assert api.saved["collection_recoveries"][0]["independently_verified"] is False
    assert not api.chain.posts
    with pytest.raises(WalletError,match="changed"):
        await execute(api.collections,"recover_driver_collection",data,"admin")
    # The old token can neither request a signing permit nor submit a payment.
    with pytest.raises(WalletError,match="Invalid collection attempt"):
        await api.collections.authorise(row,args|{"draft":transaction_shape(payment(api,driver))})
    with pytest.raises(WalletError,match="explicitly"):
        await api.collections.claim(row,claim_data(result,row,driver))
    # New driver signature over the rotated quote + explicit recovery action.
    with pytest.raises(WalletError,match="retired"):
        await api.collections.claim(row,claim_data(result,row,driver)|{"confirm_recovered_attempt":True})
    old_quote=before["driver_collections"][data["budget_id"]]
    with pytest.raises(WalletError,match="approved driver"):
        await api.collections.claim(row,claim_data(old_quote,row,driver,token="b"*43)|{"confirm_recovered_attempt":True})
    claimed=await api.collections.claim(row,claim_data(result,row,driver,token="b"*43)|{"confirm_recovered_attempt":True})
    assert claimed["claimed"] is True
    assert not api.chain.posts


@pytest.mark.parametrize("field", [
    "confirm_driver_wallet_checked","confirm_recipient_history_checked",
    "confirm_unsigned_draft_cancelled_or_absent","confirm_old_driver_pages_closed",
    "expected_quote_hash","expected_claimed_at","evidence_reference",
])
async def test_missing_or_stale_review_never_releases(tmp_path,field):
    _,api,_,row,_,_=await reserved(tmp_path)
    data=await recovery_data(api,row)
    before=copy.deepcopy(api.saved)
    data.pop(field)
    with pytest.raises(WalletError):
        await execute(api.collections,"recover_driver_collection",data,"admin")
    assert api.saved==before


@pytest.mark.parametrize("field,value", [
    ("state","submission_authorised"),("state","broadcast_unknown"),
    ("submission_authorised_at","recorded"),("draft_hash","hash"),
    ("txid","a"*64),("signed_raw","00"),
])
async def test_any_permit_or_transaction_evidence_blocks_release(tmp_path,field,value):
    _,api,_,row,_,_=await reserved(tmp_path)
    data=await recovery_data(api,row)
    api.collections.get(row)[field]=value
    with pytest.raises(WalletError,match="already reached signing"):
        await execute(api.collections,"recover_driver_collection",data,"admin")
    assert not api.chain.posts


@pytest.mark.parametrize("case",["revoked","expired","changed","save_failure","cancelled_save"])
async def test_existing_guards_and_persistence_failure_do_not_unlock(tmp_path,case):
    _,api,proxy,row,_,_=await reserved(tmp_path)
    data=await recovery_data(api,row)
    if case=="revoked":row["state"]="revoked"
    if case=="expired":row["terms"]["expires_at"]=(now()-timedelta(seconds=1)).isoformat()
    if case=="changed":proxy.data["latest_session"]["net_cost_aud_unrounded"]="2.50"
    if case=="save_failure":api.store.async_save=AsyncMock(side_effect=OSError("Fictional save failure"))
    if case=="cancelled_save":api.store.async_save=AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises((WalletError,OSError,asyncio.CancelledError)):
        await execute(api.collections,"recover_driver_collection",data,"admin")
    assert api.collections.get(row)["state"]=="wallet_attempt_reserved"
    assert not api.saved.get("collection_recoveries")
    assert not api.chain.posts


async def test_http_diagnostic_needs_both_capability_and_attempt_token(tmp_path):
    from test_driver_http import client_for
    hass,api,_,row,_,args=await reserved(tmp_path)
    client,view=await client_for(hass,api)
    body={"budget_id":row["terms"]["budget_id"],
          "token":api.budgets.link_token(row["terms"]["budget_id"]),
          "action":"report_collection_failure",**args,
          "diagnostic":{"event_id":"11111111-2222-4333-8444-555555555555",
                        "stage":"create_draft","code":"network_request_failed"}}
    body.pop("proof")
    try:
        for patch in [{"token":"bad"},{"attempt_token":"bad"},{"action":"recover_driver_collection"}]:
            assert (await client.post(view.url,json=body|patch)).status==400
        assert "diagnostic" not in api.collections.get(row)
        assert (await client.post(view.url,json=body)).status==200
        assert api.collections.get(row)["diagnostic"]["stage"]=="create_draft"
        assert not api.chain.posts
    finally:await client.close()


async def test_failed_diagnostic_save_is_not_later_acknowledged_as_saved(tmp_path):
    _,api,_,row,_,args=await reserved(tmp_path)
    api.store.async_save=AsyncMock(side_effect=OSError("Fictional unavailable storage"))
    report=args|{"diagnostic":{"event_id":"11111111-2222-4333-8444-555555555555",
                              "stage":"create_draft","code":"network_request_failed"}}
    for _ in range(2):
        with pytest.raises(OSError):await record_failure(api.collections,row,report)
        assert "diagnostic" not in api.collections.get(row)


@pytest.mark.parametrize("action",["prepare_collection_recovery","recover_driver_collection",
                                  "get_reviewed_collection_link"])
async def test_recovery_services_enforce_real_ha_admin_context(tmp_path,action):
    from types import SimpleNamespace
    from homeassistant.core import Context
    from homeassistant.exceptions import HomeAssistantError
    from custom_components.bsv_settlement import async_setup
    from custom_components.bsv_settlement.coordinator import SettlementCoordinator
    hass,api,_,row,_,_=await reserved(tmp_path)
    coord=SettlementCoordinator(hass,api.entry,api)
    await async_setup(hass,{})
    hass.data["bsv_settlement"][api.entry.entry_id]=coord
    data=(await recovery_data(api,row) if action=="recover_driver_collection"
          else {"budget_id":row["terms"]["budget_id"]})
    if action == "get_reviewed_collection_link":
        await execute(api.collections,"recover_driver_collection",await recovery_data(api,row),"admin")
        data.update(expected_quote_hash=api.collections.get(row)["quote"]["hash"],
                    confirm_private_link_disclosure=True)
    data["config_entry_id"]=api.entry.entry_id
    before=copy.deepcopy(api.saved)
    try:
        with pytest.raises(HomeAssistantError,match="administrator"):
            await hass.services.async_call("bsv_settlement",action,data,blocking=True)
        hass.auth=SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
        with pytest.raises(HomeAssistantError,match="administrator"):
            await hass.services.async_call("bsv_settlement",action,data,blocking=True,
                                          context=Context(user_id="not-admin"))
        assert api.saved==before
        hass.auth.async_get_user.return_value.is_admin=True
        result=await hass.services.async_call("bsv_settlement",action,data,blocking=True,
                                             return_response=True,context=Context(user_id="admin"))
        assert result["payment_sent"] is False
        assert not api.chain.posts
    finally:
        await hass.async_stop(force=True)


async def test_audit_limit_preserves_existing_records_and_held_attempt(tmp_path):
    _,api,_,row,_,_=await reserved(tmp_path)
    data=await recovery_data(api,row)
    api.saved["collection_recoveries"]=[{"fictional_review":n} for n in range(100)]
    before=copy.deepcopy(api.saved)
    with pytest.raises(WalletError,match="retention limit"):
        await execute(api.collections,"recover_driver_collection",data,"admin")
    assert api.saved==before
