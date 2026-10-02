"""Capabilities are redisplayed only through the administrator status service."""
import json
from datetime import timedelta
from urllib.parse import parse_qs

import pytest

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import now
from test_budget import invitation, consent, no_network

pytestmark = pytest.mark.asyncio


async def test_pending_link_survives_restart_without_plaintext_storage_or_public_leak(tmp_path):
    hass, api, _, _, public = await invitation(tmp_path)
    budget_id = public["terms"]["budget_id"]
    fragment = public["driver_link_fragment"]
    token = parse_qs(fragment[1:])["token"][0]
    assert len(token) == 43
    assert token not in json.dumps(api.saved)
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    status = await restored.budgets.execute("session_budget_status", {"budget_id":budget_id}, "admin")
    assert status["driver_link_fragment"] == fragment
    row = restored.budgets.driver_access(budget_id,token)
    assert token not in json.dumps(restored.budgets.public(row))
    assert token not in json.dumps(restored.budgets.summary())
    assert token not in json.dumps(restored.budgets.driver_view(row))
    assert "driver_link_scheme" not in restored.budgets.public(row)
    with pytest.raises(WalletError,match="administrator"):
        await restored.budgets.execute("session_budget_status",{"budget_id":budget_id},None)
    assert not api.chain.posts


@pytest.mark.parametrize("case",["approved","expired","revoked","legacy","mismatch"])
async def test_no_redisplay_or_silent_rotation_for_terminal_legacy_or_invalid_link(tmp_path,case):
    _, api, _, _, public = await invitation(tmp_path)
    row=api.saved["session_budgets"][public["terms"]["budget_id"]]
    original=row["driver_token_hash"]
    if case=="approved":
        await api.budgets.accept(row,consent(row),"driver")
    elif case=="expired":
        row["terms"]["expires_at"]=(now()-timedelta(seconds=1)).isoformat()
    elif case=="revoked":
        row["state"]="revoked"
    elif case=="legacy":
        row.pop("driver_link_scheme")
    else:
        row["driver_token_hash"]="0"*64
    before=row["driver_token_hash"]
    result=await api.budgets.execute("session_budget_status",{"budget_id":row["terms"]["budget_id"]},"admin")
    assert "driver_link_fragment" not in result
    assert row["driver_token_hash"]==before
    if case!="mismatch":
        assert api.budgets.driver_access(row["terms"]["budget_id"],
            parse_qs(public["driver_link_fragment"][1:])["token"][0],allow_terminal=True) is row
    assert not api.chain.posts
