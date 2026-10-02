"""Read-only UX summaries must not expose capabilities or mix sessions."""
import json
import pytest
from test_budget import invitation, no_network
from test_auto_credit import ready
from test_balance_refresh import wallet
from custom_components.bsv_settlement.coordinator import SettlementCoordinator

pytestmark = pytest.mark.asyncio


async def test_operator_summary_has_no_wallet_secrets_or_private_link(tmp_path):
    _, api, _, _, row = await invitation(tmp_path)
    text = json.dumps(api.status())
    assert api.identity["secret_hex"] not in text
    assert "driver_token_hash" not in text
    assert "driver_link_fragment" not in text
    assert "proof" not in json.dumps(api.status()["driver_approvals"])
    assert api.status()["driver_approvals"][-1]["session_id"] == "session-1"


async def test_driver_snapshot_is_only_the_approved_session(tmp_path):
    _, api, proxy, row, _, _ = await ready(tmp_path)
    assert api.budgets.driver_view(row)["session"]["session_id"] == "session-1"
    proxy.data["latest_session"]["session_id"] = "another-driver"
    assert api.budgets.driver_view(row)["session"] is None


async def test_periodic_wallet_check_reconciles_manual_payment(tmp_path):
    from test_mainnet import approval
    hass, api, _ = await wallet(tmp_path)
    p = await api.prepare_payment({"reference": "manual-ux-payment", "amount_sats": 124, "fee_sats": 10})
    await api.broadcast_payment(approval(p), "admin")
    assert api.status()["last_payment"]["state"] == "submitted"
    api.chain.confirmed = True
    api._balance_next_refresh = 0
    # Disable new automatic credits; this test only reconciles the manual payment.
    await api.auto_credits.configure(False, "admin")
    coord = SettlementCoordinator(hass, api.entry, api)
    health = (await coord._async_update_data())["health"]
    assert health["last_payment"]["state"] == "provider_confirmed"
    assert health["pending_change_sats"] == 0
    assert len(api.chain.posts) == 1
