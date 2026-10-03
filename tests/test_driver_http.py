"""Capability-scoped pre-session consent, live tariff freshness and binding."""
import asyncio
import copy
import json
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.driver_http import DriverBudgetView
from custom_components.bsv_settlement.session_review import now
from test_budget import invitation, consent, no_network  # fixture supplies isolated HA cleanup

pytestmark = pytest.mark.asyncio


def prices(hass):
    attrs={"unit_of_measurement":"$/kWh","start_time":(now()-timedelta(minutes=1)).isoformat(),
           "end_time":(now()+timedelta(minutes=4)).isoformat(),"estimate":False}
    hass.states.async_set("sensor.demo_import_price","0.3625669",attrs)
    hass.states.async_set("sensor.demo_export_price","-0.03",attrs)


async def reservation(tmp_path):
    hass,api,proxy,data,old=await invitation(tmp_path)
    proxy.data["latest_session"]["ended_at"]=now().isoformat()
    data.pop("session_id")
    for k in ("max_total_sats","max_fee_sats","valid_minutes"):
        data.pop(k)
    row=await api.budgets.execute("create_session_budget",data,"admin")
    prices(hass)
    return hass,api,proxy,data,row


async def client_for(hass,api):
    hass.data["bsv_settlement"]["wallet"]=SimpleNamespace(mode="embedded_mainnet",api=api,lock=asyncio.Lock())
    view=DriverBudgetView(hass)
    app=web.Application()
    app.router.add_post(view.url,view.post)
    client=TestClient(TestServer(app))
    await client.start_server()
    return client,view


def access(row):
    q=parse_qs(row["driver_link_fragment"][1:])
    return {"budget_id":q["budget"][0],"token":q["token"][0]}


async def test_pre_session_defaults_token_redaction_and_no_backdating(tmp_path):
    _,api,proxy,data,row=await reservation(tmp_path)
    assert row["terms"]["session_mode"]=="next_session_reservation"
    assert row["terms"]["max_total_sats"]==1000 and row["terms"]["max_fee_sats"]==1000
    assert "driver_token_hash" not in row
    saved=api.saved["session_budgets"][row["terms"]["budget_id"]]
    assert access(row)["token"] not in json.dumps(saved)
    again=await api.budgets.execute("create_session_budget",data,"admin")
    assert again==row|{"invitation_reused":True}
    status=await api.budgets.execute("session_budget_status",{},"admin")
    assert status==row
    await api.budgets.execute("accept_session_budget",
        {"budget_id":row["terms"]["budget_id"],"receipt":consent(row)},"admin")
    with pytest.raises(WalletError,match="after consent"):
        await api.budgets.execute("bind_session_budget",{
            "budget_id":row["terms"]["budget_id"],"session_id":"session-1","confirm_driver_present":True},"admin")
    proxy.data["latest_session"].update(session_id="future-1",opened_at=now().isoformat(),
        ended_at=None,ocpp_transaction_id="future-tx-1")
    bound=await api.budgets.execute("bind_session_budget",{
        "budget_id":row["terms"]["budget_id"],"session_id":"future-1","confirm_driver_present":True},"admin")
    assert bound["binding"]["transaction_id"]=="future-tx-1"
    proxy.data["previous_session"]=copy.deepcopy(proxy.data["latest_session"])
    proxy.data["latest_session"].update(session_id="future-2",ocpp_transaction_id="future-tx-2")
    with pytest.raises(WalletError,match="another session"):
        await api.budgets.execute("bind_session_budget",{
            "budget_id":row["terms"]["budget_id"],"session_id":"future-2","confirm_driver_present":True},"admin")
    assert not api.chain.posts


async def test_private_link_read_approve_replay_and_revoke(tmp_path):
    hass,api,_,_,row=await reservation(tmp_path)
    client,view=await client_for(hass,api)
    try:
        args=access(row)
        r=await client.post(view.url,json=args|{"action":"read"})
        result=await r.json()
        assert r.status==200 and r.headers["Cache-Control"]=="no-store"
        assert result["prices"]["export"]["aud_per_kwh"]=="-0.03"
        assert "token" not in json.dumps(result)
        receipt=consent(row)
        for _ in range(2):
            r=await client.post(view.url,json=args|{"action":"approve","receipt":receipt})
            assert r.status==200
            assert (await r.json())["state"]=="spending_authorised_wallet_permission_required"
        r=await client.post(view.url,json=args|{"action":"approve","receipt":consent(row)})
        assert r.status==400
        await api.budgets.execute("revoke_session_budget",{"budget_id":args["budget_id"]},"admin")
        r=await client.post(view.url,json=args|{"action":"read"})
        assert r.status==400
        assert not api.chain.posts
    finally:
        await client.close()


async def test_http_auth_limits_and_tariff_failure(tmp_path):
    hass,api,_,_,row=await reservation(tmp_path)
    client,view=await client_for(hass,api)
    try:
        args=access(row)
        for bad in (args|{"token":"bad","action":"read"},args|{"action":"broadcast_operator_payment"},[]):
            r=await client.post(view.url,json=bad)
            assert r.status==400
        r=await client.post(view.url,json=args|{"action":"read"},headers={"Sec-Fetch-Site":"cross-site"})
        assert r.status==403
        r=await client.post(view.url,data=b" "*20001,headers={"Content-Type":"application/json"})
        assert r.status==413
        r=await client.post(view.url,data=b"["*2000+b"]"*2000,headers={"Content-Type":"application/json"})
        assert r.status==400
        hass.states.async_set("sensor.demo_import_price","unavailable")
        r=await client.post(view.url,json=args|{"action":"read"})
        assert (await r.json())["prices"]["valid"] is False
        r=await client.post(view.url,json=args|{"action":"approve","receipt":consent(row)})
        assert r.status==400 and "stale" in (await r.json())["error"]
        view.requests.extend([__import__("time").monotonic()]*120)
        r=await client.post(view.url,json=args|{"action":"read"})
        assert r.status==429
        assert not api.chain.posts
    finally:
        await client.close()


async def test_amber_estimates_units_and_stale_times(tmp_path):
    hass,api,_,_,row=await reservation(tmp_path)
    assert api.budgets.prices(row)["valid"]
    attrs=dict(hass.states.get("sensor.demo_import_price").attributes)
    attrs["end_time"]=(now()-timedelta(minutes=10)).isoformat()
    hass.states.async_set("sensor.demo_import_price","0.3",attrs)
    assert not api.budgets.prices(row)["valid"]
    prices(hass)
    attrs=dict(hass.states.get("sensor.demo_import_price").attributes)
    attrs["unit_of_measurement"]="c/kWh"
    hass.states.async_set("sensor.demo_import_price","30",attrs)
    assert not api.budgets.prices(row)["valid"]
