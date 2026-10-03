"""Closed-account resolution never bypasses driver consent or duplicate guards."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.session_review import now
from test_budget import invitation, consent, no_network
from test_collection_recovery import reserved

pytestmark = pytest.mark.asyncio


async def closed(tmp_path, warning=True):
    hass,api,proxy,data,pending = await invitation(tmp_path)
    record=proxy.data["latest_session"]
    record.update(ended_at=now().isoformat(), net_cost_aud_unrounded="0.05")
    if warning:
        record["quality_flags"].append("import:energy_without_matching_state")
    plan=await api.closures.execute("prepare_session_closure",data,"admin")
    args=data|{"expected_review_hash":plan["review_hash"],"reason":"Reviewed charger timing mismatch",
               "confirm_account_review":True,"confirm_provisional_metering":True,
               "confirm_replace_pending":True,"confirm_no_payment":True}
    return hass,api,proxy,args,pending,plan


async def test_preparation_is_read_only_and_waiver_is_audited(tmp_path):
    hass,api,proxy,args,pending,plan=await closed(tmp_path)
    before=copy.deepcopy(api.saved)
    assert await api.closures.execute("prepare_session_closure",args,"admin")==plan
    assert api.saved==before
    result=await api.closures.execute("waive_session_charge",args,"admin")
    assert result["state"]=="waived" and result["net_amount_aud"]=="0.05"
    assert result["quality_flags"]==["import:energy_without_matching_state"]
    assert api.saved["session_budgets"][pending["terms"]["budget_id"]]["state"]=="revoked"
    with pytest.raises(WalletError):
        await api.budgets.accept(api.saved["session_budgets"][pending["terms"]["budget_id"]],consent(pending),"admin")
    with pytest.raises(WalletError,match="closed"):
        await api.reviews.prepare(args,"admin")
    assert not api.chain.posts
    from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
    restored=MainnetWalletAPI(hass,api.entry)
    await restored.load()
    assert restored.closures.summary()[0]["state"]=="waived"
    with pytest.raises(WalletError,match="already closed"):
        await restored.closures.execute("waive_session_charge",args,"admin")


async def test_post_session_consent_preserves_warning_and_requires_fresh_driver_signature(tmp_path):
    _,api,proxy,args,pending,_=await closed(tmp_path)
    new=await api.closures.execute("request_closed_session_consent",args,"admin")
    assert new["terms"]["budget_id"]!=pending["terms"]["budget_id"]
    row=api.saved["session_budgets"][new["terms"]["budget_id"]]
    assert row["terms"]["closed_session_review"]["account"]["net_amount_aud"]=="0.05"
    assert "import:energy_without_matching_state" in row["terms"]["account_scope"]
    assert row["receipt"] is None and "driver_link_fragment" in new
    assert (await api.collections.status(row))["state"]=="collection_blocked"
    with pytest.raises(WalletError):
        await api.budgets.accept(row,consent(pending),"admin")
    await api.budgets.accept(row,consent(row),"admin")
    quote=json.loads((await api.collections.status(row))["quote"]["payload"])
    assert quote["amount_sats"]==5
    assert quote["account"]==row["terms"]["closed_session_review"]["account"]
    assert not api.budgets.driver_view(row)["automatic_credit_enabled"]
    assert "credit_receiving" not in row["terms"]
    with pytest.raises(WalletError,match="Post-session"):
        api.auto_credits.guard(row)
    assert not api.chain.posts


@pytest.mark.parametrize("field",[
    "expected_review_hash","confirm_account_review","confirm_provisional_metering","reason","confirm_no_payment"])
async def test_missing_review_never_waives(tmp_path,field):
    _,api,_,args,_,_=await closed(tmp_path)
    before=copy.deepcopy(api.saved)
    args.pop(field)
    with pytest.raises(WalletError):
        await api.closures.execute("waive_session_charge",args,"admin")
    assert api.saved==before


@pytest.mark.parametrize("case",["source","rate","approved","pending_revoked","anonymous","save","cancel"])
async def test_stale_or_failed_waiver_preserves_state(tmp_path,case):
    _,api,proxy,args,pending,_=await closed(tmp_path)
    if case=="source":proxy.data["latest_session"]["import_kwh"]+=1
    if case=="rate":api.hass.states.async_set("sensor.demo_rate","200",{"unit_of_measurement":"sat/AUD"})
    if case=="approved":await api.budgets.accept(api.saved["session_budgets"][pending["terms"]["budget_id"]],consent(pending),"admin")
    if case=="pending_revoked":api.saved["session_budgets"][pending["terms"]["budget_id"]]["state"]="revoked"
    if case=="save":api.store.async_save=AsyncMock(side_effect=OSError("fixture"))
    if case=="cancel":api.store.async_save=AsyncMock(side_effect=asyncio.CancelledError())
    before=copy.deepcopy(api.saved)
    with pytest.raises((WalletError,OSError,asyncio.CancelledError)):
        await api.closures.execute("waive_session_charge",args,None if case=="anonymous" else "admin")
    assert api.saved==before


@pytest.mark.parametrize("flag",["history_starts_mid_session","import:missing_counter_baseline","unknown_running_state"])
async def test_hard_quality_flags_cannot_be_accepted(tmp_path,flag):
    _,api,proxy,args,_,_=await closed(tmp_path)
    proxy.data["latest_session"]["quality_flags"].append(flag)
    with pytest.raises(WalletError):
        await api.closures.execute("prepare_session_closure",args,"admin")


async def test_driver_credit_cannot_be_waived_and_zero_account_closes(tmp_path):
    _,api,proxy,args,_,_=await closed(tmp_path,False)
    proxy.data["latest_session"]["net_cost_aud_unrounded"]="-0.50"
    with pytest.raises(WalletError,match="credit owed"):
        await api.closures.execute("prepare_session_closure",args,"admin")
    proxy.data["latest_session"]["net_cost_aud_unrounded"]="0"
    plan=await api.closures.execute("prepare_session_closure",args,"admin")
    args["expected_review_hash"]=plan["review_hash"]
    assert (await api.closures.execute("waive_session_charge",args,"admin"))["state"]=="closed_zero"


async def test_reserved_attempt_is_not_replaceable_or_waivable(tmp_path):
    _,api,_,row,_,_=await reserved(tmp_path)
    args={"proxy_config_entry_id":row["proxy_config_entry_id"],"session_id":row["terms"]["session_id"],
          "conversion_rate_entity":"sensor.demo_rate"}
    before=copy.deepcopy(api.saved)
    with pytest.raises(WalletError,match="settlement already exists"):
        await api.closures.execute("prepare_session_closure",args,"admin")
    assert api.saved==before


async def test_new_closed_account_change_blocks_collection(tmp_path):
    _,api,proxy,args,_,_=await closed(tmp_path)
    new=await api.closures.execute("request_closed_session_consent",args,"admin")
    row=api.saved["session_budgets"][new["terms"]["budget_id"]]
    await api.budgets.accept(row,consent(row),"admin")
    proxy.data["latest_session"]["net_cost_aud_unrounded"]="0.10"
    result=await api.collections.status(row)
    assert result["state"]=="collection_blocked"
    assert "changed" in result["error"]


@pytest.mark.parametrize("field", ["confirm_account_review", "confirm_provisional_metering",
                                  "confirm_replace_pending", "expected_review_hash"])
async def test_consent_request_needs_explicit_review_and_replacement(tmp_path,field):
    _,api,_,args,_,_=await closed(tmp_path)
    before=copy.deepcopy(api.saved)
    args.pop(field)
    with pytest.raises(WalletError):
        await api.closures.execute("request_closed_session_consent",args,"admin")
    assert api.saved==before


@pytest.mark.parametrize("case", ["driver_collection_index","automatic_credit_index","manual","credit"])
async def test_other_settlement_ownership_blocks_resolution(tmp_path,case):
    _,api,_,args,_,_=await closed(tmp_path)
    key=args["proxy_config_entry_id"]+"|"+args["session_id"]
    if case.endswith("_index"):
        api.saved.setdefault(case,{})[key]="existing-record"
    elif case=="manual":
        api.saved["session_review_index"][key]="review"
        api.saved["session_reviews"]["review"]={"state":"driver_payment_requested"}
    else:
        api.saved["automatic_credits"]["credit"]={"session_id":args["session_id"]}
    before=copy.deepcopy(api.saved)
    with pytest.raises(WalletError):
        await api.closures.execute("prepare_session_closure",args,"admin")
    assert api.saved==before


async def test_failed_invitation_save_restores_pending_and_no_fee_headroom_rejected(tmp_path):
    _,api,_,args,pending,_=await closed(tmp_path)
    before=copy.deepcopy(api.saved)
    with pytest.raises(WalletError,match="headroom"):
        await api.closures.execute("request_closed_session_consent",args|{"max_total_sats":5},"admin")
    api.store.async_save=AsyncMock(side_effect=OSError("fixture"))
    with pytest.raises(OSError):
        await api.closures.execute("request_closed_session_consent",args,"admin")
    assert api.saved==before


async def test_service_schema_requires_session_and_authenticated_admin(tmp_path):
    from types import SimpleNamespace
    from homeassistant.core import Context
    from homeassistant.exceptions import HomeAssistantError
    from custom_components.bsv_settlement import async_setup
    hass,api,_,args,_,_=await closed(tmp_path)
    await async_setup(hass,{})
    schemas=hass.services.async_services()["bsv_settlement"]
    request=schemas["request_closed_session_consent"].schema
    data={k:v for k,v in args.items() if k!="confirm_no_payment"}|{"config_entry_id":api.entry.entry_id}
    data.pop("max_fee_sats",None)
    validated=request(data)
    assert validated["max_fee_sats"]==1000
    assert validated["session_id"]==args["session_id"]
    with pytest.raises(Exception):
        request({k:v for k,v in data.items() if k!="session_id"})
    hass.auth=SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
    with pytest.raises(HomeAssistantError,match="administrator"):
        await hass.services.async_call("bsv_settlement","request_closed_session_consent",data,
            blocking=True,return_response=True,context=Context(user_id="nonadmin"))


@pytest.mark.parametrize("field,value",[
    ("unpriced_import_wh",1),("unpriced_export_wh",None),
    ("estimated_rate_import_wh",1),("estimated_rate_export_wh",1),
    ("net_cost_aud_unrounded",None),("net_cost_aud_unrounded","NaN"),
    ("import_kwh",None),("export_kwh",-1)])
async def test_incomplete_or_invalid_pricing_and_energy_cannot_be_accepted(tmp_path,field,value):
    _,api,proxy,args,_,_=await closed(tmp_path)
    proxy.data["latest_session"][field]=value
    with pytest.raises(WalletError):
        await api.closures.execute("prepare_session_closure",args,"admin")
