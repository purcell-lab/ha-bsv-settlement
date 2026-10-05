"""S5 integrated failure matrix through the authenticated portal. Offline fixtures only.

Each test names its case from docs/station-first-implementation.md. Cases that
need a native wallet or device are listed in docs/monthly-release.md as pending.
"""
import asyncio
import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.monthly_allowance import Month
from custom_components.bsv_settlement.monthly_authority import MonthlyAuthorities
from custom_components.bsv_settlement.monthly_ownership import KEY as LEDGER, ensure_no_monthly_owner
from custom_components.bsv_settlement.monthly_portal import KEY, MonthlyPortal
from custom_components.bsv_settlement.portal import KEY as PORTAL
from test_portal import fixture, login, post
from test_budget import no_network  # noqa: F401  (autouse network stub)
from test_monthly_portal import POLICY, sign, call
from test_session_review import session

pytestmark = pytest.mark.asyncio
ACCOUNT = "proxy|session"


def activate(hass, coord, clock, *, remaining=30_000, net="1.00"):
    """Reviewed-activation stand-in with every adapter supplied (fixtures only)."""
    async def resolver(account, identity):
        return dict(account_key=account, driver_identity=identity, station_id="station-1",
                    transaction_id=account + "-tx", opened_at=clock[0].isoformat(), ended_at=None,
                    satoshis_per_aud="100", ownership_evidence="fixture-presence")

    async def record(binding):
        return session(net, binding["account_key"].split("|")[1]) | {
            "ocpp_transaction_id": binding["transaction_id"], "opened_at": binding["opened_at"],
            "ended_at": (datetime.fromisoformat(binding["opened_at"]) + timedelta(seconds=1)).isoformat()}

    async def grant(terms):
        return dict(driver_identity=terms["driver_identity"], origin=terms["origin"], network="BSV mainnet",
                    policy_id=POLICY.policy_id, monthly_limit_sats=30_000,
                    month=asdict(Month.at(clock[0], wallet_timezone="UTC")), remaining_sats=remaining,
                    observed_at=clock[0].isoformat(), evidence_ref="fixture-native-grant")

    service = MonthlyAuthorities(coord, origin="charging.example.com", policies={POLICY.policy_id: POLICY},
                                 resolve_session=resolver, resolve_record=record, wallet_grant=grant,
                                 clock=lambda: clock[0])
    hass.data[KEY] = MonthlyPortal(service, ("station-1",), POLICY.policy_id)
    return service


async def ready(tmp_path, **kwargs):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    coord = hass.data["bsv_settlement"]["wallet"]
    clock = [datetime.now(timezone.utc)]
    service = activate(hass, coord, clock, **kwargs)
    cookie, _, _ = await login(client, view, driver)
    _, status = await call(client, view, cookie, "monthly_status")
    _, challenge = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
    code, _ = await call(client, view, cookie, "monthly_accept", authority_id=challenge["authority_id"],
                         proof=sign(challenge["payload"], challenge["keyID"], driver), revision=challenge["revision"])
    assert code == 200
    return hass, api, driver, view, client, cookie, service, clock, challenge


async def spend(service, clock, authority_id, *, attempt="a1", account=ACCOUNT, fee=10, state="reserve"):
    """Internal reconciler path (never a browser route): bind, reserve, optionally invoke."""
    rev = lambda: service.snapshot()["revision"]
    await service.bind(authority_id, account, expected_revision=rev())
    clock[0] += timedelta(seconds=2)
    await service.transition(authority_id, "reserve", dict(attempt_id=attempt, account_id=account,
                             debit_sats=100, fee_reserve_sats=fee), expected_revision=rev())
    if state in ("wallet_pending", "uncertain"):
        await service.transition(authority_id, "wallet_pending", dict(attempt_id=attempt,
                                 wallet_action_id="op-" + attempt), expected_revision=rev())
    if state == "uncertain":
        await service.transition(authority_id, "uncertain", dict(attempt_id=attempt), expected_revision=rev())


async def test_case1_expired_login_blocks_monthly_and_returning_driver_sees_authority(tmp_path):
    hass, api, driver, view, client, cookie, service, clock, challenge = await ready(tmp_path)
    try:
        for item in hass.data[PORTAL].sessions.values():
            item["deadline"] = 0  # Portal login expired.
        assert (await post(client, view, "monthly_status", cookie)).status == 401
        cookie, _, _ = await login(client, view, driver)  # Returning driver signs in again.
        _, status = await call(client, view, cookie, "monthly_status")
        assert status["authority"]["authority_id"] == challenge["authority_id"]
        assert status["authority"]["state"] == "active" and status["readiness"]["automatic_collection"]
    finally:
        await client.close()


async def test_case3_browser_closed_after_signing_replays_safely_and_expired_request_refused(tmp_path):
    hass, api, driver, view, client, cookie, service, clock, challenge = await ready(tmp_path)
    try:
        before = copy.deepcopy(api.saved[LEDGER])
        # The browser closed before seeing the response and resubmits the same proof.
        code, body = await call(client, view, cookie, "monthly_accept", authority_id=challenge["authority_id"],
                                proof=sign(challenge["payload"], challenge["keyID"], driver),
                                revision=service.snapshot()["revision"])
        assert code == 200 and body["accepted"] and api.saved[LEDGER] == before
        other = PrivateKey(4242)
        other_cookie, _, _ = await login(client, view, other)
        _, fresh = await call(client, view, other_cookie, "monthly_challenge", revision=service.snapshot()["revision"])
        clock[0] += timedelta(minutes=11)  # Wallet prompt left open past the window.
        code, body = await call(client, view, other_cookie, "monthly_accept", authority_id=fresh["authority_id"],
                                proof=sign(fresh["payload"], fresh["keyID"], other), revision=fresh["revision"])
        assert code == 409 and body["code"] == "expired"
        # The expired request is pruned on the next challenge and a new one is issued.
        _, again = await call(client, view, other_cookie, "monthly_challenge", revision=service.snapshot()["revision"])
        assert again["authority_id"] != fresh["authority_id"]
        assert fresh["authority_id"] not in service.snapshot()["challenges"]
        assert challenge["authority_id"] in service.snapshot()["challenges"]  # Used: kept as evidence.
    finally:
        await client.close()


async def test_case4_two_tabs_racing_one_wins_and_ledger_stays_consistent(tmp_path):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    activate(hass, hass.data["bsv_settlement"]["wallet"], [datetime.now(timezone.utc)])
    try:
        tab1, _, _ = await login(client, view, driver)
        tab2, _, _ = await login(client, view, driver)
        _, status = await call(client, view, tab1, "monthly_status")
        results = await asyncio.gather(*(call(client, view, tab, "monthly_challenge", revision=status["revision"])
                                         for tab in (tab1, tab2)))
        codes = sorted(code for code, _ in results)
        # Either the second tab conflicts, or it reuses the first tab's open request.
        assert codes in ([200, 200], [200, 409])
        ids = {body["authority_id"] for code, body in results if code == 200}
        assert len(ids) == 1 and len(hass.data[KEY].service.snapshot()["challenges"]) == 1
    finally:
        await client.close()


async def test_case5_observed_overrun_is_shown_as_review_not_ready(tmp_path):
    hass, api, driver, view, client, cookie, service, clock, challenge = await ready(tmp_path)
    try:
        aid = challenge["authority_id"]
        await spend(service, clock, aid, state="wallet_pending")
        await service.transition(aid, "commit", dict(attempt_id="a1", actual_spent_sats=150, evidence_ref="tx-1",
                                 booking_month=asdict(Month.at(clock[0], wallet_timezone="UTC"))),
                                 expected_revision=service.snapshot()["revision"])
        _, status = await call(client, view, cookie, "monthly_status")
        assert status["allowance"]["spent_sats"] == 150 and status["allowance"]["blocked"]
        assert "fee_or_spend_overrun_requires_review" in status["allowance"]["reasons"]
        assert status["readiness"] == {"automatic_collection": False, "missing": ["allowance_review"]}
    finally:
        await client.close()


async def test_case5_fee_inclusive_cap_against_native_remaining(tmp_path):
    hass, api, driver, view, client, cookie, service, clock, challenge = await ready(tmp_path, remaining=109)
    try:
        with pytest.raises(WalletError, match="insufficient"):
            await spend(service, clock, challenge["authority_id"], fee=10)  # 100 + 10 > 109
        assert all(not row["ledger"]["attempts"] for row in service.snapshot()["authorities"].values())
    finally:
        await client.close()


@pytest.mark.parametrize("net", ["-1.00", "0"])
async def test_case6_credit_and_zero_sessions_never_enter_the_debit_route(tmp_path, net):
    hass, api, driver, view, client, cookie, service, clock, challenge = await ready(tmp_path, net=net)
    try:
        with pytest.raises(WalletError, match="separate credit or zero-balance route"):
            await spend(service, clock, challenge["authority_id"])
        _, status = await call(client, view, cookie, "monthly_status")
        assert status["allowance"]["reserved_sats"] == 0 and status["allowance"]["spent_sats"] == 0
    finally:
        await client.close()


async def test_case7_prior_month_uncertain_attempt_blocks_new_month_and_is_visible(tmp_path):
    hass, api, driver, view, client, cookie, service, clock, challenge = await ready(tmp_path)
    try:
        await spend(service, clock, challenge["authority_id"], state="uncertain")
        first = Month.at(clock[0], wallet_timezone="UTC")
        clock[0] = datetime(first.year + (first.month == 12), first.month % 12 + 1, 1, 0, 0, 5, tzinfo=timezone.utc)
        _, status = await call(client, view, cookie, "monthly_status")
        assert status["allowance"]["month"] != asdict(first)
        assert status["allowance"]["blocked"]
        assert "cross_period_attempt_unresolved" in status["allowance"]["reasons"]
        assert status["readiness"]["automatic_collection"] is False
        attempt = service.snapshot()["authorities"][challenge["authority_id"]]["ledger"]["attempts"][0]
        assert attempt["state"] == "uncertain" and attempt["month"] == asdict(first)  # Never moved.
    finally:
        await client.close()


async def test_case8_cancellation_after_submission_keeps_the_attempt_recoverable(tmp_path):
    hass, api, driver, view, client, cookie, service, clock, challenge = await ready(tmp_path)
    try:
        aid = challenge["authority_id"]
        await spend(service, clock, aid, state="wallet_pending")
        _, request = await call(client, view, cookie, "monthly_cancel_challenge", revision=0)
        code, body = await call(client, view, cookie, "monthly_cancel",
                                proof=sign(request["payload"], request["keyID"], driver), revision=request["revision"])
        assert code == 200 and body["wallet_permission_revoked"] == "not_verified"
        _, status = await call(client, view, cookie, "monthly_status")
        assert status["authority"]["state"] == "cancelled" and status["allowance"]["reserved_sats"] == 110
        # The reconciler can still resolve the in-flight payment after cancellation.
        await service.transition(aid, "commit", dict(attempt_id="a1", actual_spent_sats=105, evidence_ref="tx-1",
                                 booking_month=asdict(Month.at(clock[0], wallet_timezone="UTC"))),
                                 expected_revision=service.snapshot()["revision"])
        with pytest.raises(WalletError, match="cancelled"):
            await spend(service, clock, aid, attempt="a2", account="proxy|second")
    finally:
        await client.close()


async def test_case10_restart_after_each_transition_restores_identical_status(tmp_path):
    hass, api, driver, view, client, cookie, service, clock, challenge = await ready(tmp_path)
    try:
        aid = challenge["authority_id"]
        rev = lambda: service.snapshot()["revision"]
        steps = [
            lambda: service.bind(aid, ACCOUNT, expected_revision=rev()),
            lambda: service.transition(aid, "reserve", dict(attempt_id="a1", account_id=ACCOUNT, debit_sats=100,
                                       fee_reserve_sats=10), expected_revision=rev()),
            lambda: service.transition(aid, "wallet_pending", dict(attempt_id="a1", wallet_action_id="op-1"),
                                       expected_revision=rev()),
            lambda: service.transition(aid, "uncertain", dict(attempt_id="a1"), expected_revision=rev()),
        ]
        for index, step in enumerate(steps):
            if index == 1:
                clock[0] += timedelta(seconds=2)
            await step()
            _, before = await call(client, view, cookie, "monthly_status")
            # Restart: a new service instance over what the store holds on disk.
            stored = json.loads(json.dumps(api.saved))
            api.saved = stored
            service = activate(hass, hass.data["bsv_settlement"]["wallet"], clock)
            _, after = await call(client, view, cookie, "monthly_status")
            before["native_grant"].pop("observed_at", None)
            after["native_grant"].pop("observed_at", None)
            assert after == before, f"status changed across restart after step {index}"
    finally:
        await client.close()


async def test_case14_deactivation_keeps_holds_and_never_reenables_legacy_routes(tmp_path):
    hass, api, driver, view, client, cookie, service, clock, challenge = await ready(tmp_path)
    try:
        await spend(service, clock, challenge["authority_id"], state="uncertain")
        ledger = copy.deepcopy(api.saved[LEDGER])
        del hass.data[KEY]  # Operator disables the monthly feature.
        code, status = await call(client, view, cookie, "monthly_status")
        assert code == 200 and status["enabled"] is False
        code, body = await call(client, view, cookie, "monthly_challenge", revision=0)
        assert code == 409 and body["code"] == "disabled"
        assert api.saved[LEDGER] == ledger  # Holds and evidence retained untouched.
        with pytest.raises(WalletError, match="Monthly authority already owns"):
            ensure_no_monthly_owner(api, ACCOUNT)  # Legacy collection stays excluded.
        assert (await post(client, view, "sessions", cookie)).status == 200
    finally:
        await client.close()
