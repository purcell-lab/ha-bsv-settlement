"""Automatic credits use only fictional keys and a recording fake provider."""
from datetime import timedelta
from copy import deepcopy

import pytest
from bsv import PrivateKey, PublicKey, Transaction
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.budget import message_hash, canonical
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import now
from test_budget import invitation, consent, no_network
from test_mainnet import FictionalChain

pytestmark = pytest.mark.asyncio


class CreditChain(FictionalChain):
    async def request(self, method, path, raw=False):
        if self.fail:
            raise WalletError("Provider unavailable")
        return self.posts[-1]


def proof(api, row, driver):
    payload = api.auto_credits.registration_payload(row)
    key = driver.derive_child(PrivateKey(1).public_key(),
                             f"2-ev session spending-{row['terms']['budget_id']}")
    return {"proof": {"payload": payload, "signature": key.sign(
        payload.encode(), hasher=message_hash).hex()}}


async def ready(tmp_path, amount="-1.89"):
    hass, api, proxy, data, old = await invitation(tmp_path)
    await api.auto_credits.configure(True, "admin")
    await api.budgets.execute("revoke_session_budget", {"budget_id":old["terms"]["budget_id"]}, "admin")
    public = await api.budgets.create(data, "admin")
    row = api.saved["session_budgets"][public["terms"]["budget_id"]]
    driver = PrivateKey()
    await api.budgets.accept(row, consent(row, driver), "driver")
    await api.auto_credits.register(row, proof(api, row, driver))
    proxy.data["latest_session"].update(ended_at=now().isoformat(), net_cost_aud_unrounded=amount)
    api.chain = CreditChain(api.identity["address"])
    return hass, api, proxy, row, driver, data


async def test_pays_without_browser_or_per_payment_approval_and_freezes_rate(tmp_path):
    hass, api, _, row, driver, _ = await ready(tmp_path)
    hass.states.async_set("sensor.demo_rate", "500", {"unit_of_measurement":"sat/AUD"})
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["state"] == "submitted"
    assert item["amount_sats"] == 189 and item["fee_sats"] == 10
    tx = Transaction.from_hex(api.chain.posts[0])
    assert tx.outputs[0].satoshis == 189 and tx.outputs[1].satoshis == 49801
    r = row["terms"]["credit_receiving"]
    key_id = r["derivationPrefix"] + " " + r["derivationSuffix"]
    receiver = driver.derive_child(PublicKey(bytes.fromhex(api.identity["public_key"])),
                                  f"2-3241645161d8-{key_id}")
    assert receiver.address() == item["recipient_address"]
    await api.auto_credits.tick()
    assert item["state"] == "provider_confirmed" and len(api.chain.posts) == 1
    assert (await api.collections.status(row))["direction"] == "operator_to_driver"
    assert "signed_raw" not in canonical(api.auto_credits.summary())


async def test_unknown_submission_and_restart_never_rebroadcast(tmp_path):
    hass, api, _, row, _, _ = await ready(tmp_path)
    api.chain.fail = True
    await api.auto_credits.tick()
    assert api.auto_credits.get(row)["state"] == "broadcast_unknown"
    restored = MainnetWalletAPI(hass, api.entry)
    await restored.load()
    restored.chain = api.chain
    await restored.auto_credits.tick()
    assert len(api.chain.posts) == 1
    restored.chain.fail = False
    await restored.auto_credits.tick()
    assert len(api.chain.posts) == 1
    assert restored.auto_credits.get(row)["state"] == "provider_confirmed"


@pytest.mark.parametrize("amount,posts", [("-9.90",1),("-9.91",0),("0",0),("1.00",0),("-0.001",0)])
async def test_caps_and_direction(tmp_path, amount, posts):
    _, api, _, _, _, _ = await ready(tmp_path, amount)
    await api.auto_credits.tick()
    assert len(api.chain.posts) == posts


@pytest.mark.parametrize("problem", ["disabled","revoked","expired","no_binding","quality",
                                    "unpriced","txid","late_destination","broadcast_disabled"])
async def test_fail_closed_gates(tmp_path, problem):
    _, api, proxy, row, _, _ = await ready(tmp_path)
    if problem == "disabled":
        await api.auto_credits.configure(False, "admin")
    elif problem == "revoked":
        row["state"] = "revoked"
    elif problem == "expired":
        row["terms"]["expires_at"] = (now()-timedelta(seconds=1)).isoformat()
    elif problem == "no_binding":
        row.pop("credit_destination")
    elif problem == "quality":
        proxy.data["latest_session"]["quality_flags"] = ["meter_reset"]
    elif problem == "unpriced":
        proxy.data["latest_session"]["unpriced_export_wh"] = 1
    elif problem == "txid":
        proxy.data["latest_session"]["ocpp_transaction_id"] = "changed"
    elif problem == "late_destination":
        row["credit_destination"]["registered_at"] = (now()+timedelta(minutes=1)).isoformat()
    else:
        api.hass.config_entries.async_update_entry(api.entry,data={**api.entry.data,"enable_broadcast":False})
    await api.auto_credits.tick()
    assert not api.chain.posts


async def test_storage_failure_before_broadcast(tmp_path):
    _, api, _, row, _, _ = await ready(tmp_path)
    original = api.store.async_save
    async def reject_signed(saved):
        if api.auto_credits.get(row).get("txid"):
            raise OSError("Fictional disk failure")
        await original(saved)
    api.store.async_save = reject_signed
    with pytest.raises(OSError):
        await api.auto_credits.tick()
    assert not api.chain.posts


async def test_conflicting_manual_and_no_stale_utxo_reuse(tmp_path):
    _, api, _, row, _, data = await ready(tmp_path)
    await api.auto_credits.tick()
    with pytest.raises(WalletError, match="Automatic credit"):
        await api.reviews.prepare(data, "admin")
    with pytest.raises(WalletError, match="pending automatic"):
        await api.prepare_payment({"reference":"manual-credit","amount_sats":100,"fee_sats":10})
    await api.auto_credits.tick()
    with pytest.raises(WalletError, match="No suitable"):
        await api.prepare_payment({"reference":"manual-credit","amount_sats":100,"fee_sats":10})


async def test_no_funds_queues_then_retries_unsigned_only(tmp_path):
    _, api, proxy, row, _, _ = await ready(tmp_path)
    rows=api.chain.rows
    api.chain.rows=[]
    await api.auto_credits.tick()
    assert api.auto_credits.get(row)["state"] == "credit_queued"
    assert not api.chain.posts
    api.chain.rows=rows
    proxy.data["latest_session"]["net_cost_aud_unrounded"]="-2.00"
    await api.auto_credits.tick()
    assert not api.chain.posts
    assert "changed" in row["credit_error"]


async def test_old_invitations_and_late_registration_rejected(tmp_path):
    _, api, proxy, _, row = await invitation(tmp_path)
    await api.auto_credits.configure(True,"admin")
    driver=PrivateKey()
    stored=api.saved["session_budgets"][row["terms"]["budget_id"]]
    await api.budgets.accept(stored,consent(stored,driver),"driver")
    with pytest.raises(WalletError,match="new invitation"):
        await api.auto_credits.register(stored,proof(api,stored,driver))
    stored["terms"]["created_at"]=now().isoformat()
    # Do not change signed terms to bypass prospectivity: mandate must still fail.
    with pytest.raises(WalletError):
        await api.auto_credits.register(stored,{"proof":{"payload":"wrong","signature":"00"}})


async def test_bad_registration_signature(tmp_path):
    _, api, _, row, driver, _=await ready(tmp_path)
    wrong=proof(api,row,PrivateKey())
    with pytest.raises(WalletError,match="proof"):
        await api.auto_credits.register(row,wrong)
    assert not api.chain.posts


async def test_policy_requires_admin_context_and_is_default_off(tmp_path):
    _, api, _, _, _=await invitation(tmp_path)
    assert api.auto_credits.policy["enabled"] is False
    with pytest.raises(WalletError,match="administrator"):
        await api.auto_credits.configure(True,None)
    await api.auto_credits.configure(True,"admin")
    assert api.auto_credits.summary()["max_total_sats"]==1000
    await api.auto_credits.configure(False,"admin")
    assert api.auto_credits.policy["enabled"] is False


async def test_registration_after_session_end_is_refused(tmp_path):
    _, api, _, row, driver, _=await ready(tmp_path)
    row.pop("credit_destination")
    with pytest.raises(WalletError,match="before"):
        await api.auto_credits.register(row,proof(api,row,driver))
    assert not api.chain.posts


async def test_manual_draft_blocks_auto_and_disabled_policy_still_reconciles(tmp_path):
    _, api, _, row, _, _=await ready(tmp_path)
    draft=await api.prepare_payment({"reference":"manual-first","amount_sats":100,"fee_sats":10})
    await api.auto_credits.tick()
    assert not api.chain.posts
    await api.cancel_payment({"draft_id":draft["draft_id"]})
    await api.auto_credits.tick()
    assert len(api.chain.posts)==1
    await api.auto_credits.configure(False,"admin")
    await api.auto_credits.tick()
    assert api.auto_credits.get(row)["state"]=="provider_confirmed"
    assert len(api.chain.posts)==1


async def test_occupied_closed_session_is_paid_without_reusing_approval_for_resumption(tmp_path):
    _, api, proxy, row, _, _ = await ready(tmp_path)
    ended = proxy.data["latest_session"]
    ended["running_state"] = "Occupied"
    resumed = deepcopy(ended)
    resumed.update(session_id="new-resumed-session", ocpp_transaction_id="new-proxy-id",
                   ended_at=None, status="active_observed", running_state="Discharging")
    proxy.data.update(latest_session=resumed, previous_session=ended)
    await api.auto_credits.tick()
    item = api.auto_credits.get(row)
    assert item["state"] == "submitted" and item["session_id"] == ended["session_id"]
    await api.auto_credits.tick()
    assert len(api.chain.posts) == 1
    assert all(p["session_id"] != resumed["session_id"]
               for p in api.saved["automatic_credits"].values())
