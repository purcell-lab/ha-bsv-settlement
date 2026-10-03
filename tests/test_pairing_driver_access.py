"""Pairing stays behind the real driver-capability validation."""
import pytest
from custom_components.bsv_settlement.pairing import KEY, PairingHub
from test_driver_http import reservation, client_for, access
from test_budget import no_network

pytestmark = pytest.mark.asyncio


async def test_pairing_create_cancel_requires_current_invitation(tmp_path):
    hass, api, proxy, data, row = await reservation(tmp_path)
    hass.config.external_url = "https://charging.example.com"
    client, view = await client_for(hass, api)
    hub = hass.data[KEY] = PairingHub(hass)
    body = {**access(row), "action": "pairing_create",
            "backend_identity": "02" + "11" * 32}
    try:
        res = await client.post(view.url, json=body | {"token": "x" * 43},
                                headers={"Origin": hass.config.external_url})
        assert res.status == 400
        assert not hub.sessions
        res = await client.post(view.url, json=body,
                                headers={"Origin": "https://wrong.example.com"})
        assert res.status == 400
        res = await client.post(view.url, json=body,
                                headers={"Origin": hass.config.external_url})
        assert res.status == 200
        pairing = await res.json()
        assert pairing["topic"] in hub.sessions
        assert api.saved["session_budgets"][body["budget_id"]]["state"] == "awaiting_driver_consent"
        assert not api.chain.posts
        res = await client.post(view.url, json={**access(row), "action": "pairing_cancel",
                                               "topic": pairing["topic"]})
        assert res.status == 200
        assert not hub.sessions
        await api.budgets.execute("revoke_session_budget", {"budget_id":body["budget_id"]}, "admin")
        res = await client.post(view.url, json=body, headers={"Origin":hass.config.external_url})
        assert res.status == 400
        assert not hub.sessions
    finally:
        await hub.close_all()
        await client.close()
