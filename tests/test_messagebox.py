"""Opt-in MessageBox (PeerPay-compatible) credit delivery. No live network."""
import asyncio
import base64
import copy
import json
from datetime import timedelta

import pytest
from bsv import PrivateKey, Transaction

from custom_components.bsv_settlement import messagebox
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.messagebox import (
    BOX, MAX_ATTEMPTS, MESSAGE_PROTOCOL, OperatorAuthWallet, RETRY_AFTER, payment_request,
)
from test_budget import no_network  # noqa: F401  Autouse: no live provider sessions.
from test_receipt_ack import confirmed, report
from custom_components.bsv_settlement.receipt_ack import acknowledge

pytestmark = pytest.mark.asyncio


class Sender:
    def __init__(self, result=(200, {"status": "success"})):
        self.calls, self.result, self.lock = [], result, None

    def __call__(self, secret_hex, host, payload):
        self.calls.append({"host": host, "payload": json.loads(payload), "lock_held": self.lock.locked()})
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def decrypt(driver, operator_public, wire):
    from bsv.primitives.symmetric_key import SymmetricKey
    wallet = OperatorAuthWallet(driver)
    key = wallet.symmetric_key(MESSAGE_PROTOCOL, "1", operator_public)
    return json.loads(SymmetricKey(key).decrypt(base64.b64decode(json.loads(wire)["encryptedMessage"])))


def realistic_proof(api, item):
    """A credit is never alone in its block (that is the coinbase): one sibling node."""
    from bsv.hash import hash256
    sibling, original = "33" * 32, api.chain.request
    root = hash256(bytes.fromhex(item["txid"])[::-1] + bytes.fromhex(sibling)[::-1])[::-1].hex()

    async def request(method, path, raw=False):
        if path.endswith("/proof/tsc"):
            return [{"txOrId": item["txid"], "index": 0, "nodes": [sibling], "target": "22" * 32}]
        if path.startswith("/block/hash/"):
            return {"hash": "22" * 32, "height": 800000, "merkleroot": root}
        return await original(method, path, raw)
    api.chain.request = request
    item.pop("receipt", None)


async def setup(tmp_path, ongoing=False, enabled=True):
    hass, api, row, driver, item = await confirmed(tmp_path, ongoing)
    realistic_proof(api, item)
    sender = Sender()
    api.messagebox.sender = sender
    if enabled:
        await api.messagebox.configure({"enabled": True}, "admin")
    credit_id = next(k for k, v in api.saved["automatic_credits"].items() if v is item)
    return hass, api, row, driver, item, sender, credit_id


async def run(api, sender):
    lock = asyncio.Lock()
    sender.lock = lock
    await api.messagebox.run(lock)


async def test_disabled_by_default_and_admin_only_configuration(tmp_path):
    hass, api, row, driver, item, sender, credit_id = await setup(tmp_path, enabled=False)
    assert api.messagebox.summary() == {"enabled": False, "host": messagebox.DEFAULT_HOST,
                                        "enabled_at": None, "message_box": BOX}
    assert api.messagebox.schedule(asyncio.Lock()) is None
    with pytest.raises(WalletError, match="Enable"):
        await api.messagebox.request(credit_id, "admin")
    for data, user in [({"enabled": True}, None), ({"enabled": "yes"}, "admin"),
                       ({"enabled": True, "host": "http://insecure.example"}, "admin"),
                       ({"enabled": True, "host": "https://evil.example/path"}, "admin")]:
        with pytest.raises(WalletError, match="administrator"):
            await api.messagebox.configure(data, user)
    result = await api.messagebox.configure({"enabled": True}, "admin")
    assert result["enabled"] is True and result["enabled_at"]
    assert sender.calls == []


@pytest.mark.parametrize("ongoing", [False, True])
async def test_requested_credit_is_sent_as_an_encrypted_peerpay_payment(tmp_path, ongoing):
    hass, api, row, driver, item, sender, credit_id = await setup(tmp_path, ongoing)
    posts, before = len(api.chain.posts), copy.deepcopy(api.saved)
    # Created before enablement: never sent automatically, only on request.
    await run(api, sender)
    assert sender.calls == []
    assert await api.messagebox.request(credit_id, "admin") == {"credit_id": credit_id, "queued": True}
    await run(api, sender)
    assert len(sender.calls) == 1 and sender.calls[0]["lock_held"] is False
    message = sender.calls[0]["payload"]["message"]
    identity = driver.public_key().hex()
    assert message["recipient"] == identity and message["messageBox"] == BOX
    assert len(message["messageId"]) == 64
    token = decrypt(driver, api.identity["public_key"], message["body"])
    remit = row["terms"]["credit_receiving"]
    assert token["customInstructions"] == {"derivationPrefix": remit["derivationPrefix"],
                                           "derivationSuffix": remit["derivationSuffix"]}
    assert token["amount"] == item["amount_sats"] and token["outputIndex"] == 0
    beef = bytes(token["transaction"])
    assert beef[:4] == bytes([1, 1, 1, 1]) and beef[4:36] == bytes.fromhex(item["txid"])[::-1]
    assert item["signed_raw"] in beef.hex()
    delivery = item["messagebox_delivery"]
    assert delivery["state"] == "sent" and delivery["recipient"] == identity and delivery["attempts"] == 1
    assert api.auto_credits.public(item)["inbox_delivery"]["state"] == "sent"
    # Evidence only: no payment, broadcast or change beyond delivery state and the cached proof.
    assert len(api.chain.posts) == posts
    fields = ("state", "txid", "signed_raw", "amount_sats", "fee_sats", "recipient_address", "account")
    assert {k: item.get(k) for k in fields} == {k: before["automatic_credits"][credit_id].get(k) for k in fields}
    assert {k: v for k, v in api.saved.items() if k not in ("automatic_credits", "messagebox_delivery_policy")} == {
        k: v for k, v in before.items() if k not in ("automatic_credits", "messagebox_delivery_policy")}
    with pytest.raises(WalletError, match="already delivered"):
        await api.messagebox.request(credit_id, "admin")


async def test_new_credits_are_sent_automatically_once(tmp_path):
    hass, api, row, driver, item, sender, credit_id = await setup(tmp_path)
    item["created_at"] = (messagebox.now() + timedelta(seconds=1)).isoformat()
    await run(api, sender)
    await run(api, sender)
    assert len(sender.calls) == 1 and item["messagebox_delivery"]["state"] == "sent"


async def test_failures_are_bounded_and_retried_after_a_delay(tmp_path, monkeypatch):
    hass, api, row, driver, item, sender, credit_id = await setup(tmp_path)
    item["created_at"] = (messagebox.now() + timedelta(seconds=1)).isoformat()
    sender.result = (500, None)
    clock = [messagebox.now()]
    monkeypatch.setattr(messagebox, "now", lambda: clock[0])
    await run(api, sender)
    await run(api, sender)  # Not due again yet.
    assert len(sender.calls) == 1
    assert item["messagebox_delivery"]["state"] == "failed"
    assert "HTTP 500" in item["messagebox_delivery"]["error"]
    sender.result = OSError("offline")
    for _ in range(MAX_ATTEMPTS + 2):
        clock[0] += RETRY_AFTER
        await run(api, sender)
    assert len(sender.calls) == MAX_ATTEMPTS
    assert item["messagebox_delivery"]["error"] == "MessageBox delivery failed: OSError"
    # An explicit request starts a fresh bounded attempt.
    sender.result = (200, {"status": "success"})
    await api.messagebox.request(credit_id, "admin")
    await run(api, sender)
    assert item["messagebox_delivery"]["state"] == "sent"


async def test_wallet_reported_or_unconfirmed_credits_are_never_sent(tmp_path):
    hass, api, row, driver, item, sender, credit_id = await setup(tmp_path)
    item["created_at"] = (messagebox.now() + timedelta(seconds=1)).isoformat()
    item["state"] = "provider_unconfirmed"
    with pytest.raises(WalletError, match="provider-confirmed"):
        await api.messagebox.request(credit_id, "admin")
    await run(api, sender)
    item["state"] = "provider_confirmed"
    await acknowledge(api, row, report(api, row, item, driver))
    with pytest.raises(WalletError, match="provider-confirmed"):
        await api.messagebox.request(credit_id, "admin")
    await run(api, sender)
    assert sender.calls == []


async def test_mismatched_recipient_is_refused_before_sending(tmp_path):
    hass, api, row, driver, item, sender, credit_id = await setup(tmp_path)
    await api.messagebox.request(credit_id, "admin")
    item["recipient_address"] = PrivateKey(77).public_key().address()
    await run(api, sender)
    assert sender.calls == []
    assert item["messagebox_delivery"]["state"] == "failed"
    assert "does not match" in item["messagebox_delivery"]["error"]


async def test_schedule_starts_one_background_run(tmp_path):
    hass, api, row, driver, item, sender, credit_id = await setup(tmp_path)
    await api.messagebox.request(credit_id, "admin")
    lock = asyncio.Lock()
    sender.lock = lock
    task = api.messagebox.schedule(lock)
    assert task is not None and api.messagebox.schedule(lock) is None
    await task
    assert len(sender.calls) == 1 and api.messagebox.running is False


def test_auth_wallet_signs_verifies_and_hmacs_with_flat_or_nested_arguments():
    operator, other = OperatorAuthWallet(PrivateKey(31)), OperatorAuthWallet(PrivateKey(32))
    other_hex = other.public_key.hex()
    nested = {"encryption_args": {"protocol_id": {"securityLevel": 2, "protocol": "auth message signature"},
                                  "key_id": "a b", "counterparty": {"type": 3, "counterparty": other_hex}},
              "data": b"payload"}
    flat = {"protocolID": [2, "auth message signature"], "keyID": "a b", "counterparty": other_hex,
            "data": b"payload"}
    signature = operator.create_signature(nested).signature
    assert operator.create_signature(flat).signature  # Both argument shapes are accepted.
    check = {"protocolID": [2, "auth message signature"], "keyID": "a b",
             "counterparty": operator.public_key.hex(), "data": b"payload", "signature": signature}
    assert other.verify_signature(check).valid is True
    assert other.verify_signature(check | {"data": b"changed"}).valid is False
    mac = operator.create_hmac({"protocolID": [2, "server hmac"], "keyID": "k", "data": b"x"})["hmac"]
    assert operator.verify_hmac({"protocolID": [2, "server hmac"], "keyID": "k", "data": b"x", "hmac": mac}).valid
    assert not operator.verify_hmac({"protocolID": [2, "server hmac"], "keyID": "k", "data": b"y", "hmac": mac}).valid
    assert operator.get_public_key({"identityKey": True}).public_key == operator.public_key
    assert operator.create_action({}).error


async def test_official_typescript_sdk_reads_the_message_and_payment(tmp_path):
    from pathlib import Path
    hass, api, row, driver, item, sender, credit_id = await setup(tmp_path)
    receipt = await api.auto_credits.receipt_for_item(row, item)
    wallet = OperatorAuthWallet(PrivateKey(bytes.fromhex(api.identity["secret_hex"])))
    message_id, payload = payment_request(wallet, driver.public_key().hex(), receipt)
    signature = wallet.create_signature({"protocolID": [2, "auth message signature"], "keyID": "x y",
                                         "counterparty": driver.public_key().hex(), "data": b"signed"}).signature
    script = Path(__file__).resolve().parents[1] / "frontend/driver/cross-messagebox.js"
    proc = await asyncio.create_subprocess_exec("node", str(script),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, error = await proc.communicate(json.dumps({
        "driver_secret": driver.hex(), "operator_secret": api.identity["secret_hex"],
        "request": json.loads(payload), "signature": signature.hex()}).encode())
    assert proc.returncode == 0, error.decode()
    result = json.loads(out)
    assert result == {"txid": item["txid"], "amount": item["amount_sats"], "output_matches": True,
                      "merkle_root_matches": True, "message_id_matches": True, "signature_valid": True}
    assert Transaction.from_hex(item["signed_raw"]).txid() == item["txid"] and message_id


async def test_disabling_stops_queued_requests(tmp_path):
    hass, api, row, driver, item, sender, credit_id = await setup(tmp_path)
    await api.messagebox.request(credit_id, "admin")
    await api.messagebox.configure({"enabled": False}, "admin")
    assert api.messagebox.schedule(asyncio.Lock()) is None
    await run(api, sender)
    assert sender.calls == []
