"""Public QR tests use fictional keys and never connect to a wallet or chain."""
import copy
from datetime import timedelta

import pytest
from bsv import PrivateKey
from custom_components.bsv_settlement.budget import SPENDING_STATE
from custom_components.bsv_settlement.enrolment import available, token
from custom_components.bsv_settlement.session_review import now
from test_budget import consent, no_network
from test_driver_http import reservation, client_for, access

pytestmark = pytest.mark.asyncio


async def test_public_qr_then_private_handoff_cannot_be_followed_by_qr_observers(tmp_path):
    hass,api,_,_,public=await reservation(tmp_path)
    row=api.saved["session_budgets"][public["terms"]["budget_id"]]
    client,view=await client_for(hass,api)
    try:
        before=copy.deepcopy(api.saved)
        response=await client.post(view.url,json={"action":"public_invitation"})
        result=await response.json()
        assert response.status==200 and result["state"]=="available"
        assert api.saved==before  # Discovery creates no invitation or authority.
        assert access(public)["token"] not in result["public_link_fragment"]
        fields={"join":row["terms"]["budget_id"],"key":token(api,row)}
        response=await client.post(view.url,json=fields|{"action":"public_read"})
        read=await response.json()
        assert set(read)=={"invitation","state","prices","public_enrolment"}
        receipt=consent(row,PrivateKey())
        approved=await client.post(view.url,json=fields|{"action":"public_approve","receipt":receipt})
        private=await approved.json()
        assert approved.status==200 and private["state"]==SPENDING_STATE
        assert access(public)["token"] in private["private_link_fragment"]
        # Public QR is consumed atomically when approval is saved.
        old_read=await client.post(view.url,json=fields|{"action":"public_read"})
        assert old_read.status==400
        listing=await client.post(view.url,json={"action":"public_invitation"})
        assert await listing.json()=={"state":"unavailable"}
        stolen_public=await client.post(view.url,json={
            "budget_id":fields["join"],"token":fields["key"],"action":"read"})
        assert stolen_public.status==400
        # Lost-response recovery needs the exact wallet signature, not the QR.
        retry=await client.post(view.url,json=fields|{"action":"public_approve","receipt":receipt})
        assert retry.status==200 and (await retry.json())["private_link_fragment"]==private["private_link_fragment"]
        other=await client.post(view.url,json=fields|{"action":"public_approve","receipt":consent(row)})
        assert other.status==400
        assert not api.chain.posts and not api.saved["driver_collections"]
    finally:
        await client.close()


@pytest.mark.parametrize("problem",["registered","approved","revoked","expired","closed","existing","no_latest"])
async def test_public_qr_is_hidden_when_not_unassigned_future_consent(tmp_path,problem):
    hass,api,_,_,public=await reservation(tmp_path)
    row=api.saved["session_budgets"][public["terms"]["budget_id"]]
    if problem=="registered":
        earlier=next(r for r in api.saved["session_budgets"].values() if r is not row)
        earlier["credit_destination"]={"address":"fictional"}
    elif problem=="approved":
        await api.budgets.accept(row,consent(row),"driver")
    elif problem=="revoked":
        row["state"]="revoked"
    elif problem=="expired":
        row["terms"]["expires_at"]=(now()-timedelta(seconds=1)).isoformat()
    elif problem=="closed":
        row["terms"]["session_mode"]="existing_session"
        row["terms"]["closed_session_review"]={"account":"fictional"}
    elif problem=="existing":
        row["terms"]["session_mode"]="existing_session"
    else:
        api.saved.pop("latest_session_budget")
    assert available(api) is None
    client,view=await client_for(hass,api)
    try:
        r=await client.post(view.url,json={"action":"public_invitation"})
        assert await r.json()=={"state":"unavailable"}
    finally:
        await client.close()


async def test_bad_signature_and_stale_rates_never_claim_public_invitation(tmp_path):
    hass,api,_,_,public=await reservation(tmp_path)
    row=api.saved["session_budgets"][public["terms"]["budget_id"]]
    client,view=await client_for(hass,api)
    fields={"join":row["terms"]["budget_id"],"key":token(api,row)}
    try:
        bad=consent(row);bad["signature"]="00"*64
        r=await client.post(view.url,json=fields|{"action":"public_approve","receipt":bad})
        assert r.status==400 and row["receipt"] is None
        hass.states.async_set("sensor.demo_import_price","unavailable")
        r=await client.post(view.url,json=fields|{"action":"public_approve","receipt":consent(row)})
        assert r.status==400 and row["receipt"] is None
        assert available(api) is row and not api.chain.posts
    finally:
        await client.close()
