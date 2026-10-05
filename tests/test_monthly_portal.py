"""S3 monthly portal transport. Fictional wallets, adapters and HTTP only."""
import asyncio
import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement.budget import message_hash
from custom_components.bsv_settlement.monthly_allowance import Month
from custom_components.bsv_settlement.monthly_authority import MonthlyAuthorities
from custom_components.bsv_settlement.monthly_consent import WalletPeriodPolicy
from custom_components.bsv_settlement.monthly_ownership import KEY as LEDGER
from custom_components.bsv_settlement.monthly_portal import KEY, MonthlyPortal, REFUSALS
from test_portal import fixture, login, post
from test_budget import no_network  # noqa: F401  (autouse network stub)

pytestmark = pytest.mark.asyncio
POLICY = WalletPeriodPolicy("fixture-utc", "fictional-wallet-1", "UTC",
                           "verified_spend_commit", "all_driver_paid_wallet_debits",
                           "offline-fixture-only")


def activate(hass, api, coord, *, origin="charging.example.com", grant=True):
    """Test-only stand-in for the reviewed S5 activation."""
    async def wallet_grant(terms):
        if not grant:
            raise RuntimeError("no adapter evidence")
        now = datetime.now(timezone.utc)
        return dict(driver_identity=terms["driver_identity"], origin=terms["origin"],
                    network="BSV mainnet", policy_id=POLICY.policy_id, monthly_limit_sats=30_000,
                    month=asdict(Month.at(now, wallet_timezone="UTC")), remaining_sats=29_000,
                    observed_at=now.isoformat(), evidence_ref="fixture-native-grant")

    service = MonthlyAuthorities(coord, origin=origin, policies={POLICY.policy_id: POLICY},
                                 wallet_grant=wallet_grant)
    hass.data[KEY] = MonthlyPortal(service, ("station-1",), POLICY.policy_id)
    return service


def sign(payload, authority_id, key):
    child = key.derive_child(PrivateKey(1).public_key(), f"2-ev monthly spending-{authority_id}")
    return {"payload": payload, "signature": child.sign(payload.encode(), hasher=message_hash).hex()}


async def setup(tmp_path, **kwargs):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    coord = hass.data["bsv_settlement"]["wallet"]
    service = activate(hass, api, coord, **kwargs)
    cookie, _, _ = await login(client, view, driver)
    return hass, api, driver, view, client, cookie, service


async def call(client, view, cookie, action, **data):
    r = await post(client, view, action, cookie, **data)
    return r.status, await r.json()


async def authorise(client, view, cookie, driver):
    _, status = await call(client, view, cookie, "monthly_status")
    code, challenge = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
    assert code == 200
    code, body = await call(client, view, cookie, "monthly_accept", authority_id=challenge["authority_id"],
                            proof=sign(challenge["payload"], challenge["keyID"], driver),
                            revision=challenge["revision"])
    assert code == 200 and body["accepted"] is True
    return challenge


async def test_disabled_by_default_is_read_only_and_keeps_sign_in(tmp_path):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    try:
        cookie, _, _ = await login(client, view, driver)
        before = copy.deepcopy(api.saved)
        code, body = await call(client, view, cookie, "monthly_status")
        assert code == 200 and body == {"enabled": False, "readiness": {
            "automatic_collection": False, "missing": ["monthly_disabled"]}}
        for action in ("monthly_challenge", "monthly_accept", "monthly_cancel_challenge", "monthly_cancel"):
            code, body = await call(client, view, cookie, action, revision=0)
            assert code == 409 and body == {"error": REFUSALS["disabled"], "code": "disabled"}
        assert api.saved == before and LEDGER not in api.saved
        assert (await post(client, view, "sessions", cookie)).status == 200  # Still signed in.
    finally:
        await client.close()


async def test_monthly_actions_require_sign_in(tmp_path):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    activate(hass, api, hass.data["bsv_settlement"]["wallet"])
    try:
        r = await post(client, view, "challenge")
        anonymous = r.cookies["__Host-bsv_driver_portal"].value
        for action in ("monthly_status", "monthly_challenge", "monthly_accept", "monthly_cancel"):
            assert (await post(client, view, action, None, revision=0)).status == 401
            assert (await post(client, view, action, anonymous, revision=0)).status == 401
        assert LEDGER not in api.saved
    finally:
        await client.close()


async def test_one_authority_status_projection_and_reused_challenge(tmp_path):
    hass, api, driver, view, client, cookie, service = await setup(tmp_path)
    try:
        code, status = await call(client, view, cookie, "monthly_status")
        assert code == 200 and status["enabled"] and status["authority"] is None
        assert status["receiving"] == {"registered": True, "routes_new_credits": True}
        assert status["readiness"] == {"automatic_collection": False, "missing": ["monthly_authority"]}
        _, first = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
        _, again = await call(client, view, cookie, "monthly_challenge", revision=first["revision"])
        assert again["authority_id"] == first["authority_id"]  # No storage growth per click.
        assert len(service.snapshot()["challenges"]) == 1
        terms = first["terms"]
        assert first["protocolID"] == [2, "ev monthly spending"] and first["keyID"] == terms["authority_id"]
        assert terms["driver_identity"] == driver.public_key().hex()
        assert terms["origin"] == "charging.example.com" and terms["station_ids"] == ["station-1"]
        assert terms["monthly_limit_sats"] == 30_000 and terms["included_session"] is None
        code, _ = await call(client, view, cookie, "monthly_accept", authority_id=first["authority_id"],
                             proof=sign(first["payload"], first["keyID"], driver), revision=first["revision"])
        assert code == 200
        _, status = await call(client, view, cookie, "monthly_status")
        assert status["authority"]["state"] == "active"
        assert status["allowance"]["limit_sats"] == 30_000 and status["allowance"]["remaining_sats"] == 30_000
        assert status["native_grant"]["state"] == "verified" and status["native_grant"]["remaining_sats"] == 29_000
        assert status["readiness"] == {"automatic_collection": True, "missing": []}
        for forbidden in ("proof", "signature", "nonce", "evidence_ref", "bindings", "ledger"):
            assert forbidden not in json.dumps(status)
        code, body = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
        assert code == 409 and body["code"] == "exists"
    finally:
        await client.close()


async def test_honest_readiness_without_native_grant_evidence(tmp_path):
    hass, api, driver, view, client, cookie, service = await setup(tmp_path, grant=False)
    try:
        await authorise(client, view, cookie, driver)
        _, status = await call(client, view, cookie, "monthly_status")
        assert status["authority"]["state"] == "active"
        assert status["native_grant"] == {"state": "unverified"}
        assert status["readiness"] == {"automatic_collection": False, "missing": ["wallet_monthly_permission"]}
    finally:
        await client.close()


async def test_other_driver_cannot_see_or_accept_and_stale_tab_conflicts(tmp_path):
    hass, api, driver, view, client, cookie, service = await setup(tmp_path)
    other = PrivateKey(991)
    try:
        _, status = await call(client, view, cookie, "monthly_status")
        _, challenge = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
        other_cookie, _, _ = await login(client, view, other)
        _, theirs = await call(client, view, other_cookie, "monthly_status")
        assert theirs["authority"] is None and theirs["challenge_pending"] is False
        assert theirs["receiving"] == {"registered": False, "routes_new_credits": False}
        assert "receiving_registration" in theirs["readiness"]["missing"]
        before = copy.deepcopy(api.saved)
        for key in (driver, other):  # Neither a valid nor a foreign signature helps.
            code, body = await call(client, view, other_cookie, "monthly_accept",
                                    authority_id=challenge["authority_id"],
                                    proof=sign(challenge["payload"], challenge["keyID"], key),
                                    revision=challenge["revision"])
            assert code == 409 and body["code"] == "expired"
        assert api.saved == before
        # A stale second tab cannot overwrite the first tab's newer state.
        code, body = await call(client, view, other_cookie, "monthly_challenge", revision=0)
        assert code == 409 and body["code"] == "revision_conflict"
        code, body = await call(client, view, cookie, "monthly_accept", authority_id=challenge["authority_id"],
                                proof=sign(challenge["payload"], challenge["keyID"], driver), revision=0)
        assert code == 409 and body["code"] == "revision_conflict"
        assert (await post(client, view, "sessions", cookie)).status == 200
    finally:
        await client.close()


@pytest.mark.parametrize("action", ["bind", "reserve", "wallet_pending", "uncertain", "commit",
                                    "release_uninvoked", "monthly_commit", "monthly_transition",
                                    "monthly_bind", "monthly_reserve"])
async def test_accounting_transitions_are_not_browser_operations(tmp_path, action):
    hass, api, driver, view, client, cookie, service = await setup(tmp_path)
    try:
        challenge = await authorise(client, view, cookie, driver)
        before = copy.deepcopy(api.saved)
        r = await post(client, view, action, cookie, authority_id=challenge["authority_id"],
                       attempt_id="a", account_id="proxy|session", actual_spent_sats=1,
                       evidence_ref="browser", revision=service.snapshot()["revision"])
        assert r.status == 401
        assert api.saved == before
    finally:
        await client.close()


async def test_cancellation_is_wallet_signed_and_reports_native_revocation_unverified(tmp_path):
    hass, api, driver, view, client, cookie, service = await setup(tmp_path)
    try:
        await authorise(client, view, cookie, driver)
        _, request = await call(client, view, cookie, "monthly_cancel_challenge", revision=0)
        assert json.loads(request["payload"])["action"] == "cancel_monthly_charging"
        code, body = await call(client, view, cookie, "monthly_cancel",
                                proof=sign(request["payload"], request["keyID"], PrivateKey(5)),
                                revision=request["revision"])
        assert code == 409 and body["code"] == "invalid_proof"
        code, body = await call(client, view, cookie, "monthly_cancel",
                                proof=sign(request["payload"], request["keyID"], driver),
                                revision=request["revision"])
        assert code == 200 and body["cancelled"] and body["wallet_permission_revoked"] == "not_verified"
        _, status = await call(client, view, cookie, "monthly_status")
        assert status["authority"]["state"] == "cancelled"
        assert status["authority"]["wallet_permission_revoked"] == "not_verified"
        assert status["native_grant"] == {"state": "not_applicable"}
        assert status["readiness"]["automatic_collection"] is False
        code, body = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
        assert code == 409 and body["code"] == "cancelled"
    finally:
        await client.close()


async def test_activation_for_another_origin_or_wallet_stays_disabled(tmp_path):
    hass, api, driver, view, client, cookie, service = await setup(tmp_path, origin="other.example.com")
    try:
        _, status = await call(client, view, cookie, "monthly_status")
        assert status["enabled"] is False
    finally:
        await client.close()


async def test_failed_durable_write_blocks_without_signing_out(tmp_path):
    hass, api, driver, view, client, cookie, service = await setup(tmp_path)
    original = api.store.async_save

    async def fail(data):
        raise OSError("fixture write failure")
    try:
        _, status = await call(client, view, cookie, "monthly_status")
        api.store.async_save = fail
        code, body = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
        assert code == 409 and body["code"] == "unavailable"
        api.store.async_save = original
        code, body = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
        assert code == 409 and body["code"] == "unavailable"  # Blocked until reload.
        assert LEDGER not in api.saved
        assert (await post(client, view, "sessions", cookie)).status == 200
    finally:
        await client.close()


async def test_typescript_sdk_signature_accepted_over_http(tmp_path):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    activate(hass, api, hass.data["bsv_settlement"]["wallet"])
    try:
        cookie, _, _ = await login(client, view, driver)
        _, status = await call(client, view, cookie, "monthly_status")
        _, challenge = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
        script = Path(__file__).parents[1] / "frontend/driver/monthly-cross-sdk.cjs"
        p = await asyncio.create_subprocess_exec("node", str(script), stdin=asyncio.subprocess.PIPE,
                                                 stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await p.communicate(json.dumps({"payload": challenge["payload"],
            "authority_id": challenge["authority_id"], "secret": driver.hex()}).encode())
        assert p.returncode == 0, err.decode()
        code, body = await call(client, view, cookie, "monthly_accept", authority_id=challenge["authority_id"],
                                proof=json.loads(out), revision=challenge["revision"])
        assert code == 200 and body["accepted"] is True
    finally:
        await client.close()


async def test_browser_validator_accepts_python_terms_and_its_signature_verifies(tmp_path):
    """The shipped JS validator/signer against genuine Python-issued terms, over HTTP."""
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    activate(hass, api, hass.data["bsv_settlement"]["wallet"])
    try:
        cookie, _, _ = await login(client, view, driver)
        _, status = await call(client, view, cookie, "monthly_status")
        _, challenge = await call(client, view, cookie, "monthly_challenge", revision=status["revision"])
        script = Path(__file__).parents[1] / "frontend/driver/monthly-cross-portal.mjs"

        async def run(item, stations):
            p = await asyncio.create_subprocess_exec(
                "node", str(script), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, err = await p.communicate(json.dumps({"challenge": item, "hostname": "charging.example.com",
                                                       "stations": stations, "secret": driver.hex()}).encode())
            return p.returncode, out, err
        tampered = copy.deepcopy(challenge)
        tampered["terms"]["monthly_limit_sats"] = 30_001
        for item, stations in ((tampered, ["station-1"]), (challenge, ["station-2"])):
            code, _, err = await run(item, stations)
            assert code != 0 and b"did not match" in err
        code, out, err = await run(challenge, ["station-1"])
        assert code == 0, err.decode()
        code, body = await call(client, view, cookie, "monthly_accept", authority_id=challenge["authority_id"],
                                proof=json.loads(out), revision=challenge["revision"])
        assert code == 200 and body["accepted"] is True
    finally:
        await client.close()


async def test_public_station_facts_need_no_sign_in_and_reveal_nothing_private(tmp_path):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    try:
        r = await post(client, view, "station")
        assert r.status == 200 and await r.json() == {"monthly_enabled": False, "station_ids": []}
        assert "Set-Cookie" not in r.headers
        activate(hass, api, hass.data["bsv_settlement"]["wallet"])
        body = await (await post(client, view, "station")).json()
        assert body == {"monthly_enabled": True, "station_ids": ["station-1"],
                        "operator_identity": api.identity["public_key"], "monthly_limit_sats": 30_000}
        assert driver.public_key().hex() not in json.dumps(body)
    finally:
        await client.close()
