"""Connected-wallet routing: fictional identities, no live chain or wallet."""
import copy
import json
from datetime import timedelta

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.portal_registration import handle, offer, unassigned
from custom_components.bsv_settlement.weekly import ticket, remaining
from test_budget import consent, no_network
from test_auto_credit import proof
from test_current_weekly import current_setup

pytestmark = pytest.mark.asyncio


async def fixture(tmp_path):
    api, proxy, args = await current_setup(tmp_path)
    # Operator-created template. Do not attach the current account until the
    # authenticated wallet requests its own bounded offer.
    template = await api.budgets.create({k: v for k, v in args.items()
                                       if k != "initial_session_id"}, "admin")
    from custom_components.bsv_settlement.session_review import now
    api.ongoing_credits.policy.update(enabled=True, enabled_at=(now()-timedelta(days=1)).isoformat(),
        proxy_config_entry_id=args["proxy_config_entry_id"], conversion_rate_entity=args["conversion_rate_entity"])
    for source in ("import_price", "export_price"):
        api.hass.states.async_set(proxy.sources[source], "0.25", {
            "unit_of_measurement": "AUD/kWh", "start_time": (now()-timedelta(minutes=1)).isoformat(),
            "end_time": (now()+timedelta(minutes=5)).isoformat()})
    return api, proxy, template


async def approve(api, wallet, offered):
    row = api.saved["session_budgets"][json.loads(offered["invitation"]["payload"])["budget_id"]]
    identity = wallet.public_key().hex()
    result = await handle(api, identity, {"action": "registration_accept",
        "budget_id": row["terms"]["budget_id"], "receipt": consent(row, wallet)})
    assert result["driver_identity"] == identity
    await handle(api, identity, {"action": "registration_receive",
        "budget_id": row["terms"]["budget_id"], **proof(api, row, wallet)})
    return row


async def test_current_unassigned_and_future_route_to_verified_wallet(tmp_path):
    api, proxy, template = await fixture(tmp_path)
    driver = PrivateKey(140)
    historical = copy.deepcopy(api.saved["session_budgets"])
    offered = await offer(api, driver.public_key().hex())
    assert (await offer(api, driver.public_key().hex()))["invitation"] == offered["invitation"]
    assert all(api.saved["session_budgets"][k] == v for k, v in historical.items())
    row = await approve(api, driver, offered)
    assert row["terms"]["included_session"]["session_id"] == "session-1"
    assert row["terms"]["max_total_sats"] == 1000
    assert remaining(api, row) == 1000
    route = next(iter(api.ongoing_credits.routes.values()))
    assert route["recipient"]["driver_identity"] == driver.public_key().hex()
    assert route["recipient"]["address"] == row["credit_destination"]["address"]
    before_retry = copy.deepcopy(api.saved)
    await handle(api, driver.public_key().hex(), {"action": "registration_receive",
        "budget_id": row["terms"]["budget_id"], **proof(api, row, driver)})
    assert api.saved == before_retry
    child = await ticket(api, row, "session-1")
    assert child["receipt"]["driver_identity"] == driver.public_key().hex()
    assert (await api.collections.status(child))["state"] == "ready"
    assert not api.chain.posts
    assert await offer(api, driver.public_key().hex()) == {"state": "existing_approval"}


@pytest.mark.parametrize("index", ["driver_collection_index", "automatic_credit_index",
                                  "session_review_index", "closed_sessions"])
async def test_assigned_or_closed_accounts_are_never_adopted(tmp_path, index):
    api, proxy, _ = await fixture(tmp_path)
    api.saved.setdefault(index, {})["proxy-entry|session-1"] = "original-owner"
    original = copy.deepcopy(api.saved[index])
    offered = await offer(api, PrivateKey(141).public_key().hex())
    import json
    assert "included_session" not in json.loads(offered["invitation"]["payload"])
    assert api.saved[index] == original
    assert not api.chain.posts


async def test_prior_credit_route_remains_frozen(tmp_path):
    api, proxy, _ = await fixture(tmp_path)
    route = {"proxy_config_entry_id": "proxy-entry", "session_id": "session-1",
             "recipient": {"driver_identity": "original", "address": "original-address"}}
    api.ongoing_credits.routes["ongoing:proxy-entry|session-1"] = route
    offered = await offer(api, PrivateKey(142).public_key().hex())
    import json
    assert "included_session" not in json.loads(offered["invitation"]["payload"])
    assert api.ongoing_credits.routes["ongoing:proxy-entry|session-1"] == route


async def test_policy_off_blocks_new_registration(tmp_path):
    api, _, _ = await fixture(tmp_path)
    api.ongoing_credits.policy["enabled"] = False
    before = copy.deepcopy(api.saved)
    with pytest.raises(WalletError):
        await offer(api, PrivateKey(143).public_key().hex())
    assert api.saved == before


async def test_other_wallet_cannot_read_or_accept_offer(tmp_path):
    api, _, _ = await fixture(tmp_path)
    a, b = PrivateKey(144), PrivateKey(145)
    offered = await offer(api, a.public_key().hex())
    import json
    bid = json.loads(offered["invitation"]["payload"])["budget_id"]
    row = api.saved["session_budgets"][bid]
    for action in ("registration_read", "registration_accept", "registration_receive"):
        with pytest.raises(WalletError):
            await handle(api, b.public_key().hex(), {"action": action, "budget_id": bid, "receipt": consent(row, b)})
    assert row["receipt"] is None


async def test_ownership_race_blocks_acceptance(tmp_path):
    api, _, _ = await fixture(tmp_path)
    driver = PrivateKey(146)
    offered = await offer(api, driver.public_key().hex())
    import json
    bid = json.loads(offered["invitation"]["payload"])["budget_id"]
    row = api.saved["session_budgets"][bid]
    api.saved["driver_collection_index"]["proxy-entry|session-1"] = "already-submitted"
    with pytest.raises(WalletError, match="owner"):
        await handle(api, driver.public_key().hex(), {"action": "registration_accept",
            "budget_id": bid, "receipt": consent(row, driver)})
    assert row["receipt"] is None and not api.chain.posts


async def test_client_cannot_select_limit_or_historical_session(tmp_path):
    api, _, _ = await fixture(tmp_path)
    result = await handle(api, PrivateKey(147).public_key().hex(), {
        "action": "registration_offer", "max_total_sats": 99999,
        "initial_session_id": "unrelated-history", "operator_address": "attacker"})
    import json
    t = json.loads(result["invitation"]["payload"])
    assert t["max_total_sats"] == 1000
    assert t["operator_address"] == api.identity["address"]
    assert t["included_session"]["session_id"] == "session-1"


async def test_replaced_operator_template_blocks_old_offer(tmp_path):
    api, _, template = await fixture(tmp_path)
    driver = PrivateKey(148)
    offered = await offer(api, driver.public_key().hex())
    bid = json.loads(offered["invitation"]["payload"])["budget_id"]
    row = api.saved["session_budgets"][bid]
    original = api.saved["session_budgets"][template["terms"]["budget_id"]]
    original["state"] = "revoked"
    with pytest.raises(WalletError):
        await handle(api, driver.public_key().hex(), {"action": "registration_accept",
            "budget_id": bid, "receipt": consent(row, driver)})
    assert row["receipt"] is None


async def test_expired_old_registration_does_not_own_new_session(tmp_path):
    api, proxy, template = await fixture(tmp_path)
    from custom_components.bsv_settlement.session_review import now
    proxy.data["latest_session"]["opened_at"] = (now()-timedelta(hours=1)).isoformat()
    old = copy.deepcopy(api.saved["session_budgets"][template["terms"]["budget_id"]])
    old["terms"].update(budget_id="historical", session_id="multi:historical",
                       expires_at=(now()-timedelta(days=1)).isoformat())
    old["credit_destination"] = {"registered_at": (now()-timedelta(days=8)).isoformat()}
    api.saved["session_budgets"]["historical"] = old
    assert unassigned(api, "proxy-entry", "session-1")
