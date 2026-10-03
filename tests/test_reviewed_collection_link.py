"""Lost links can be redisplayed without another mandate or payment attempt."""
import copy
import json
from datetime import timedelta
from urllib.parse import parse_qs
from unittest.mock import AsyncMock

import pytest
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.collection_recovery import execute
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import now
from test_budget import no_network
from test_collection_recovery import reserved, recovery_data

pytestmark = pytest.mark.asyncio


async def reviewed(tmp_path):
    hass, api, proxy, row, driver, _ = await reserved(tmp_path)
    await execute(api.collections, "recover_driver_collection", await recovery_data(api,row), "admin")
    data = {"budget_id": row["terms"]["budget_id"],
            "expected_quote_hash": api.collections.get(row)["quote"]["hash"],
            "confirm_private_link_disclosure": True}
    return hass, api, proxy, row, driver, data


async def test_retrieval_is_read_only_same_link_and_not_a_payment(tmp_path):
    _,api,_,row,_,data = await reviewed(tmp_path)
    before = copy.deepcopy(api.saved)
    api.store.async_save = AsyncMock(side_effect=AssertionError("Retrieval must not write"))
    for _ in range(2):
        result = await execute(api.collections,"get_reviewed_collection_link",data,"admin")
        token = parse_qs(result["driver_link_fragment"][1:])["token"][0]
        assert token == api.budgets.link_token(data["budget_id"])
        assert api.budgets.driver_access(data["budget_id"],token) is row
        assert result["same_link"] and result["requires_driver_confirmation"]
        assert result["payment_sent"] is False
        assert result["expires_at"] == row["terms"]["expires_at"]
        assert token not in json.dumps(api.saved)
        assert token not in json.dumps(api.budgets.public(row))
        assert token not in json.dumps(api.budgets.summary())
        assert token not in json.dumps(api.budgets.driver_view(row))
        assert "driver_link_fragment" not in api.budgets.admin_status(row)
        assert api.saved == before
        assert not api.chain.posts
    api.store.async_save.assert_not_called()


async def test_same_link_is_available_after_reload(tmp_path):
    hass,api,_,row,_,data = await reviewed(tmp_path)
    first = await execute(api.collections,"get_reviewed_collection_link",data,"admin")
    restored = MainnetWalletAPI(hass,api.entry)
    await restored.load()
    second = await execute(restored.collections,"get_reviewed_collection_link",data,"admin")
    assert first == second
    assert not api.chain.posts


@pytest.mark.parametrize("case", [
    "no_admin","stale_quote","no_confirmation","revoked","expired","changed_account",
    "unapproved","legacy","mismatch","wrong_owner","missing_recovery",
    "claimed","held","submitted","confirmed","permit","draft","signed","txid",
])
async def test_refused_retrieval_never_changes_state(tmp_path,case):
    _,api,proxy,row,_,data = await reviewed(tmp_path)
    item = api.collections.get(row)
    if case == "stale_quote": data["expected_quote_hash"]="wrong"
    if case == "no_confirmation": data.pop("confirm_private_link_disclosure")
    if case == "revoked": row["state"]="revoked"
    if case == "expired": row["terms"]["expires_at"]=(now()-timedelta(seconds=1)).isoformat()
    if case == "changed_account": proxy.data["latest_session"]["net_cost_aud_unrounded"]="2.50"
    if case == "unapproved": row["receipt"]=None
    if case == "legacy": row.pop("driver_link_scheme")
    if case == "mismatch": row["driver_token_hash"]="0"*64
    if case == "wrong_owner": api.saved["driver_collection_index"][api.collections.key(row)]="another"
    if case == "missing_recovery": item.pop("recovery")
    if case == "claimed": item["claimed_at"]="recorded"
    if case == "held": item["state"]="wallet_attempt_reserved"
    if case == "submitted": item["state"]="submitted"
    if case == "confirmed": item["state"]="provider_confirmed"
    if case == "permit": item["submission_authorised_at"]="recorded"
    if case == "draft": item["draft_hash"]="recorded"
    if case == "signed": item["signed_raw"]="00"
    if case == "txid": item["txid"]="a"*64
    before = copy.deepcopy(api.saved)
    with pytest.raises(WalletError):
        await execute(api.collections,"get_reviewed_collection_link",data,
                      None if case=="no_admin" else "admin")
    assert api.saved == before
    assert not api.chain.posts


async def test_driver_http_cannot_retrieve_private_link(tmp_path):
    from test_driver_http import client_for
    hass,api,_,row,_,data = await reviewed(tmp_path)
    client,view = await client_for(hass,api)
    before = copy.deepcopy(api.saved)
    try:
        response = await client.post(view.url,json={
            **data,"action":"get_reviewed_collection_link",
            "token":api.budgets.link_token(data["budget_id"]),
        })
        assert response.status == 400
        assert "driver_link_fragment" not in await response.text()
        assert api.saved == before
        assert not api.chain.posts
    finally:
        await client.close()
