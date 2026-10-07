"""Portal transport reuses the signed, per-session collection state machine."""
import copy
from datetime import timedelta

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.portal_debits import jobs, handle
from custom_components.bsv_settlement.collection import transaction_shape
from custom_components.bsv_settlement.weekly import remaining
from test_weekly_mandate import setup, session
from test_collection import claim_data, payment
from test_portal import fixture, login, post
from test_budget import no_network

pytestmark = pytest.mark.asyncio


async def test_one_login_can_discover_and_collect_two_sessions_with_existing_limits(tmp_path, monkeypatch):
    _, api, proxy, root, driver, _, clock = await setup(tmp_path, monkeypatch)
    identity = driver.public_key().hex()
    first = session(proxy, clock, "first", "0.89")
    second = session(proxy, clock, "second", "0.51")
    before = copy.deepcopy(api.saved)
    queue = await jobs(api, identity)
    assert [j["session_id"] for j in queue["jobs"]] == [first["session_id"], second["session_id"]]
    assert api.saved == before  # Discovery itself is read-only.
    for job, amount in zip(queue["jobs"], (89, 51)):
        result = await handle(api, identity, {"action": "debit_status", **job})
        assert result["collection"]["state"] == "ready"
        with pytest.raises(WalletError):
            await handle(api, identity, {"action": "debit_claim", **job})  # Login alone is not a wallet claim.
        child = next(r for r in api.saved["session_budgets"].values()
                     if r["terms"]["session_id"] == job["session_id"])
        args = claim_data(result["collection"], child, driver)
        assert (await handle(api, identity, {"action": "debit_claim", **job, **args}))["claimed"]
        tx = payment(api, driver, amount=amount, fee=5)
        permit = await handle(api, identity, {"action": "debit_authorise", **job, **args,
                                            "draft": transaction_shape(tx)})
        assert permit["submit_once"]
        result = await handle(api, identity, {"action": "debit_report", **job, **args, "raw_tx": tx.hex()})
        assert result["state"] == "provider_confirmed"
        posts = len(api.chain.posts)
        await handle(api, identity, {"action": "debit_report", **job, **args, "raw_tx": tx.hex()})
        assert len(api.chain.posts) == posts  # Same bytes reconcile; no second broadcast.
    assert len(api.chain.posts) == 2  # Recording mock only; no network or real funds.
    assert (await jobs(api, identity))["jobs"] == []
    assert remaining(api, root) == 850  # Two net amounts and both actual fees.
    assert root["terms"]["max_total_sats"] == 1000


async def test_wrong_wallet_expiry_revocation_and_recovery_stay_guarded(tmp_path, monkeypatch):
    _, api, proxy, root, driver, _, clock = await setup(tmp_path, monkeypatch)
    session(proxy, clock, "closed", "0.89")
    identity = driver.public_key().hex()
    job = (await jobs(api, identity))["jobs"][0]
    assert (await jobs(api, PrivateKey(999).public_key().hex()))["jobs"] == []
    with pytest.raises(WalletError):
        await handle(api, PrivateKey(999).public_key().hex(), {"action": "debit_status", **job})
    with pytest.raises(WalletError, match="recovery hold"):
        await handle(api, identity, {"action": "debit_claim", **job, "confirm_recovered_attempt": True})
    root["state"] = "revoked"
    assert (await jobs(api, identity))["jobs"] == []
    root["state"] = "spending_authorised_wallet_permission_required"
    clock[0] += timedelta(days=8)
    assert (await jobs(api, identity))["jobs"] == []


async def test_http_debit_actions_require_authenticated_owner_not_private_link(tmp_path):
    _, api, row, driver, _, view, hub, client = await fixture(tmp_path)
    before = copy.deepcopy(api.saved)
    try:
        assert (await post(client, view, "debit_jobs")).status == 401
        cookie, _, _ = await login(client, view, PrivateKey(999))
        response = await post(client, view, "debit_status", cookie,
                              budget_id=row["terms"]["budget_id"], session_id=row["terms"]["session_id"])
        assert response.status == 409
        assert api.saved == before
    finally:
        await hub.close_all()
        await client.close()
