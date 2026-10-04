"""Session-linked flows with real SDK transactions and fictional provider data."""
import copy
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")
from bsv import PrivateKey, P2PKH, Transaction, TransactionInput, TransactionOutput
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from custom_components.bsv_settlement import async_setup
from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
from custom_components.bsv_settlement.session_review import account_snapshot, digest, now
from test_mainnet import setup_wallet, approval


@pytest.fixture(autouse=True)
def no_network_session(monkeypatch):
    monkeypatch.setattr("custom_components.bsv_settlement.mainnet.async_get_clientsession", lambda hass: None)


def session(amount="1.89", session_id="session-1"):
    return {
        "session_id": session_id, "ocpp_transaction_id": "proxy-transaction-1",
        "transaction_id_source": "proxy_generated", "status": "ended_observed",
        "opened_at": "2026-10-02T09:00:00+10:00",
        "energy_started_at": "2026-10-02T09:00:05+10:00",
        "ended_at": "2026-10-02T10:00:00+10:00",
        "import_kwh": 1.0, "export_kwh": 0.0, "net_cost_aud_unrounded": amount,
        "quality_flags": ["interval_energy_allocation_estimated", "not_a_final_bill"],
        "unpriced_import_wh": "0", "unpriced_export_wh": "0",
        "estimated_rate_import_wh": "0", "estimated_rate_export_wh": "0",
    }


def source(hass, record):
    proxy = SimpleNamespace(mode="sensor_proxy", archive=[],
                            data={"latest_session": record, "previous_session": None, "issues": []},
                            async_request_refresh=AsyncMock())
    hass.data.setdefault("bsv_settlement", {})["proxy-entry"] = proxy
    hass.states.async_set("sensor.demo_rate", "100", {"unit_of_measurement": "sat/AUD"})
    return proxy


def prepare_data(sid="session-1"):
    return {"proxy_config_entry_id": "proxy-entry", "session_id": sid,
            "conversion_rate_entity": "sensor.demo_rate"}


def review_approval(review):
    return {key: review[key] for key in ("review_id", "terms_hash", "recipient_address", "amount_sats")} | {
        "confirm_account_review": True, "confirm_driver_details": True}


@pytest.mark.asyncio
async def test_request_freezes_terms_rate_and_binding_without_broadcast(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        proxy = source(hass, session())
        review = await api.reviews.execute("prepare_session_review", prepare_data(), "admin")
        assert review["amount_sats"] == 189 and review["state"] == "awaiting_account_approval"
        assert digest(review["frozen_terms"]) == review["terms_hash"]
        assert review["payment_request"] is None
        hass.states.async_set("sensor.demo_rate", "200", {"unit_of_measurement": "sat/AUD"})
        same = await api.reviews.execute("prepare_session_review", prepare_data(), "admin")
        assert same["amount_sats"] == 189 and same["review_id"] == review["review_id"]
        with pytest.raises(WalletError, match="hash"):
            await api.reviews.approve(review_approval(review) | {"terms_hash": "wrong"}, "admin")
        requested = await api.reviews.approve(review_approval(review), "admin")
        assert requested["payment_request"]["amount_sats"] == 189
        assert requested["identity_verification"] == "administrator_attested_not_cryptographic"
        assert digest(requested["frozen_terms"]) == requested["terms_hash"]
        assert not api.chain.posts
        restored = MainnetWalletAPI(hass, entry)
        await restored.load()
        assert restored.reviews.latest()["payment_request"] == requested["payment_request"]
        assert api.identity["secret_hex"] not in json.dumps(restored.status())
        with pytest.raises(WalletError, match="unissued"):
            await api.reviews.cancel({"review_id": review["review_id"]})
    finally:
        await hass.async_stop(force=True)


@pytest.mark.parametrize("changes", [
    {"ended_at": None}, {"quality_flags": ["import:counter_decrease_quarantined"]},
    {"unpriced_import_wh": "1"}, {"estimated_rate_import_wh": "1"},
    {"net_cost_aud_unrounded": None}, {"import_kwh": None},
])
def test_unfinished_or_unreconciled_accounts_block(changes):
    with pytest.raises(WalletError):
        account_snapshot(session() | changes)


def test_fully_priced_estimated_tariffs_are_disclosed_nonblocking():
    record = session("-0.81") | {
        "import_kwh": .51, "export_kwh": 9.71,
        "estimated_rate_import_wh": "465.2272870908510805",
        "estimated_rate_export_wh": "3755.5841231647186679",
        "quality_flags": ["import:estimated_tariff", "export:estimated_tariff",
                         "interval_energy_allocation_estimated", "not_a_final_bill"],
    }
    frozen = account_snapshot(record)
    assert frozen["net_amount_aud"] == "-0.81"
    assert frozen["quality_flags"] == record["quality_flags"]
    assert record["estimated_rate_export_wh"] == "3755.5841231647186679"


@pytest.mark.parametrize("changes", [
    {"unpriced_import_wh": "0.001"}, {"unpriced_export_wh": "1"},
    {"estimated_rate_import_wh": "-1"}, {"estimated_rate_export_wh": "1"},
    {"estimated_rate_import_wh": "1001"}, {"estimated_rate_import_wh": None},
    {"estimated_rate_import_wh": "NaN"}, {"estimated_rate_import_wh": "Infinity"},
    {"net_cost_aud_unrounded": None},
    {"quality_flags": ["import:missing_tariff", "import:estimated_tariff"]},
    {"quality_flags": ["import:overlapping_tariff_periods", "import:estimated_tariff"]},
    {"quality_flags": ["import:missing_counter_baseline", "import:estimated_tariff"]},
    {"quality_flags": ["new_unknown_flag", "import:estimated_tariff"]},
])
def test_estimate_warning_never_bypasses_incomplete_or_invalid_account(changes):
    record = session() | {"estimated_rate_import_wh": "500",
                          "quality_flags": ["import:estimated_tariff"]}
    with pytest.raises(WalletError):
        account_snapshot(record | changes)


@pytest.mark.asyncio
async def test_credit_requires_separate_exact_approval_and_linked_action(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        source(hass, session("-1.89"))
        review = await api.reviews.prepare(prepare_data(), "admin")
        fields = {"review_id": review["review_id"], "terms_hash": review["terms_hash"], "fee_sats": 10}
        with pytest.raises(WalletError, match="Approve"):
            await api.reviews.prepare_credit(fields)
        await api.reviews.approve(review_approval(review), "admin")
        prepared = await api.reviews.prepare_credit(fields)
        p = prepared["credit_draft"]
        assert p["amount_sats"] == 189 and p["fee_sats"] == 10
        assert not api.chain.posts
        with pytest.raises(WalletError, match="session-linked"):
            await api.broadcast_payment(approval(p), "admin")
        linked = approval(p) | {"review_id": review["review_id"], "terms_hash": review["terms_hash"]}
        with pytest.raises(WalletError, match="amount"):
            await api.reviews.broadcast_credit(linked | {"amount_sats": 190}, "admin")
        sent = await api.reviews.broadcast_credit(linked, "admin")
        assert sent["state"] == "credit_submitted" and len(api.chain.posts) == 1
        restored = MainnetWalletAPI(hass, entry)
        await restored.load()
        restored.chain = api.chain
        again = await restored.reviews.broadcast_credit(linked, "admin")
        assert again["state"] == "credit_submitted" and len(api.chain.posts) == 1
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_credit_uncertainty_survives_restart_and_cannot_repeat(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        source(hass, session("-1.00"))
        r = await api.reviews.prepare(prepare_data(), "admin")
        await api.reviews.approve(review_approval(r), "admin")
        r = await api.reviews.prepare_credit({"review_id": r["review_id"], "terms_hash": r["terms_hash"], "fee_sats": 10})
        fields = approval(r["credit_draft"]) | {"review_id": r["review_id"], "terms_hash": r["terms_hash"]}
        api.chain.fail = True
        with pytest.raises(WalletError, match="uncertain"):
            await api.reviews.broadcast_credit(fields, "admin")
        assert api.reviews.latest()["state"] == "credit_broadcast_unknown"
        api.saved["session_reviews"][r["review_id"]]["expires_at"] = (now()-timedelta(hours=1)).isoformat()
        again = await api.reviews.broadcast_credit(fields, "admin")
        assert again["state"] == "credit_broadcast_unknown" and len(api.chain.posts) == 1
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_changed_account_driver_expiry_zero_and_subsat(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        proxy = source(hass, session())
        r = await api.reviews.prepare(prepare_data(), "admin")
        proxy.data["latest_session"]["net_cost_aud_unrounded"] = "2.00"
        with pytest.raises(WalletError, match="account changed"):
            await api.reviews.approve(review_approval(r), "admin")
        proxy.data["latest_session"]["net_cost_aud_unrounded"] = "1.89"
        await api.set_driver("driver_receive_address", PrivateKey().address())
        with pytest.raises(WalletError, match="Driver details"):
            await api.reviews.approve(review_approval(r), "admin")
        await api.reviews.cancel({"review_id": r["review_id"]})
        r = await api.reviews.prepare(prepare_data(), "admin")
        api.saved["session_reviews"][r["review_id"]]["expires_at"] = (now()-timedelta(seconds=1)).isoformat()
        with pytest.raises(WalletError, match="expired"):
            await api.reviews.approve(review_approval(r), "admin")
        await api.reviews.cancel({"review_id": r["review_id"]})
        proxy.data["latest_session"] = session("0")
        r = await api.reviews.prepare(prepare_data(), "admin")
        r = await api.reviews.approve(review_approval(r), "admin")
        assert r["state"] == "no_payment_due" and not api.chain.posts
        proxy.data["latest_session"] = session(".01", "session-2")
        hass.states.async_set("sensor.demo_rate", "1", {"unit_of_measurement": "sat/AUD"})
        with pytest.raises(WalletError, match="below one"):
            await api.reviews.prepare(prepare_data("session-2"), "admin")
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_driver_receipt_exact_output_dedupe_and_reorg(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        proxy = source(hass, session())
        r = await api.reviews.prepare(prepare_data(), "admin")
        await api.reviews.approve(review_approval(r), "admin")
        tx = Transaction([TransactionInput(source_txid="22"*32)],
                         [TransactionOutput(P2PKH().lock(r["recipient_address"]), satoshis=189)])
        api.chain.request = AsyncMock(return_value=tx.hex())
        fields = {"review_id": r["review_id"], "txid": tx.txid(), "output_index": 0,
                  "confirm_driver_payment_reference": True}
        with pytest.raises(WalletError, match="output"):
            await api.reviews.verify_driver_payment(fields | {"output_index": 1}, "admin")
        rec = await api.reviews.verify_driver_payment(fields, "admin")
        assert rec["state"] == "driver_payment_provider_confirmed"
        assert not api.chain.posts
        api.chain.details = AsyncMock(return_value={"txid": tx.txid(), "confirmations": 0})
        rec = await api.reviews.verify_driver_payment(fields, "admin")
        assert rec["state"] == "driver_payment_provider_unconfirmed"
        api.chain.details.side_effect = WalletError("Simulated provider outage")
        with pytest.raises(WalletError):
            await api.reviews.verify_driver_payment(fields, "admin")
        assert api.reviews.latest()["state"] == "driver_payment_evidence_unavailable"
        api.chain.details.side_effect = None
        proxy.data["latest_session"] = session("1.89", "session-2")
        r2 = await api.reviews.prepare(prepare_data("session-2"), "admin")
        await api.reviews.approve(review_approval(r2), "admin")
        with pytest.raises(WalletError, match="another session"):
            await api.reviews.verify_driver_payment(fields | {"review_id": r2["review_id"]}, "admin")
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_review_services_require_authenticated_admin(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        source(hass, session())
        coord = SettlementCoordinator(hass, entry, api)
        await coord.load()
        await async_setup(hass, {})
        hass.data["bsv_settlement"][entry.entry_id] = coord
        data = prepare_data() | {"config_entry_id": entry.entry_id}
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call("bsv_settlement", "prepare_session_review", data, blocking=True)
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=SimpleNamespace(is_admin=False)))
        with pytest.raises(HomeAssistantError, match="administrator"):
            await hass.services.async_call(
                "bsv_settlement", "prepare_session_review", data, blocking=True,
                context=Context(user_id="not-admin"))
        hass.auth.async_get_user.return_value.is_admin = True
        result = await hass.services.async_call(
            "bsv_settlement", "prepare_session_review", data, blocking=True, return_response=True,
            context=Context(user_id="admin"))
        assert result["amount_sats"] == 189 and not api.chain.posts
        assert coord.data["health"]["latest_session_review"]["review_id"] == result["review_id"]
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_cancel_unsigned_credit_and_requote_without_broadcast(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        source(hass, session("-1.89"))
        r = await api.reviews.prepare(prepare_data(), "admin")
        await api.reviews.approve(review_approval(r), "admin")
        prepared = await api.reviews.prepare_credit({
            "review_id": r["review_id"], "terms_hash": r["terms_hash"], "fee_sats": 10})
        cancelled = await api.reviews.cancel({"review_id": r["review_id"]})
        assert cancelled["state"] == "credit_cancelled"
        hass.states.async_set("sensor.demo_rate", "200", {"unit_of_measurement": "sat/AUD"})
        replacement = await api.reviews.prepare(prepare_data(), "admin")
        assert replacement["review_id"] != r["review_id"]
        assert replacement["amount_sats"] == 378
        assert api.saved["session_reviews"][r["review_id"]]["amount_sats"] == 189
        assert not api.chain.posts
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_review_expiry_during_funding_check_blocks_signing(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        source(hass, session("-1"))
        r = await api.reviews.prepare(prepare_data(), "admin")
        await api.reviews.approve(review_approval(r), "admin")
        r = await api.reviews.prepare_credit({
            "review_id": r["review_id"], "terms_hash": r["terms_hash"], "fee_sats": 10})
        original = api.chain.unspent
        async def expire_during_read(address):
            api.saved["session_reviews"][r["review_id"]]["expires_at"] = (now()-timedelta(seconds=1)).isoformat()
            return await original(address)
        api.chain.unspent = expire_during_read
        with pytest.raises(WalletError, match="expired before signing"):
            await api.reviews.broadcast_credit(
                approval(r["credit_draft"]) | {"review_id": r["review_id"], "terms_hash": r["terms_hash"]}, "admin")
        assert not api.chain.posts
        assert api.saved["payments"][r["credit_draft"]["draft_id"]]["signed_raw"] is None
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_wrong_driver_output_amount_address_and_old_transaction_rejected(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        source(hass, session())
        r = await api.reviews.prepare(prepare_data(), "admin")
        await api.reviews.approve(review_approval(r), "admin")
        for address, amount in [(PrivateKey().address(), 189), (r["recipient_address"], 190)]:
            tx = Transaction([TransactionInput(source_txid="33"*32)],
                             [TransactionOutput(P2PKH().lock(address), satoshis=amount)])
            api.chain.request = AsyncMock(return_value=tx.hex())
            with pytest.raises(WalletError, match="exact amount"):
                await api.reviews.verify_driver_payment({
                    "review_id": r["review_id"], "txid": tx.txid(), "output_index": 0,
                    "confirm_driver_payment_reference": True}, "admin")
        tx = Transaction([TransactionInput(source_txid="44"*32)],
                         [TransactionOutput(P2PKH().lock(r["recipient_address"]), satoshis=189)])
        api.chain.request = AsyncMock(return_value=tx.hex())
        api.chain.details = AsyncMock(return_value={"txid": tx.txid(), "confirmations": 1, "blocktime": 1})
        with pytest.raises(WalletError, match="before the request"):
            await api.reviews.verify_driver_payment({
                "review_id": r["review_id"], "txid": tx.txid(), "output_index": 0,
                "confirm_driver_payment_reference": True}, "admin")
        assert not api.saved["received_outpoints"] and not api.chain.posts
    finally:
        await hass.async_stop(force=True)


@pytest.mark.asyncio
async def test_frontend_registered_once_and_hacs_bundle_matches():
    from pathlib import Path
    import yaml
    from custom_components.bsv_settlement.const import SERVICES
    root = Path(__file__).resolve().parents[1]
    package = root / "custom_components/bsv_settlement"
    assert (package / "frontend/session-review-card.js").read_bytes() == (
        root / "frontend/bsv-session-review-card.bundle.js").read_bytes()
    metadata = yaml.safe_load((package / "services.yaml").read_text())
    assert set(SERVICES) <= set(metadata)
    hass = SimpleNamespace(data={}, bus=SimpleNamespace(async_listen_once=Mock()),
                           http=SimpleNamespace(async_register_static_paths=AsyncMock(), register_view=Mock()),
                           services=SimpleNamespace(async_register=Mock()))
    await async_setup(hass, {})
    await async_setup(hass, {})
    assert hass.http.async_register_static_paths.await_count == 1
