"""Wallet sensors stay under the recorder's attribute limit on a busy wallet."""
import json
from types import SimpleNamespace

from custom_components.bsv_settlement.sensor import (
    BALANCE_KEYS, WALLET_DETAIL_KEYS, SettlementSensor)

RECORDER_LIMIT = 16384


def busy_health():
    """Fictional health shaped like a live mainnet wallet (~50 KB of ledger)."""
    approval = {"session_id": "fictional-session", "state": "approved", "approved": True,
                "satoshis_per_aud": "1234.5678", "note": "x" * 400}
    payment = {"session_id": "fictional-session", "txid": "0" * 64, "amount_sats": 1000,
               "state": "provider_confirmed", "note": "y" * 300}
    return {
        "state": "broadcast_enabled_capped_automatic_credits", "mode": "embedded_mainnet",
        "network": "mainnet", "backend": "bsv-sdk", "broadcast_enabled": True,
        "balance_verified": False, "balance_sats": 4786, "pending_change_sats": 0,
        "last_self_test": {"txid": None, "signed_bytes_sha256": "a" * 64},
        "last_payment": {"draft_id": "fictional", "amount_sats": 1000},
        "operator_public_key": "02" + "b" * 64,
        "driver_approvals": [approval] * 20,
        "session_payments": [payment] * 11,
        "ongoing_credit": {"enabled": True, "effective": True, "sessions": [payment] * 40},
        "closed_sessions": [{"session_id": "fictional", "state": "settled"}] * 15,
        "latest_session_review": {"review_id": "fictional", "detail": "z" * 4000},
        "automatic_credit": {"enabled": True, "payments": [payment] * 20},
    }


def sensor(key, health):
    coordinator = SimpleNamespace(mode="embedded_mainnet", api=SimpleNamespace(network="mainnet"),
                                  data={"health": health})
    entry = SimpleNamespace(entry_id="fictional-entry", title="Fictional")
    return SettlementSensor(coordinator, entry, key, key, None)


def recorded(entity):
    # What the recorder stores: the state attributes minus the entity's
    # combined unrecorded attributes.
    excluded = entity._Entity__combined_unrecorded_attributes
    return {k: v for k, v in entity.extra_state_attributes.items() if k not in excluded}


def test_wallet_status_keeps_ledger_live_but_out_of_the_recorder():
    health = busy_health()
    entity = sensor("operator_wallet_status", health)
    attributes = entity.extra_state_attributes
    assert len(json.dumps(attributes)) > RECORDER_LIMIT
    for key in WALLET_DETAIL_KEYS:
        assert attributes[key] == health[key]
    stored = recorded(entity)
    assert not set(WALLET_DETAIL_KEYS) & set(stored)
    assert len(json.dumps(stored)) < RECORDER_LIMIT
    assert stored["operator_public_key"] == health["operator_public_key"]
    assert stored["last_payment"] == health["last_payment"]
    assert {k: stored[k] for k in (
        "driver_approval_count", "session_payment_count", "closed_session_count",
        "ongoing_credit_session_count", "ongoing_credit_effective",
        "automatic_credit_enabled", "automatic_credit_payment_count")} == {
        "driver_approval_count": 20, "session_payment_count": 11, "closed_session_count": 15,
        "ongoing_credit_session_count": 40, "ongoing_credit_effective": True,
        "automatic_credit_enabled": True, "automatic_credit_payment_count": 20}


def test_balance_sensor_carries_balance_fields_only():
    health = busy_health()
    entity = sensor("confirmed_wallet_balance", health)
    assert entity.native_value == 4786
    assert set(entity.extra_state_attributes) == set(BALANCE_KEYS)
    assert len(json.dumps(recorded(entity))) < 1024


def test_summary_tolerates_missing_or_odd_ledger_fields():
    entity = sensor("operator_wallet_status", {"ongoing_credit": "unexpected",
                                               "automatic_credit": {"payments": None}})
    attributes = entity.extra_state_attributes
    assert attributes["driver_approval_count"] is None
    assert attributes["ongoing_credit_session_count"] is None
    assert attributes["ongoing_credit_effective"] is None
    assert attributes["automatic_credit_payment_count"] is None
    assert attributes["automatic_credit_enabled"] is None
