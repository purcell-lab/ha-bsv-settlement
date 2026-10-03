"""Mainnet safety tests with fictional chain data; never contact a broadcaster."""
import copy
from datetime import timedelta
import json
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("homeassistant")
from bsv import PrivateKey, P2PKH, Transaction, TransactionInput, TransactionOutput
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI, WoCClient, utcnow, validate_driver
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.text import DriverText
from test_embedded import make_hass, make_entry


@pytest.fixture(autouse=True)
def no_ha_network_session(monkeypatch):
    monkeypatch.setattr("custom_components.bsv_settlement.mainnet.async_get_clientsession",
                        lambda hass: None)


class FictionalChain:
    def __init__(self, address):
        tx = Transaction([TransactionInput(source_txid="11" * 32)],
                         [TransactionOutput(P2PKH().lock(address), satoshis=50000)])
        self.raw = tx.hex()
        self.rows = [{"tx_hash": tx.txid(), "tx_pos": 0, "value": 50000,
                      "height": 1, "isSpentInMempoolTx": False}]
        self.posts = []
        self.fail = False

    async def unspent(self, address):
        return copy.deepcopy(self.rows)

    async def source(self, txid):
        return self.raw

    async def details(self, txid):
        return {"txid": txid, "confirmations": 1}

    async def fee_policy(self):
        # Fictional 40 sat/KB rounds to 10 sat at the conservative 227-byte bound.
        # Separate fee-aware tests cover current-style 100 sat/KB and quote changes.
        return {"fee_unit": "sat/KB", "fee": 40, "mempool_min_fee": 40}

    async def broadcast(self, raw):
        self.posts.append(raw)
        if self.fail:
            raise WalletError("Simulated response timeout")
        return Transaction.from_hex(raw).txid()


@pytest.mark.asyncio
@pytest.mark.parametrize("coinbase,confirmations,allowed", [
    (False, 1, True), (False, 47, True), (False, 0, False),
    (True, 99, False), (True, 100, True),
])
async def test_woc_funding_maturity_uses_raw_outpoint_not_empty_json_field(coinbase, confirmations, allowed):
    tx = Transaction(
        [TransactionInput(source_txid=("00" if coinbase else "11") * 32,
                          source_output_index=0xFFFFFFFF if coinbase else 0)],
        [TransactionOutput(P2PKH().lock(PrivateKey().address()), satoshis=5000)])
    client = WoCClient(None)
    client.request = AsyncMock(side_effect=[
        tx.hex(), {"txid":tx.txid(), "confirmations":confirmations,
                   "vin":[{"coinbase":"0101" if coinbase else ""}]}])
    if allowed:
        assert await client.source(tx.txid()) == tx.hex()
    else:
        with pytest.raises(WalletError, match="not mature" if coinbase else "provider-confirmed"):
            await client.source(tx.txid())


@pytest.mark.asyncio
async def test_woc_source_rejects_raw_transaction_identity_mismatch():
    tx = Transaction([TransactionInput(source_txid="11"*32)],
                     [TransactionOutput(P2PKH().lock(PrivateKey().address()),satoshis=5000)])
    client = WoCClient(None)
    client.request = AsyncMock(side_effect=[
        tx.hex(), {"txid":"22"*32, "confirmations":100, "vin":[{"coinbase":""}]}])
    with pytest.raises(WalletError,match="identity"):
        await client.source("22"*32)


async def setup_wallet(tmp_path):
    entry = make_entry(backend="embedded_mainnet", network="mainnet",
                       acknowledge_mainnet=True, enable_broadcast=True)
    hass = await make_hass(tmp_path, entry)
    api = MainnetWalletAPI(hass, entry)
    await api.load()
    driver = PrivateKey()
    await api.set_driver("driver_public_identity", driver.public_key().hex())
    await api.set_driver("driver_receive_address", driver.address())
    api.chain = FictionalChain(api.identity["address"])
    return hass, entry, api


def approval(p):
    return {k: p[k] for k in ("draft_id", "recipient_address", "amount_sats", "fee_sats")} | {
        "confirm_mainnet_payment": True}


@pytest.mark.asyncio
async def test_mainnet_prepare_approve_exact_and_duplicate_no_network(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        with patch.object(socket.socket, "connect", side_effect=AssertionError("Network forbidden")):
            p = await api.prepare_payment({"reference": "credit-0001", "amount_sats": 1000, "fee_sats": 100})
            assert api.chain.posts == []
            assert p["txid"] is None and "signed_raw" not in p
            assert p["change_sats"] == 48900
            for changes in ({"amount_sats": 1001}, {"fee_sats": 101},
                            {"recipient_address": PrivateKey().address()},
                            {"confirm_mainnet_payment": False}):
                with pytest.raises(WalletError):
                    await api.broadcast_payment(approval(p) | changes, "admin")
            with pytest.raises(WalletError):
                await api.broadcast_payment(approval(p), None)
            assert not api.chain.posts
            sent = await api.broadcast_payment(approval(p), "admin")
            assert sent["state"] == "submitted"
            assert len(api.chain.posts) == 1
            assert sent["txid"] == Transaction.from_hex(api.chain.posts[0]).txid()
            again = await api.broadcast_payment(approval(p), "admin")
            assert again == sent and len(api.chain.posts) == 1
            restored = MainnetWalletAPI(hass, entry)
            await restored.load()
            restored.chain = api.chain
            assert restored.identity["public_key"] == api.identity["public_key"]
            assert await restored.broadcast_payment(approval(p), "admin") == sent
            assert len(api.chain.posts) == 1
            await restored.refresh_chain()
            assert restored.status()["last_payment"]["state"] == "provider_confirmed"
            with pytest.raises(WalletError, match="No suitable"):
                await restored.prepare_payment({"reference": "credit-0002", "amount_sats": 1000, "fee_sats": 100})
            assert api.identity["secret_hex"] not in json.dumps(restored.status())
            assert "signed_raw" not in json.dumps(restored.status())
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_uncertain_broadcast_reserves_input_and_never_retries(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        p = await api.prepare_payment({"reference": "credit-0001", "amount_sats": 1000, "fee_sats": 100})
        api.chain.fail = True
        with pytest.raises(WalletError, match="uncertain"):
            await api.broadcast_payment(approval(p), "admin")
        assert len(api.chain.posts) == 1
        restored = MainnetWalletAPI(hass, entry)
        await restored.load()
        restored.chain = api.chain
        result = await restored.broadcast_payment(approval(p), "admin")
        assert result["state"] == "broadcast_unknown" and len(api.chain.posts) == 1
        with pytest.raises(WalletError):
            await restored.cancel_payment({"draft_id": p["draft_id"]})
        with pytest.raises(WalletError):
            await restored.prepare_payment({"reference": "credit-0002", "amount_sats": 1000, "fee_sats": 100})
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_expiry_changed_identity_spent_inputs_and_immutable_reference(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        args = {"reference": "credit-0001", "amount_sats": 1000, "fee_sats": 100}
        p = await api.prepare_payment(args)
        assert await api.prepare_payment(args) == p
        with pytest.raises(WalletError, match="different terms"):
            await api.prepare_payment(args | {"amount_sats": 1001})
        api.saved["payments"][p["draft_id"]]["expires_at"] = (utcnow() - timedelta(seconds=1)).isoformat()
        with pytest.raises(WalletError, match="expired"):
            await api.broadcast_payment(approval(p), "admin")
        p2 = await api.prepare_payment(args | {"reference": "credit-0002"})
        await api.set_driver("driver_public_identity", PrivateKey().public_key().hex())
        with pytest.raises(WalletError, match="awaiting"):
            await api.broadcast_payment(approval(p2), "admin")
        p3 = await api.prepare_payment(args | {"reference": "credit-0003"})
        api.chain.rows = []
        with pytest.raises(WalletError, match="no longer"):
            await api.broadcast_payment(approval(p3), "admin")
        assert not api.chain.posts
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_native_driver_dialog_and_admin_only_services(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        coord = SettlementCoordinator(hass, entry, api)
        await coord.load()
        await async_setup(hass, {})
        hass.data["bsv_settlement"][entry.entry_id] = coord
        entity = DriverText(coord, entry, "driver_public_identity", "Driver public identity", 130)
        pub = PrivateKey().public_key().hex()
        await entity.async_set_value(pub)
        assert entity.native_value == pub
        with pytest.raises(HomeAssistantError):
            await entity.async_set_value(PrivateKey().wif())
        assert entity.native_value == pub
        data = {"config_entry_id": entry.entry_id, "reference": "credit-0001",
                "amount_sats": 1000, "fee_sats": 100}
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call("bsv_settlement", "prepare_operator_payment",
                                           data, blocking=True, return_response=True)
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call("bsv_settlement", "prepare_operator_payment",
                                           data, blocking=True, return_response=True,
                                           context=Context(user_id="nonadmin"))
        hass.auth.async_get_user.return_value.is_admin = True
        prepared = await hass.services.async_call(
            "bsv_settlement", "prepare_operator_payment", data, blocking=True, return_response=True,
            context=Context(user_id="admin"))
        assert prepared["state"] == "prepared" and not api.chain.posts
    finally:
        await hass.async_stop(force=True)


@pytest.mark.parametrize("field,value", [
    ("driver_public_identity", "00" * 32),
    ("driver_public_identity", "not a public key"),
    ("driver_public_identity", "02" + "ff" * 32),
    ("driver_receive_address", "mipcBbFg9gMiCh81Kj8tqqdgoZub1ZJRfn"),
    ("driver_receive_address", "1invalid"),
])
def test_reject_invalid_public_driver_data(field, value):
    with pytest.raises(WalletError):
        validate_driver(field, value)


@pytest.mark.asyncio
async def test_provider_errors_and_pagination_fail_closed():
    chain = WoCClient(None)
    address = PrivateKey().address()
    for response in ([], {"address": address, "error": "failed", "result": []},
                     {"address": address, "result": [], "next-page": "more"},
                     {"address": address, "result": [{"tx_hash": "invalid"}]}):
        chain.request = AsyncMock(return_value=response)
        with pytest.raises(WalletError):
            await chain.unspent(address)
