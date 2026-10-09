"""Per-request wallet signatures on state-changing portal actions. No live network."""
import copy
import json

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement import portal_debits, portal_registration, portal_requests
from custom_components.bsv_settlement.budget import message_hash
from test_budget import no_network  # noqa: F401  Autouse: no live provider sessions.
from test_portal import ORIGIN, fixture, login, post

pytestmark = pytest.mark.asyncio


def sign(grant, driver, request, **override):
    p = {"version": 1, "action": "signed_driver_portal_request", "origin": ORIGIN,
         "identity": grant["identity"], "browser_binding": grant["browser_binding"],
         "nonce": grant["nonce"], "issued_at": grant["issued_at"], "expires_at": grant["expires_at"],
         "request": request} | override
    payload = json.dumps(p)
    key = driver.derive_child(PrivateKey(1).public_key(), f"2-{portal_requests.PROTOCOL}-{grant['nonce']}")
    return {"payload": payload, "signature": key.sign(payload.encode(), hasher=message_hash).hex()}


async def grant(client, view, cookie):
    r = await post(client, view, "request_nonce", cookie)
    assert r.status == 200
    return await r.json()


@pytest.fixture
def recorded(monkeypatch):
    seen = []

    async def debits(api, identity, data):
        seen.append(("debit", identity, data))
        return {"ok": True}

    async def registration(api, identity, data):
        seen.append(("registration", identity, data))
        return {"ok": True}
    monkeypatch.setattr(portal_debits, "handle", debits)
    monkeypatch.setattr(portal_registration, "handle", registration)
    return seen


@pytest.mark.parametrize("action", portal_requests.SIGNED_ACTIONS)
async def test_cookie_alone_never_reaches_a_signed_action(tmp_path, recorded, action):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path)
    try:
        cookie, _, _ = await login(client, view, driver)
        before = copy.deepcopy(api.saved)
        r = await post(client, view, action, cookie, budget_id="b", session_id="s", attempt_token="t")
        assert r.status == 403 and (await r.json())["code"] == "request_signature_required"
        assert recorded == [] and api.saved == before
        # A refused request is not a sign-out.
        assert (await post(client, view, "sessions", cookie)).status == 200
    finally:
        await client.close()


async def test_signed_request_delivers_exactly_the_signed_body(tmp_path, recorded):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path)
    try:
        cookie, _, _ = await login(client, view, driver)
        body = {"action": "debit_authorise", "budget_id": "b", "session_id": "s",
                "attempt_token": "t", "draft": {"version": 1, "locktime": 0, "inputs": [], "outputs": []}}
        g = await grant(client, view, cookie)
        assert g["identity"] == driver.public_key().hex() and g["keyID"] == g["nonce"]
        assert g["protocolID"] == [2, portal_requests.PROTOCOL]
        r = await post(client, view, "debit_authorise", cookie, signed_request=sign(g, driver, body))
        assert r.status == 200
        assert recorded == [("debit", driver.public_key().hex(), body)]
        g = await grant(client, view, cookie)
        offer = {"action": "registration_offer"}
        r = await post(client, view, "registration_offer", cookie, signed_request=sign(g, driver, offer))
        assert r.status == 200 and recorded[-1] == ("registration", driver.public_key().hex(), offer)
    finally:
        await client.close()


async def test_replay_tamper_wrong_wallet_action_and_extra_fields_are_refused(tmp_path, recorded):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path)
    try:
        cookie, _, _ = await login(client, view, driver)
        body = {"action": "debit_failure", "budget_id": "b", "session_id": "s", "attempt_token": "t"}

        async def refused(action, signed_request, **extra):
            r = await post(client, view, action, cookie, signed_request=signed_request, **extra)
            assert r.status == 403, await r.text()

        g = await grant(client, view, cookie)
        signed = sign(g, driver, body)
        assert (await post(client, view, "debit_failure", cookie, signed_request=signed)).status == 200
        await refused("debit_failure", signed)  # Replay of a consumed nonce.

        g = await grant(client, view, cookie)
        tampered = sign(g, driver, body)
        tampered["payload"] = tampered["payload"].replace('"attempt_token": "t"', '"attempt_token": "u"')
        await refused("debit_failure", tampered)
        # The nonce was consumed by the failed attempt; a correct retry with it also fails.
        await refused("debit_failure", sign(g, driver, body))

        g = await grant(client, view, cookie)
        await refused("debit_failure", sign(g, PrivateKey(999), body))  # Different wallet.
        g = await grant(client, view, cookie)
        await refused("debit_authorise", sign(g, driver, body))  # Signed for another action.
        g = await grant(client, view, cookie)
        await refused("debit_failure", sign(g, driver, body), budget_id="other")  # Unsigned extras.
        g = await grant(client, view, cookie)
        await refused("debit_failure", sign(g, driver, body, origin="https://evil.example"))
        g = await grant(client, view, cookie)
        await refused("debit_failure", sign(g, driver, body, expires_at=g["expires_at"] + 600))
        g = await grant(client, view, cookie)
        await refused("debit_failure", sign(g, driver, body, version=True))
        g = await grant(client, view, cookie)
        await refused("debit_failure", sign(g, driver, body | {"signed_request": {}}))
        assert len(recorded) == 1
    finally:
        await client.close()


async def test_grant_is_bound_to_one_browser_sign_in(tmp_path, recorded):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path)
    try:
        first, _, _ = await login(client, view, driver)
        second, _, _ = await login(client, view, driver)
        g = await grant(client, view, first)
        body = {"action": "debit_failure", "budget_id": "b", "session_id": "s", "attempt_token": "t"}
        r = await post(client, view, "debit_failure", second, signed_request=sign(g, driver, body))
        assert r.status == 403 and recorded == []
    finally:
        await client.close()


async def test_grants_need_a_signed_in_wallet_expire_and_are_bounded(tmp_path, recorded, monkeypatch):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path)
    try:
        r = await post(client, view, "challenge")
        anonymous = r.cookies["__Host-bsv_driver_portal"].value
        assert (await post(client, view, "request_nonce", anonymous)).status == 401
        assert (await post(client, view, "request_nonce")).status == 401
        cookie, _, _ = await login(client, view, driver)
        for _ in range(portal_requests.MAX_PENDING):
            await grant(client, view, cookie)
        assert (await post(client, view, "request_nonce", cookie)).status == 401
        import time
        from types import SimpleNamespace
        later = SimpleNamespace(time=time.time, monotonic=lambda: time.monotonic() + portal_requests.LIFETIME + 1)
        monkeypatch.setattr(portal_requests, "time", later)  # This module only; not asyncio's clock.
        g = await grant(client, view, cookie)  # Expired grants no longer count towards the bound.
        later.time = lambda: time.time() + portal_requests.LIFETIME + 1
        body = {"action": "debit_failure", "budget_id": "b", "session_id": "s", "attempt_token": "t"}
        r = await post(client, view, "debit_failure", cookie, signed_request=sign(g, driver, body))
        assert r.status == 403 and recorded == []
    finally:
        await client.close()


async def test_official_typescript_sdk_request_signature_verifies_in_python_http(tmp_path, recorded):
    import asyncio
    from pathlib import Path
    from test_portal import COOKIE, signed
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path)
    try:
        r = await post(client, view, "challenge")
        cookie = r.cookies[COOKIE].value
        r = await post(client, view, "login", cookie, **signed(await r.json(), PrivateKey(19)))
        cookie = r.cookies[COOKIE].value
        g = await grant(client, view, cookie)
        # Non-ASCII text checks that both sides sign and verify the same UTF-8 bytes.
        body = {"action": "debit_failure", "budget_id": "b", "session_id": "séance-⚡", "attempt_token": "t"}
        script = Path(__file__).resolve().parents[1] / "frontend/driver/cross-portal-request.js"
        proc = await asyncio.create_subprocess_exec("node", str(script),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, error = await proc.communicate(json.dumps({"grant": g, "origin": ORIGIN, "request": body}).encode())
        assert proc.returncode == 0, error.decode()
        r = await post(client, view, "debit_failure", cookie, signed_request=json.loads(out))
        assert r.status == 200, await r.text()
        assert recorded == [("debit", PrivateKey(19).public_key().hex(), body)]
    finally:
        await client.close()
