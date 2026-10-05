"""Read-only provisional history metadata; no live wallets or networks."""
import copy

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement.portal import history
from custom_components.bsv_settlement.session_review import now
from test_auto_credit import ready
from test_ongoing_credit import setup
from test_budget import no_network

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("ongoing", [False, True])
async def test_open_account_projects_frozen_rate_not_live_sensor(tmp_path, ongoing):
    hass, api, proxy, row, driver, _ = await (setup(tmp_path) if ongoing else ready(tmp_path))
    record = proxy.data["latest_session"]
    record.update(ended_at=None, net_cost_aud=-.06, as_of=now().isoformat())
    hass.states.async_set("sensor.demo_rate", "999", {"unit_of_measurement": "sat/AUD"})
    before = copy.deepcopy(api.saved)
    posts = len(api.chain.posts)
    result = history(api, driver.public_key().hex())
    target = next(s for s in result if s["session_id"] == record["session_id"])
    assert target["net_amount_aud"] == -.06
    assert target["satoshis_per_aud"] == "100"
    assert target["meter_updated_at"] == record["as_of"]
    assert api.saved == before and len(api.chain.posts) == posts
    assert history(api, PrivateKey().public_key().hex()) == []


async def test_missing_route_rate_does_not_use_unrelated_current_conversion(tmp_path):
    _, api, proxy, row, driver, _ = await setup(tmp_path)
    proxy.data["latest_session"].update(ended_at=None, net_cost_aud=-.06)
    route = next(iter(api.ongoing_credits.routes.values()))
    route["satoshis_per_aud"] = None
    result = history(api, driver.public_key().hex())
    assert result[0]["satoshis_per_aud"] is None


async def test_closed_credit_retains_payment_amount_and_no_provisional_rate(tmp_path):
    _, api, _, _, driver, _ = await ready(tmp_path)
    await api.auto_credits.tick()
    result = history(api, driver.public_key().hex())
    target = next(s for s in result if s["transactions"])
    assert target["ended_at"]
    assert target["satoshis_per_aud"] is None
    assert target["transactions"][0]["amount_sats"] == 189
