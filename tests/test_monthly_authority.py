"""S2 offline consent/storage/ownership tests. Fictional wallets and adapters."""
import asyncio
import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bsv import PrivateKey
import pytest

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.budget import message_hash
from custom_components.bsv_settlement.monthly_allowance import Month, AllowanceError
from custom_components.bsv_settlement.monthly_authority import MonthlyAuthorities, decode
from custom_components.bsv_settlement.monthly_consent import (
    WalletPeriodPolicy, approval_payload, cancellation_payload, verify_proof,
)
from custom_components.bsv_settlement.monthly_ownership import (
    KEY, ensure_no_legacy_owner, ensure_no_monthly_owner,
)
from custom_components.bsv_settlement.session_closure import ensure_open
from test_session_review import session

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
DRIVER = PrivateKey(17)
OTHER = PrivateKey(19)
OPERATOR = PrivateKey(23)
POLICY = WalletPeriodPolicy("fixture-utc", "fictional-wallet-1", "UTC",
                           "verified_spend_commit", "all_driver_paid_wallet_debits",
                           "offline-fixture-only")


class MemoryStore:
    def __init__(self):
        self.data = None
        self.fail = False
        self.after_write_failure = False

    async def async_save(self, data):
        await asyncio.sleep(0)
        if self.fail:
            raise OSError("fixture write failure")
        self.data = json.loads(json.dumps(data))
        if self.after_write_failure:
            raise OSError("fixture lost response after write")


def service():
    api = SimpleNamespace(
        saved={}, store=MemoryStore(),
        identity={"public_key": OPERATOR.public_key().hex(), "address": OPERATOR.address()},
        collections=SimpleNamespace(session_id=lambda row: row["terms"].get("session_id")),
    )
    coord = SimpleNamespace(api=api, lock=asyncio.Lock())
    clock = [NOW]

    async def resolver(account, identity):
        return dict(account_key=account, driver_identity=identity, station_id="station-1",
                    transaction_id=account + "-tx", opened_at=NOW.isoformat(),
                    ended_at=None, satoshis_per_aud="100", ownership_evidence="fixture-presence")

    async def grant(terms):
        return dict(driver_identity=terms["driver_identity"], origin=terms["origin"],
                    network="BSV mainnet",
                    policy_id=POLICY.policy_id, monthly_limit_sats=30_000,
                    month=asdict(Month.at(clock[0], wallet_timezone="UTC")),
                    remaining_sats=30_000, observed_at=clock[0].isoformat(),
                    evidence_ref="fixture-native-grant")

    async def record(binding):
        return session("1.00", binding["account_key"].split("|")[1]) | {
            "ocpp_transaction_id": binding["transaction_id"], "opened_at": NOW.isoformat(),
            "ended_at": (NOW + timedelta(seconds=1)).isoformat()}

    svc = MonthlyAuthorities(coord, origin="charging.example", policies={POLICY.policy_id: POLICY},
                             resolve_session=resolver, resolve_record=record, wallet_grant=grant,
                             clock=lambda: clock[0])
    return svc, clock


def revision(svc):
    return svc.snapshot()["revision"]


def proof(terms, key=DRIVER, *, cancel=False):
    payload = cancellation_payload(terms) if cancel else approval_payload(terms)
    child = key.derive_child(PrivateKey(1).public_key(),
                            f"2-ev monthly spending-{terms['authority_id']}")
    return {"payload": payload, "signature": child.sign(payload.encode(), hasher=message_hash).hex()}


async def approved(svc=None, key=DRIVER, included=None):
    svc, clock = (service() if svc is None else (svc, None))
    issued = await svc.issue(driver_identity=key.public_key().hex(), station_ids=["station-1"],
                             policy_id=POLICY.policy_id, expected_revision=revision(svc),
                             included_session=included)
    await svc.accept(issued["terms"]["authority_id"], proof(issued["terms"], key),
                     expected_revision=revision(svc))
    return svc, clock, issued["terms"]


async def bound():
    svc, clock, terms = await approved()
    await svc.bind(terms["authority_id"], "proxy|session", expected_revision=revision(svc))
    clock[0] += timedelta(seconds=1)
    return svc, clock, terms


async def transition(svc, terms, action, **data):
    return await svc.transition(terms["authority_id"], action, data, expected_revision=revision(svc))


async def reserved():
    svc, clock, terms = await bound()
    await transition(svc, terms, "reserve", attempt_id="attempt", account_id="proxy|session",
                     debit_sats=100, fee_reserve_sats=10)
    return svc, clock, terms


async def test_signed_monthly_terms_and_replay_do_not_reset_ledger():
    svc, _, terms = await reserved()
    assert terms["monthly_limit_sats"] == 30_000
    before = copy.deepcopy(svc.api.saved)
    await svc.accept(terms["authority_id"], proof(terms), expected_revision=0)
    assert svc.api.saved == before
    with pytest.raises(WalletError, match="without resetting"):
        await svc.issue(driver_identity=DRIVER.public_key().hex(), station_ids=["station-1"],
                        policy_id=POLICY.policy_id, expected_revision=revision(svc))


async def test_cross_sdk_monthly_signature():
    svc, _ = service()
    issued = await svc.issue(driver_identity=DRIVER.public_key().hex(), station_ids=["station-1"],
                             policy_id=POLICY.policy_id, expected_revision=0)
    script = Path(__file__).parents[1] / "frontend/driver/monthly-cross-sdk.cjs"
    p = await asyncio.create_subprocess_exec("node", str(script), stdin=asyncio.subprocess.PIPE,
                                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate(json.dumps(dict(payload=issued["payload"],
        authority_id=issued["terms"]["authority_id"], secret=DRIVER.hex())).encode())
    assert p.returncode == 0, err.decode()
    await svc.accept(issued["terms"]["authority_id"], json.loads(out), expected_revision=revision(svc))


@pytest.mark.parametrize("field,value", [
    ("version", 3), ("version", True), ("monthly_limit_sats", 30001),
    ("origin", "wrong.example"), ("station_ids", ["station-2"]),
    ("network", "BSV testnet"),
    ("nonce", "0"*64), ("driver_identity", OTHER.public_key().hex()),
    ("operator_identity", OTHER.public_key().hex()),
])
async def test_tampered_terms_reject_signature(field, value):
    svc, _, terms = await approved()
    changed = copy.deepcopy(terms)
    changed[field] = value
    with pytest.raises(WalletError):
        verify_proof(changed, proof(terms))


async def test_wrong_key_and_legacy_protocol_rejected():
    svc, _, terms = await approved()
    with pytest.raises(WalletError):
        verify_proof(terms, proof(terms, OTHER))
    p = approval_payload(terms)
    old = DRIVER.derive_child(PrivateKey(1).public_key(), f"2-ev session spending-{terms['authority_id']}")
    with pytest.raises(WalletError):
        verify_proof(terms, {"payload": p, "signature": old.sign(p.encode(), hasher=message_hash).hex()})


async def test_expired_or_replayed_challenge_cannot_authorise_other_id():
    svc, clock = service()
    row = await svc.issue(driver_identity=DRIVER.public_key().hex(), station_ids=["station-1"],
                          policy_id=POLICY.policy_id, expected_revision=0)
    clock[0] += timedelta(minutes=10)
    with pytest.raises(WalletError, match="expired"):
        await svc.accept(row["terms"]["authority_id"], proof(row["terms"]), expected_revision=revision(svc))
    with pytest.raises(WalletError, match="Unknown"):
        await svc.accept("other-id", proof(row["terms"]), expected_revision=revision(svc))


async def test_no_default_wallet_policy_or_permission():
    svc, _ = service()
    with pytest.raises(WalletError, match="policy"):
        await svc.issue(driver_identity=DRIVER.public_key().hex(), station_ids=["station-1"],
                        policy_id="invented", expected_revision=0)
    svc, _, terms = await bound()
    svc.wallet_grant = None
    with pytest.raises(WalletError, match="permission"):
        await transition(svc, terms, "reserve", attempt_id="a", account_id="proxy|session",
                         debit_sats=100, fee_reserve_sats=0)
    assert not svc.snapshot()["authorities"][terms["authority_id"]]["ledger"]["attempts"]


async def test_concurrent_reservations_share_revision_and_total_limit():
    svc, _, terms = await bound()
    await svc.bind(terms["authority_id"], "proxy|second", expected_revision=revision(svc))
    original_resolver = svc.resolve_record
    async def expensive(binding):
        return await original_resolver(binding) | {"net_cost_aud_unrounded": "200.00"}
    svc.resolve_record = expensive
    rev = revision(svc)
    results = await asyncio.gather(*(
        svc.transition(terms["authority_id"], "reserve",
                       dict(attempt_id=f"a{i}", account_id=key, debit_sats=20_000, fee_reserve_sats=100),
                       expected_revision=rev)
        for i, key in enumerate(("proxy|session", "proxy|second"))
    ), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, (WalletError, AllowanceError)) for r in results) == 1
    row = decode(svc.snapshot()["authorities"][terms["authority_id"]]["ledger"])
    assert row.summary(Month(2026, 10, "UTC")).reserved_sats == 20_100


async def test_restart_restores_consent_binding_and_reservation():
    svc, _, terms = await reserved()
    saved = copy.deepcopy(svc.api.store.data)
    svc.api.saved = saved
    restored = MonthlyAuthorities(svc.coordinator, origin=svc.origin, policies=svc.policies)
    row = restored.snapshot()["authorities"][terms["authority_id"]]
    assert decode(row["ledger"]).attempts[0].reserved_sats == 110
    assert restored.snapshot()["bindings"]["proxy|session"]["satoshis_per_aud"] == "100"


@pytest.mark.parametrize("after_write", [False, True])
async def test_failed_storage_never_grants_next_action(after_write):
    svc, _, terms = await bound()
    original = copy.deepcopy(svc.api.saved)
    svc.api.store.fail = not after_write
    svc.api.store.after_write_failure = after_write
    with pytest.raises(OSError):
        await transition(svc, terms, "reserve", attempt_id="a", account_id="proxy|session",
                         debit_sats=100, fee_reserve_sats=0)
    assert svc.api.saved == original
    with pytest.raises(WalletError, match="uncertain"):
        svc.snapshot()
    if after_write:
        restored_api = SimpleNamespace(**{k: v for k, v in vars(svc.api).items()
                                         if k != "_monthly_write_uncertain"})
        restored_api.saved = svc.api.store.data
        restored_coord = SimpleNamespace(api=restored_api, lock=asyncio.Lock())
        recovered = MonthlyAuthorities(restored_coord, origin=svc.origin, policies=svc.policies)
        assert len(decode(recovered.snapshot()["authorities"][terms["authority_id"]]["ledger"]).attempts) == 1


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(schema="unknown"),
    lambda s: s.update(revision=True),
    lambda s: s["authorities"].clear(),
    lambda s: s["bindings"].clear(),
    lambda s: next(iter(s["authorities"].values()))["ledger"].update(limit_sats=30001),
    lambda s: next(iter(s["authorities"].values()))["proof"].update(signature="00"),
])
async def test_corrupt_state_never_resets_to_empty(mutation):
    svc, _, _ = await reserved()
    mutation(svc.api.saved[KEY])
    with pytest.raises(WalletError, match="store"):
        MonthlyAuthorities(svc.coordinator, origin=svc.origin, policies=svc.policies)


async def test_cancellation_is_signed_stops_new_actions_preserves_reconcile():
    svc, _, terms = await reserved()
    await transition(svc, terms, "wallet_pending", attempt_id="attempt", wallet_action_id="op-1")
    with pytest.raises(WalletError):
        await svc.cancel(terms["authority_id"], proof(terms), expected_revision=revision(svc))
    result = await svc.cancel(terms["authority_id"], proof(terms, cancel=True), expected_revision=revision(svc))
    assert result["wallet_permission_revoked"] == "not_verified"
    with pytest.raises(WalletError, match="cancelled"):
        await transition(svc, terms, "reserve", attempt_id="new", account_id="proxy|session",
                         debit_sats=1, fee_reserve_sats=0)
    await transition(svc, terms, "commit", attempt_id="attempt", actual_spent_sats=105,
                     evidence_ref="fixture-spend", booking_month=asdict(Month(2026, 10, "UTC")))
    assert decode(svc.snapshot()["authorities"][terms["authority_id"]]["ledger"]).cancelled


async def test_month_boundary_does_not_sign_or_move_uncertain_attempt():
    svc, clock, terms = await reserved()
    await transition(svc, terms, "wallet_pending", attempt_id="attempt", wallet_action_id="op")
    await transition(svc, terms, "uncertain", attempt_id="attempt")
    clock[0] = datetime(2026, 11, 1, tzinfo=timezone.utc)
    with pytest.raises(WalletError, match="period mismatch"):
        await transition(svc, terms, "commit", attempt_id="attempt", actual_spent_sats=105,
                         evidence_ref="fixture-spend", booking_month=asdict(Month(2026, 11, "UTC")))
    row = decode(svc.snapshot()["authorities"][terms["authority_id"]]["ledger"])
    assert row.summary(Month(2026, 11, "UTC")).blocked
    assert row.attempts[0].state == "uncertain"


@pytest.mark.parametrize("index", [
    "driver_collection_index", "automatic_credit_index", "session_review_index", "closed_sessions",
])
async def test_legacy_index_prevents_monthly_binding(index):
    svc, _, terms = await approved()
    svc.api.saved[index] = {"proxy|session": "existing-owner"}
    with pytest.raises(WalletError, match="legacy"):
        await svc.bind(terms["authority_id"], "proxy|session", expected_revision=revision(svc))


async def test_monthly_binding_excludes_legacy_closure_and_another_identity():
    svc, _, terms = await bound()
    with pytest.raises(WalletError, match="Monthly authority"):
        ensure_open(svc.api, "proxy|session")
    svc, _, other = await approved(svc, OTHER)
    with pytest.raises(WalletError, match="Another monthly"):
        await svc.bind(other["authority_id"], "proxy|session", expected_revision=revision(svc))
    assert svc.snapshot()["bindings"]["proxy|session"]["authority_id"] == terms["authority_id"]


async def test_ongoing_receiving_route_and_old_invitation_preserved():
    svc, _, _ = await approved()
    svc.api.saved["ongoing_credit_routes"] = {"ongoing:proxy|session": {"recipient": "fixed"}}
    with pytest.raises(WalletError, match="receiving"):
        ensure_no_legacy_owner(svc.api, "proxy|session")
    svc.api.saved["session_budgets"] = {"old": {
        "proxy_config_entry_id": "proxy", "terms": {"session_id": "other"}}}
    with pytest.raises(WalletError, match="invitation"):
        ensure_no_legacy_owner(svc.api, "proxy|other")


async def test_current_session_requires_signed_explicit_inclusion():
    svc, clock = service()
    clock[0] = NOW + timedelta(minutes=1)
    svc, _, terms = await approved(svc)
    with pytest.raises(WalletError, match="Historical"):
        await svc.bind(terms["authority_id"], "proxy|session", expected_revision=revision(svc))
    svc, clock = service()
    clock[0] = NOW + timedelta(minutes=1)
    included = dict(account_key="proxy|session", transaction_id="proxy|session-tx", opened_at=NOW.isoformat())
    svc, _, terms = await approved(svc, included=included)
    await svc.bind(terms["authority_id"], "proxy|session", expected_revision=revision(svc))


async def test_missing_owner_adapter_wrong_owner_and_station_fail_closed():
    svc, _, terms = await approved()
    resolver = svc.resolve_session
    svc.resolve_session = None
    with pytest.raises(WalletError, match="ownership adapter"):
        await svc.bind(terms["authority_id"], "proxy|session", expected_revision=revision(svc))
    original = await resolver("proxy|session", DRIVER.public_key().hex())
    for field, value in (("driver_identity", OTHER.public_key().hex()), ("station_id", "station-2")):
        svc.resolve_session = AsyncMock(return_value=original | {field: value})
        with pytest.raises(WalletError, match="scope mismatch"):
            await svc.bind(terms["authority_id"], "proxy|session", expected_revision=revision(svc))


@pytest.mark.parametrize("field,value", [
    ("driver_identity", OTHER.public_key().hex()), ("origin", "wrong.example"),
    ("network", "BSV testnet"),
    ("remaining_sats", True), ("remaining_sats", 0), ("monthly_limit_sats", 30001),
    ("observed_at", (NOW-timedelta(minutes=1)).isoformat()),
    ("month", dict(year=2026, month=11, timezone="UTC")),
])
async def test_native_grant_mismatch_or_insufficient_prevents_reservation(field, value):
    svc, _, terms = await bound()
    grant = await svc.wallet_grant(terms)
    svc.wallet_grant = AsyncMock(return_value=grant | {field: value})
    with pytest.raises(WalletError):
        await transition(svc, terms, "reserve", attempt_id="a", account_id="proxy|session",
                         debit_sats=100, fee_reserve_sats=0)


async def test_legacy_unchanged_when_monthly_namespace_absent():
    svc, _ = service()
    ensure_no_monthly_owner(svc.api, "any-account")
    ensure_open(svc.api, "any-account")
    assert KEY not in svc.api.saved


async def test_wallet_adapter_await_crossing_month_boundary_is_rechecked():
    svc, clock, terms = await bound()
    old = await svc.wallet_grant(terms)

    async def crossed(_terms):
        clock[0] = datetime(2026, 11, 1, tzinfo=timezone.utc)
        return old

    svc.wallet_grant = crossed
    with pytest.raises(WalletError):
        await transition(svc, terms, "reserve", attempt_id="a", account_id="proxy|session",
                         debit_sats=100, fee_reserve_sats=0)


async def test_failed_store_blocks_a_second_service_over_same_stale_api():
    svc, _, terms = await bound()
    svc.api.store.fail = True
    with pytest.raises(OSError):
        await transition(svc, terms, "reserve", attempt_id="a", account_id="proxy|session",
                         debit_sats=100, fee_reserve_sats=0)
    with pytest.raises(WalletError, match="uncertain"):
        MonthlyAuthorities(svc.coordinator, origin=svc.origin, policies=svc.policies)


async def test_collection_terms_explicitly_require_session_closure_not_month_end():
    svc, _, terms = await bound()
    assert terms["collection_policy"] == "one_final_net_payment_per_session_on_closure"
    original = svc.resolve_record
    record = await original(svc.snapshot()["bindings"]["proxy|session"])
    svc.resolve_record = AsyncMock(return_value=record | {"ended_at": None, "status": "Charging"})
    with pytest.raises(WalletError, match="closed session"):
        await transition(svc, terms, "reserve", attempt_id="a", account_id="proxy|session",
                         debit_sats=100, fee_reserve_sats=0)


async def test_two_sessions_settle_separately_with_one_shared_monthly_authority():
    svc, _, terms = await reserved()
    await transition(svc, terms, "wallet_pending", attempt_id="attempt", wallet_action_id="op-1")
    await transition(svc, terms, "commit", attempt_id="attempt", actual_spent_sats=105,
                     evidence_ref="first-session-payment", booking_month=asdict(Month(2026, 10, "UTC")))
    await svc.bind(terms["authority_id"], "proxy|second", expected_revision=revision(svc))
    await transition(svc, terms, "reserve", attempt_id="second", account_id="proxy|second",
                     debit_sats=100, fee_reserve_sats=10)
    await transition(svc, terms, "wallet_pending", attempt_id="second", wallet_action_id="op-2")
    await transition(svc, terms, "commit", attempt_id="second", actual_spent_sats=107,
                     evidence_ref="second-session-payment", booking_month=asdict(Month(2026, 10, "UTC")))
    ledger = decode(svc.snapshot()["authorities"][terms["authority_id"]]["ledger"])
    assert len(ledger.attempts) == 2
    assert {a.account_id for a in ledger.attempts} == {"proxy|session", "proxy|second"}
    assert ledger.summary(Month(2026, 10, "UTC")).spent_sats == 212
    assert ledger.summary(Month(2026, 10, "UTC")).remaining_sats == 29_788
    with pytest.raises((WalletError, AllowanceError)):
        await transition(svc, terms, "reserve", attempt_id="duplicate", account_id="proxy|session",
                         debit_sats=100, fee_reserve_sats=10)


async def test_changed_session_account_cannot_reprice_reserved_payment():
    svc, _, terms = await reserved()
    original = await svc.resolve_record(svc.snapshot()["bindings"]["proxy|session"])
    svc.resolve_record = AsyncMock(return_value=original | {"net_cost_aud_unrounded": "2.00"})
    with pytest.raises(WalletError, match="Frozen per-session"):
        await transition(svc, terms, "wallet_pending", attempt_id="attempt", wallet_action_id="op")
    assert decode(svc.snapshot()["authorities"][terms["authority_id"]]["ledger"]).attempts[0].state == "reserved"


@pytest.mark.parametrize("amount", ["0", "-1", "NaN"])
async def test_credit_zero_and_invalid_account_never_become_driver_debits(amount):
    svc, _, terms = await bound()
    original = await svc.resolve_record(svc.snapshot()["bindings"]["proxy|session"])
    svc.resolve_record = AsyncMock(return_value=original | {"net_cost_aud_unrounded": amount})
    with pytest.raises(WalletError):
        await transition(svc, terms, "reserve", attempt_id="a", account_id="proxy|session",
                         debit_sats=100, fee_reserve_sats=0)


async def test_stale_revision_never_persists_or_returns_new_authority():
    svc, _ = service()
    with pytest.raises(WalletError, match="revision conflict"):
        await svc.issue(driver_identity=DRIVER.public_key().hex(), station_ids=["station-1"],
                        policy_id=POLICY.policy_id, expected_revision=99)
    assert svc.api.store.data is None and KEY not in svc.api.saved


async def test_real_ha_checkpointed_store_restart_and_split_write(tmp_path, monkeypatch):
    from custom_components.bsv_settlement.coordinator import SettlementCoordinator
    from custom_components.bsv_settlement.mainnet import MainnetWalletAPI
    from test_mainnet import setup_wallet
    monkeypatch.setattr("custom_components.bsv_settlement.mainnet.async_get_clientsession", lambda hass: None)
    hass, entry, api = await setup_wallet(tmp_path)
    try:
        svc, clock = service()
        svc.coordinator = SettlementCoordinator(hass, entry, api)
        svc.api = api
        svc, _, terms = await approved(svc)
        await svc.bind(terms["authority_id"], "proxy|session", expected_revision=revision(svc))
        clock[0] += timedelta(seconds=1)
        await transition(svc, terms, "reserve", attempt_id="a", account_id="proxy|session",
                         debit_sats=100, fee_reserve_sats=10)
        restored_api = MainnetWalletAPI(hass, entry)
        await restored_api.load()
        restored = MonthlyAuthorities(SettlementCoordinator(hass, entry, restored_api),
                                      origin=svc.origin, policies=svc.policies)
        ledger = decode(restored.snapshot()["authorities"][terms["authority_id"]]["ledger"])
        assert ledger.attempts[0].reserved_sats == 110
        assert Path(api.store.ledger.path).stat().st_mode & 0o777 == 0o600
        assert api.chain.posts == []
        # Witness advances but ledger write fails: restore must refuse the split.
        monkeypatch.setattr(api.store.ledger, "async_save", AsyncMock(side_effect=OSError("fixture disk")))
        with pytest.raises(WalletError, match="checkpoint"):
            await transition(svc, terms, "wallet_pending", attempt_id="a", wallet_action_id="op")
        with pytest.raises(WalletError, match="restore"):
            await MainnetWalletAPI(hass, entry).load()
        assert api.chain.posts == []
    finally:
        await hass.async_stop(force=True)


async def test_one_wallet_operation_and_payment_evidence_cannot_cover_two_sessions():
    svc, _, terms = await reserved()
    await transition(svc, terms, "wallet_pending", attempt_id="attempt", wallet_action_id="op-1")
    with pytest.raises(WalletError, match="below"):
        await transition(svc, terms, "commit", attempt_id="attempt", actual_spent_sats=99,
                         evidence_ref="payment-1", booking_month=asdict(Month(2026, 10, "UTC")))
    await transition(svc, terms, "commit", attempt_id="attempt", actual_spent_sats=105,
                     evidence_ref="payment-1", booking_month=asdict(Month(2026, 10, "UTC")))
    await svc.bind(terms["authority_id"], "proxy|second", expected_revision=revision(svc))
    await transition(svc, terms, "reserve", attempt_id="second", account_id="proxy|second",
                     debit_sats=100, fee_reserve_sats=10)
    with pytest.raises(WalletError, match="operation already"):
        await transition(svc, terms, "wallet_pending", attempt_id="second", wallet_action_id="op-1")
    await transition(svc, terms, "wallet_pending", attempt_id="second", wallet_action_id="op-2")
    with pytest.raises(WalletError, match="evidence already"):
        await transition(svc, terms, "commit", attempt_id="second", actual_spent_sats=105,
                         evidence_ref="payment-1", booking_month=asdict(Month(2026, 10, "UTC")))


async def test_idempotent_transition_replay_does_not_persist_or_bump_revision():
    """Issue #104: identical replays are no-ops; real transitions still bump."""
    svc, clock, terms = await bound()
    store, saves = svc.api.store, []
    original = store.async_save

    async def counting(data):
        saves.append(1)
        await original(data)

    store.async_save = counting
    month = asdict(Month.at(clock[0], wallet_timezone="UTC"))
    steps = [
        ("reserve", dict(attempt_id="a", account_id="proxy|session", debit_sats=100, fee_reserve_sats=10)),
        ("wallet_pending", dict(attempt_id="a", wallet_action_id="op-1")),
        ("uncertain", dict(attempt_id="a")),
        ("commit", dict(attempt_id="a", actual_spent_sats=105, evidence_ref="spend", booking_month=month)),
    ]
    for action, data in steps:
        rev = revision(svc)
        first = await transition(svc, terms, action, **data)
        assert first["revision"] == rev + 1 and len(saves) == 1  # Real transition bumps.
        persisted, saved = json.dumps(store.data, sort_keys=True), copy.deepcopy(svc.api.saved)
        again = await transition(svc, terms, action, **data)
        assert again == first and revision(svc) == rev + 1
        assert len(saves) == 1 and json.dumps(store.data, sort_keys=True) == persisted
        assert svc.api.saved == saved
        with pytest.raises(WalletError, match="revision conflict"):  # Stale replay still refused.
            await svc.transition(terms["authority_id"], action, data, expected_revision=rev)
        saves.clear()
    # Another client holding the pre-replay revision can still act.
    held = revision(svc)
    await transition(svc, terms, "commit", **steps[-1][1])
    result = await svc.cancel(terms["authority_id"], proof(terms, cancel=True), expected_revision=held)
    assert result["revision"] == held + 1 and len(saves) == 1


async def test_different_transition_at_stale_revision_is_refused():
    svc, clock, terms = await reserved()
    rev = revision(svc)
    await transition(svc, terms, "reserve", attempt_id="attempt", account_id="proxy|session",
                     debit_sats=100, fee_reserve_sats=10)
    assert revision(svc) == rev  # Replay was a no-op.
    await transition(svc, terms, "wallet_pending", attempt_id="attempt", wallet_action_id="op-1")
    with pytest.raises(WalletError, match="revision conflict"):
        await svc.transition(terms["authority_id"], "uncertain", {"attempt_id": "attempt"},
                             expected_revision=rev)
    attempt, = decode(svc.snapshot()["authorities"][terms["authority_id"]]["ledger"]).attempts
    assert attempt.state == "wallet_pending"
