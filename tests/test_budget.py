"""Budget consent tests use fictional keys only; never call chain services."""
import copy
import json
from datetime import timedelta

import pytest
import pytest_asyncio
pytest.importorskip("homeassistant")
from bsv import PrivateKey, PublicKey
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.budget import approval_payload, PROTOCOL, message_hash
from custom_components.bsv_settlement.session_review import now
from test_mainnet import setup_wallet
from test_session_review import source, session

pytestmark = pytest.mark.asyncio
OPEN_HASS = []

@pytest_asyncio.fixture(autouse=True)
async def no_network(monkeypatch):
    monkeypatch.setattr("custom_components.bsv_settlement.mainnet.async_get_clientsession", lambda hass: None)
    yield
    while OPEN_HASS:
        await OPEN_HASS.pop().async_stop(force=True)


async def invitation(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    OPEN_HASS.append(hass)
    record = session()
    record["ended_at"] = None
    proxy = source(hass, record)
    proxy.sources = {"import_price":"sensor.demo_import_price","export_price":"sensor.demo_export_price"}
    data = {"proxy_config_entry_id":"proxy-entry", "session_id":"session-1",
            "conversion_rate_entity":"sensor.demo_rate", "max_total_sats":1000,
            "max_fee_sats":10, "valid_minutes":120}
    row = await api.budgets.execute("create_session_budget", data, "admin")
    return hass, api, proxy, data, row


def consent(row, driver=None):
    driver = driver or PrivateKey()
    identity = driver.public_key().hex()
    payload = approval_payload(row["invitation"], identity)
    budget_id = row["terms"]["budget_id"]
    child = driver.derive_child(PrivateKey(1).public_key(), f"2-{PROTOCOL[1]}-{budget_id}")
    return {"version":1, "budget_id":budget_id, "driver_identity":identity,
            "payload":payload, "signature":child.sign(payload.encode(), hasher=message_hash).hex()}


async def test_invitation_signature_limits_and_idempotence(tmp_path):
    hass, api, proxy, data, row = await invitation(tmp_path)
    i = row["invitation"]
    assert PublicKey(bytes.fromhex(row["terms"]["operator_identity"])).verify(bytes.fromhex(i["signature"]), i["payload"].encode(), hasher=message_hash)
    assert row["terms"]["max_total_sats"] == 1000
    assert "session_key" not in row
    hass.states.async_set("sensor.demo_rate", "200", {"unit_of_measurement":"sat/AUD"})
    again = await api.budgets.execute("create_session_budget", data | {"max_total_sats":2000}, "admin")
    assert again == row
    assert api.chain.posts == []


async def test_verify_bound_identity_and_persistence(tmp_path):
    _, api, _, _, row = await invitation(tmp_path)
    receipt = consent(row)
    data = {"budget_id":row["terms"]["budget_id"],"receipt":receipt}
    result = await api.budgets.execute("accept_session_budget", data, "admin")
    assert result["state"] == "consent_verified_not_payment_authority"
    assert (await api.budgets.execute("accept_session_budget",data,"admin")) == result
    from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
    restored = MainnetWalletAPI(api.hass,api.entry)
    await restored.load()
    assert (await restored.budgets.execute("session_budget_status",data,"admin")) == result
    other = consent(row)
    with pytest.raises(WalletError,match="different driver"):
        await api.budgets.execute("accept_session_budget",data | {"receipt":other},"admin")
    assert api.chain.posts == []


@pytest.mark.parametrize("field,value", [("signature","00"*70),("payload","altered"),
    ("driver_identity","02"+"00"*32),("version",2),("budget_id","different")])
async def test_tampered_receipts_rejected(tmp_path, field, value):
    _, api, _, _, row = await invitation(tmp_path)
    receipt = consent(row); receipt[field] = value
    with pytest.raises(WalletError):
        await api.budgets.execute("accept_session_budget",
            {"budget_id":row["terms"]["budget_id"],"receipt":receipt},"admin")


async def test_revocation_expiry_and_closed_session(tmp_path):
    _, api, proxy, data, row = await invitation(tmp_path)
    key = row["terms"]["budget_id"]
    await api.budgets.execute("revoke_session_budget",{"budget_id":key},"admin")
    with pytest.raises(WalletError,match="expired or revoked"):
        await api.budgets.execute("accept_session_budget",{"budget_id":key,"receipt":consent(row)},"admin")
    fresh = await api.budgets.execute("create_session_budget",data,"admin")
    assert fresh["terms"]["budget_id"] != key
    api.saved["session_budgets"][fresh["terms"]["budget_id"]]["terms"]["expires_at"] = (now()-timedelta(seconds=1)).isoformat()
    with pytest.raises(WalletError,match="expired"):
        await api.budgets.execute("accept_session_budget",{"budget_id":fresh["terms"]["budget_id"],"receipt":consent(fresh)},"admin")
    proxy.data["latest_session"]["ended_at"] = now().isoformat()
    with pytest.raises(WalletError,match="open session"):
        await api.budgets.execute("create_session_budget",data,"admin")


async def test_no_context_and_bad_limits(tmp_path):
    _, api, _, data, row = await invitation(tmp_path)
    with pytest.raises(WalletError,match="administrator"):
        await api.budgets.execute("create_session_budget",data,None)
    await api.budgets.execute("revoke_session_budget",{"budget_id":row["terms"]["budget_id"]},"admin")
    for values in ({"max_total_sats":True},{"max_total_sats":10,"max_fee_sats":10},{"valid_minutes":1441}):
        with pytest.raises(WalletError):
            await api.budgets.execute("create_session_budget",data | values,"admin")

async def test_typescript_python_signature_interoperability(tmp_path):
    import asyncio
    from pathlib import Path
    _, api, _, _, row = await invitation(tmp_path)
    script = Path(__file__).resolve().parents[1] / "frontend/driver/cross-sdk.cjs"
    process = await asyncio.create_subprocess_exec("node", str(script),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate(json.dumps(row["invitation"]).encode())
    assert process.returncode == 0, stderr.decode()
    receipt = json.loads(stdout)
    result = await api.budgets.execute("accept_session_budget",
        {"budget_id":row["terms"]["budget_id"], "receipt":receipt}, "admin")
    assert result["state"] == "consent_verified_not_payment_authority"
    assert api.chain.posts == []

async def test_all_budget_services_admin_only(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from homeassistant.core import Context
    from homeassistant.exceptions import HomeAssistantError
    from custom_components.bsv_settlement import async_setup
    from custom_components.bsv_settlement.coordinator import SettlementCoordinator
    hass, api, _, data, row = await invitation(tmp_path)
    coord = SettlementCoordinator(hass, api.entry, api)
    await coord.load()
    await async_setup(hass, {})
    hass.data["bsv_settlement"][api.entry.entry_id] = coord
    common = {"config_entry_id":api.entry.entry_id}
    actions = {
        "create_session_budget": data,
        "session_budget_status": {"budget_id":row["terms"]["budget_id"]},
        "revoke_session_budget": {"budget_id":row["terms"]["budget_id"]},
        "accept_session_budget": {"budget_id":row["terms"]["budget_id"],"receipt":consent(row)},
    }
    hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
    for name, fields in actions.items():
        for ctx in (Context(),Context(user_id="non-admin")):
            with pytest.raises(HomeAssistantError,match="administrator"):
                await hass.services.async_call("bsv_settlement", name, common | fields,
                    context=ctx, blocking=True)
    hass.auth.async_get_user.return_value.is_admin = True
    result = await hass.services.async_call("bsv_settlement","accept_session_budget",
        common | actions["accept_session_budget"], context=Context(user_id="admin"),
        blocking=True, return_response=True)
    assert result["state"] == "consent_verified_not_payment_authority"
    assert api.chain.posts == []


async def test_driver_distribution_and_no_payment_calls():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    for name in ("index.html","style.css"):
        assert (root/"frontend/driver"/name).read_bytes() == (root/"custom_components/bsv_settlement/frontend/driver"/name).read_bytes()
    assert (root/"frontend/bsv-budget-card.js").read_bytes() == (root/"custom_components/bsv_settlement/frontend/budget-card.js").read_bytes()
    for name in ("app.js","model.js"):
        code = (root/"frontend/driver"/name).read_text()
        for forbidden in ("createAction(", "signAction(", "internalizeAction(", "fetch(", "localStorage"):
            assert forbidden not in code
