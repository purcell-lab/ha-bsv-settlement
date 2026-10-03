"""New fee ceilings and pending-link replacement never rewrite signed approvals."""
import asyncio
import copy
import json
from unittest.mock import AsyncMock

import pytest
from bsv import PrivateKey
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.budget import sha
from custom_components.bsv_settlement.collection_recovery import record_failure
from custom_components.bsv_settlement.session_review import now
from test_budget import invitation, consent, no_network
from test_collection_recovery import reserved

pytestmark=pytest.mark.asyncio


async def test_defaults_and_fee_headroom_preserve_total(tmp_path):
    _,api,proxy,data,original=await invitation(tmp_path)
    await api.budgets.execute("revoke_session_budget",{"budget_id":original["terms"]["budget_id"]},"admin")
    data.pop("max_fee_sats")
    new=await api.budgets.execute("create_session_budget",data,"admin")
    assert new["terms"]["max_fee_sats"]==new["terms"]["max_total_sats"]==1000
    row=api.saved["session_budgets"][new["terms"]["budget_id"]]
    await api.budgets.accept(row,consent(row,PrivateKey()),"admin")
    proxy.data["latest_session"].update(ended_at=now().isoformat(),net_cost_aud_unrounded="0.89")
    quote=json.loads((await api.collections.status(row))["quote"]["payload"])
    assert quote["amount_sats"]==89 and quote["max_fee_sats"]==911
    assert row["terms"]["max_fee_sats"]==1000
    assert not api.chain.posts


@pytest.mark.parametrize(("aud","expected","fee"),[
    ("9.99","ready",1),("10.00","collection_blocked",None),
    ("10.01","collection_blocked",None),
])
async def test_remaining_budget_bounds_quote(tmp_path,aud,expected,fee):
    _,api,proxy,data,original=await invitation(tmp_path)
    bid=original["terms"]["budget_id"]
    new=await api.budgets.create(data|{
        "max_fee_sats":1000,"replace_pending_budget_id":bid,
        "expected_invitation_hash":sha(original["invitation"]["payload"]),
        "confirm_replace_pending":True},"admin")
    row=api.saved["session_budgets"][new["terms"]["budget_id"]]
    await api.budgets.accept(row,consent(row),"admin")
    proxy.data["latest_session"].update(ended_at=now().isoformat(),net_cost_aud_unrounded=aud)
    result=await api.collections.status(row)
    assert result["state"]==expected
    if fee is not None:
        assert json.loads(result["quote"]["payload"])["max_fee_sats"]==fee
    assert not api.chain.posts


async def test_changed_pending_settings_require_exact_explicit_replacement(tmp_path):
    _,api,_,data,original=await invitation(tmp_path)
    bid=original["terms"]["budget_id"]
    before=copy.deepcopy(api.saved)
    changed=data|{"max_fee_sats":1000,"operator_contact":"new@example.test"}
    with pytest.raises(WalletError,match="Confirm replacement"):
        await api.budgets.create(changed,"admin")
    assert api.saved==before
    replacement=changed|{"replace_pending_budget_id":bid,
        "expected_invitation_hash":sha(original["invitation"]["payload"]),"confirm_replace_pending":True}
    new=await api.budgets.create(replacement,"admin")
    assert new["terms"]["budget_id"]!=bid
    assert new["terms"]["max_fee_sats"]==1000
    assert new["terms"]["operator_contact"]=="new@example.test"
    assert new["driver_link_fragment"]!=original["driver_link_fragment"]
    assert api.saved["session_budgets"][bid]["state"]=="revoked"
    assert new["state"]=="awaiting_driver_consent"
    assert not api.chain.posts


@pytest.mark.parametrize("case",["signed","stale","wrong_id","missing_confirmation","bad_limits","save_failure","cancelled_save"])
async def test_replacement_failure_preserves_old_invitation(tmp_path,case):
    _,api,_,data,original=await invitation(tmp_path)
    row=api.saved["session_budgets"][original["terms"]["budget_id"]]
    data|={"replace_pending_budget_id":row["terms"]["budget_id"],
           "expected_invitation_hash":sha(row["invitation"]["payload"]),"confirm_replace_pending":True,
           "max_fee_sats":1000}
    if case=="signed":await api.budgets.accept(row,consent(row),"admin")
    if case=="stale":data["expected_invitation_hash"]="bad"
    if case=="wrong_id":data["replace_pending_budget_id"]="00000000-1111-4222-8333-444444444444"
    if case=="missing_confirmation":data.pop("confirm_replace_pending")
    if case=="bad_limits":data["max_fee_sats"]=1001
    if case=="save_failure":api.store.async_save=AsyncMock(side_effect=OSError("fixture"))
    if case=="cancelled_save":api.store.async_save=AsyncMock(side_effect=asyncio.CancelledError())
    before=copy.deepcopy(api.saved)
    with pytest.raises((WalletError,OSError,asyncio.CancelledError)):
        await api.budgets.create(data,"admin")
    assert api.saved==before and not api.chain.posts


async def test_specific_diagnostic_is_bounded_persistent_and_not_verified(tmp_path):
    _,api,_,row,_,args=await reserved(tmp_path)
    diagnostic={"event_id":"11111111-2222-4333-8444-555555555555","stage":"inspect_draft",
                "code":"validation_failed","reason":"fee_limit_exceeded",
                "details":{"payment_sats":89,"fee_sats":38,"fee_cap_sats":10,"total_debit_sats":127}}
    await record_failure(api.collections,row,args|{"diagnostic":diagnostic})
    stored=api.collections.get(row)["diagnostic"]
    assert stored["verified"] is False
    assert "Wallet fee: 38 sat." in stored["message"] and "Fee cap: 10 sat." in stored["message"]
    assert api.collections.get(row)["state"]=="wallet_attempt_reserved"
    assert not api.chain.posts


@pytest.mark.parametrize("patch",[
    {"reason":"private error text"},{"details":{"secret":"private"}},{"details":{"fee_sats":-1}},
    {"details":{"fee_sats":True}},{"details":{"fee_sats":2**60}},{"stage":"create_draft"},
])
async def test_unsafe_diagnostic_is_refused(tmp_path,patch):
    _,api,_,row,_,args=await reserved(tmp_path)
    diagnostic={"event_id":"11111111-2222-4333-8444-555555555555","stage":"inspect_draft",
                "code":"validation_failed","reason":"fee_limit_exceeded","details":{"fee_sats":38}}|patch
    with pytest.raises(WalletError):
        await record_failure(api.collections,row,args|{"diagnostic":diagnostic})
    assert "diagnostic" not in api.collections.get(row)
