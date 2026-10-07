"""Portal isolation, proof-of-control, HTTP and relay tests. No live network."""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from bsv import PrivateKey

from custom_components.bsv_settlement.portal import (
    DriverPortalView, COOKIE, KEY, PROTOCOL, SCOPE, history, owned, credit_owner, authorisations,
    wallet_metadata,
)
from custom_components.bsv_settlement.budget import message_hash, sha
from custom_components.bsv_settlement.pairing import PairingHub, KEY as PAIRING_KEY
from custom_components.bsv_settlement.api import WalletError
from test_receipt_ack import confirmed, report
from test_budget import no_network

pytestmark = pytest.mark.asyncio
ORIGIN = "https://charging.example.com"


async def test_authorisations_are_owner_scoped_read_only_evidence(tmp_path):
    _, api, row, driver, _ = await confirmed(tmp_path)
    before = copy.deepcopy(api.saved)
    rows = authorisations(api, driver.public_key().hex())
    assert len(rows) == 1
    assert rows[0]["limit_sats"] == row["terms"]["max_total_sats"]
    assert rows[0]["receiving_registered"] is True
    assert authorisations(api, PrivateKey(999).public_key().hex()) == []
    assert api.saved == before
    text = json.dumps(rows)
    for forbidden in ("token", "signature", "address", "secret", "fragment"):
        assert forbidden not in text


async def test_wallet_metadata_addresses_are_owned_verified_and_read_only(tmp_path):
    _, api, row, driver, _ = await confirmed(tmp_path)
    before = copy.deepcopy(api.saved)
    metadata = wallet_metadata(api, driver.public_key().hex())
    address = metadata["addresses"][0]
    assert address["receiving_address"] == row["credit_destination"]["address"]
    assert address["receiving_verified"] is True
    assert address["payment_address"] == row["terms"]["operator_address"]
    assert address["operator_identity"] == row["terms"]["operator_identity"]
    assert wallet_metadata(api, PrivateKey(999).public_key().hex()) == {"addresses": []}
    for forbidden in ("token", "signature", "secret", "fragment", "proof"):
        assert forbidden not in json.dumps(metadata)
    assert api.saved == before
    row["credit_destination"]["address"] = "tampered"
    assert wallet_metadata(api, driver.public_key().hex())["addresses"][0]["receiving_address"] is None
    row["credit_destination"] = before["session_budgets"][row["terms"]["budget_id"]]["credit_destination"]
    row["credit_destination"]["proof"]["signature"] = "00"
    assert wallet_metadata(api, driver.public_key().hex())["addresses"][0]["receiving_verified"] is False


async def test_weekly_authorisation_projection_obeys_expiry_and_revocation(tmp_path, monkeypatch):
    from datetime import timedelta
    from test_weekly_mandate import setup
    _, api, _, row, driver, _, clock = await setup(tmp_path, monkeypatch)
    identity = driver.public_key().hex()
    assert authorisations(api, identity)[0]["spending_active"] is True
    row["state"] = "revoked"
    assert authorisations(api, identity)[0]["spending_active"] is False
    row["state"] = "spending_authorised_wallet_permission_required"
    clock[0] += timedelta(days=8)
    assert authorisations(api, identity)[0]["spending_active"] is False


async def fixture(tmp_path, ongoing=False):
    hass, api, row, driver, payment = await confirmed(tmp_path, ongoing)
    hass.config.external_url = ORIGIN
    coord = SimpleNamespace(mode="embedded_mainnet", api=api, lock=asyncio.Lock())
    hass.data["bsv_settlement"]["wallet"] = coord
    view = DriverPortalView(hass)
    hub = hass.data[PAIRING_KEY] = PairingHub(hass)
    app = web.Application()
    app.router.add_post(view.url, view.post)
    client = TestClient(TestServer(app))
    await client.start_server()
    return hass, api, row, driver, payment, view, hub, client


async def post(client, view, action, cookie=None, **data):
    headers = {"Origin": ORIGIN}
    if cookie:
        headers["Cookie"] = COOKIE + "=" + cookie
    return await client.post(view.url, json={"action": action, **data}, headers=headers)


def signed(challenge, driver):
    key = driver.derive_child(PrivateKey(1).public_key(), f"2-{PROTOCOL}-{challenge['keyID']}")
    return {"identity": driver.public_key().hex(), "payload": challenge["payload"],
            "signature": key.sign(challenge["payload"].encode(), hasher=message_hash).hex()}


async def login(client, view, driver):
    r = await post(client, view, "challenge")
    assert r.status == 200
    cookie = r.cookies[COOKIE].value
    challenge = await r.json()
    r = await post(client, view, "login", cookie, **signed(challenge, driver))
    assert r.status == 200
    assert r.cookies[COOKIE]["secure"] and r.cookies[COOKIE]["httponly"]
    assert r.cookies[COOKIE]["samesite"] == "Strict" and r.cookies[COOKIE]["path"] == "/"
    return r.cookies[COOKIE].value, cookie, challenge


@pytest.mark.parametrize("ongoing", [False, True])
async def test_login_history_receipt_logout_no_payments_or_authority_changes(tmp_path, ongoing):
    hass, api, row, driver, payment, view, hub, client = await fixture(tmp_path, ongoing)
    before = copy.deepcopy(api.saved)
    posts = len(api.chain.posts)
    try:
        cookie, old, _ = await login(client, view, driver)
        assert cookie != old
        r = await post(client, view, "sessions", cookie)
        body = await r.json()
        assert r.status == 200 and r.headers["Cache-Control"] == "no-store"
        assert body["identity"] == driver.public_key().hex() and body["total"] >= 1
        transactions = [t for s in body["sessions"] for t in s["transactions"]]
        assert any(t["txid"] == payment["txid"] for t in transactions)
        for forbidden in ("driver_link_fragment", "driver_token_hash", "secret_hex", "raw_tx", "signature", "quote"):
            assert forbidden not in json.dumps(body)
        assert api.saved == before  # Login and listing are entirely read-only.
        original_payment = {k: payment.get(k) for k in ("txid", "signed_raw", "amount_sats", "fee_sats", "recipient_address")}
        r = await post(client, view, "credit_receipt", cookie, credit_id=payment["budget_id"])
        assert r.status == 200 and (await r.json())["receipt"]["txid"] == payment["txid"]
        assert (await post(client, view, "sessions", old)).status == 401
        # Existing receipt retrieval rechecks provider evidence and may cache its proof.
        assert {k: payment.get(k) for k in original_payment} == original_payment
        for key in before:
            if key != "automatic_credits":
                assert api.saved[key] == before[key]
        assert len(api.chain.posts) == posts
        assert (await post(client, view, "logout", cookie)).status == 200
        assert (await post(client, view, "sessions", cookie)).status == 401
    finally:
        await hub.close_all()
        await client.close()


async def test_unknown_and_wrong_wallet_cannot_read_another_driver(tmp_path):
    _, api, row, driver, payment, view, hub, client = await fixture(tmp_path)
    try:
        assert (await post(client, view, "sessions")).status == 401
        stranger = PrivateKey()
        cookie, _, _ = await login(client, view, stranger)
        result = await (await post(client, view, "sessions", cookie)).json()
        assert result["sessions"] == [] and result["total"] == 0
        for cid in [payment["budget_id"], "nonexistent"]:
            r = await post(client, view, "credit_receipt", cookie, credit_id=cid)
            assert r.status == 401
        assert not owned(api, row, stranger.public_key().hex())
        row["receipt"]["signature"] = "00" * 70
        assert not owned(api, row, driver.public_key().hex())
    finally:
        await hub.close_all()
        await client.close()


@pytest.mark.parametrize("mutation", ["identity", "signature", "payload", "expired", "wrong_browser", "replay"])
async def test_challenge_binding_expiry_tampering_and_replay(tmp_path, mutation):
    _, _, _, driver, _, view, hub, client = await fixture(tmp_path)
    try:
        r = await post(client, view, "challenge")
        cookie = r.cookies[COOKIE].value
        challenge = await r.json()
        proof = signed(challenge, driver)
        if mutation == "identity":
            proof["identity"] = PrivateKey().public_key().hex()
        elif mutation == "signature":
            proof["signature"] = "00" * 70
        elif mutation == "payload":
            proof["payload"] = proof["payload"].replace(SCOPE, "authorise_spending")
        elif mutation == "expired":
            view.state.sessions[sha(cookie)]["challenge"]["deadline"] = 0
        elif mutation == "wrong_browser":
            r = await post(client, view, "challenge")
            cookie = r.cookies[COOKIE].value
        elif mutation == "replay":
            assert (await post(client, view, "login", cookie, **proof)).status == 200
        assert (await post(client, view, "login", cookie, **proof)).status == 401
        assert (await post(client, view, "login", cookie, **proof)).status == 401
    finally:
        await hub.close_all()
        await client.close()


async def test_http_origin_size_actions_restart_and_rate_guards(tmp_path):
    hass, api, _, driver, _, view, hub, client = await fixture(tmp_path)
    before = copy.deepcopy(api.saved)
    try:
        for origin in (None, "https://attacker.example"):
            r = await client.post(view.url, json={"action": "challenge"},
                                  headers={"Origin": origin} if origin else {})
            assert r.status == 403
        assert (await client.post(view.url, json={"action": "challenge"},
                headers={"Origin": ORIGIN, "Sec-Fetch-Site": "cross-site"})).status == 403
        assert (await client.post(view.url, data=b"x" * 20001,
                headers={"Origin": ORIGIN, "Content-Type": "application/json"})).status == 413
        cookie, _, _ = await login(client, view, driver)
        for action in ("approve", "claim_collection", "authorise_collection", "register_credit_destination",
                       "broadcast_operator_payment", "get_credit_receipt_link"):
            assert (await post(client, view, action, cookie)).status == 401
        view.state.sessions.clear()
        assert (await post(client, view, "sessions", cookie)).status == 401
        assert api.saved == before
        view.state.requests.extend([__import__("time").monotonic()] * 120)
        assert (await post(client, view, "challenge")).status == 429
    finally:
        await hub.close_all()
        await client.close()


async def test_portal_pairing_survives_login_rotation_not_logout_or_expiry(tmp_path):
    hass, api, row, driver, _, view, hub, client = await fixture(tmp_path)
    before = copy.deepcopy(api.saved)
    try:
        row["state"] = "revoked"  # Portal pairing is not a revived charging invitation.
        r = await post(client, view, "pairing_create", backend_identity=PrivateKey().public_key().hex())
        assert r.status == 200
        cookie = r.cookies[COOKIE].value
        pairing = await r.json()
        assert hub.valid(pairing["topic"])["budget_id"] is None
        other, _, _ = await login(client, view, PrivateKey())
        assert (await post(client, view, "pairing_cancel", other, topic=pairing["topic"])).status == 401
        r = await post(client, view, "challenge", cookie)
        challenge = await r.json()
        r = await post(client, view, "login", cookie, **signed(challenge, driver))
        new_cookie = r.cookies[COOKIE].value
        assert hub.valid(pairing["topic"])["portal_owner"] == sha(new_cookie)
        assert (await post(client, view, "logout", new_cookie)).status == 200
        assert pairing["topic"] not in hub.sessions
        before["session_budgets"][row["terms"]["budget_id"]]["state"] = "revoked"
        assert api.saved == before
    finally:
        await hub.close_all()
        await client.close()


async def test_receipt_reporting_requires_both_owner_login_and_original_wallet_signature(tmp_path):
    _, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    posts = len(api.chain.posts)
    try:
        cookie, _, _ = await login(client, view, driver)
        r = await post(client, view, "acknowledge_credit_receipt", cookie,
                       credit_id=payment["budget_id"], acknowledgement={})
        assert r.status == 401 and "wallet_receipt_ack" not in payment
        data = report(api, row, payment, driver)
        r = await post(client, view, "acknowledge_credit_receipt", cookie, credit_id=payment["budget_id"], **data)
        assert r.status == 200 and (await r.json())["wallet_receipt_status"] == "wallet_reported_accepted"
        assert len(api.chain.posts) == posts
    finally:
        await hub.close_all()
        await client.close()


async def test_history_pagination_is_owner_filtered_not_limited_to_last_twenty_routes(tmp_path):
    _, api, row, driver, payment, view, hub, client = await fixture(tmp_path, True)
    try:
        route = next(iter(api.ongoing_credits.routes.values()))
        for index in range(30):
            clone = copy.deepcopy(route)
            clone.update(route_id=f"ongoing:proxy-entry|extra-{index}", session_id=f"extra-{index}",
                         transaction_id=f"transaction-{index}")
            api.ongoing_credits.routes[clone["route_id"]] = clone
        stranger = copy.deepcopy(route)
        stranger["recipient"]["driver_identity"] = PrivateKey().public_key().hex()
        stranger.update(route_id="stranger-route", session_id="stranger-session")
        api.ongoing_credits.routes["stranger-route"] = stranger
        row["state"] = "revoked"
        cookie, _, _ = await login(client, view, driver)
        first = await (await post(client, view, "sessions", cookie)).json()
        second = await (await post(client, view, "sessions", cookie, offset=25)).json()
        assert first["total"] == 31 and len(first["sessions"]) == 25
        assert len(second["sessions"]) == 6 and not second["has_more"]
        assert "stranger-session" not in json.dumps(first) + json.dumps(second)
        assert first["has_more"]
        for bad in (-1, True, "25", 100001):
            assert (await post(client, view, "sessions", cookie, offset=bad)).status == 401
    finally:
        await hub.close_all()
        await client.close()


async def test_portal_cookie_expiry_and_loaded_instance_are_required(tmp_path):
    hass, _, _, driver, _, view, hub, client = await fixture(tmp_path)
    try:
        cookie, _, _ = await login(client, view, driver)
        view.state.sessions[sha(cookie)]["deadline"] = 0
        assert (await post(client, view, "sessions", cookie)).status == 401
        cookie, _, _ = await login(client, view, driver)
        hass.data["bsv_settlement"].pop("wallet")
        assert (await post(client, view, "sessions", cookie)).status == 401
    finally:
        await hub.close_all()
        await client.close()


async def test_official_typescript_sdk_login_signature_verifies_in_python_http(tmp_path):
    from pathlib import Path
    _, _, _, _, _, view, hub, client = await fixture(tmp_path)
    try:
        r = await post(client, view, "challenge")
        cookie = r.cookies[COOKIE].value
        challenge = await r.json()
        script = Path(__file__).resolve().parents[1] / "frontend/driver/cross-portal.js"
        proc = await asyncio.create_subprocess_exec("node", str(script),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, error = await proc.communicate(json.dumps(challenge).encode())
        assert proc.returncode == 0, error.decode()
        proof = json.loads(out)
        r = await post(client, view, "login", cookie, **proof)
        assert r.status == 200
        assert (await r.json())["identity"] == PrivateKey(19).public_key().hex()
    finally:
        await hub.close_all()
        await client.close()


async def test_weekly_child_historical_ownership_is_verified_without_renewal(tmp_path, monkeypatch):
    from datetime import timedelta
    from test_weekly_mandate import setup, session
    from custom_components.bsv_settlement.weekly import ticket
    _, api, proxy, root, driver, _, clock = await setup(tmp_path, monkeypatch)
    session(proxy, clock)
    child = await ticket(api, root, "new-1")
    clock[0] += timedelta(days=8)
    root["state"] = "revoked"
    before = copy.deepcopy(api.saved)
    identity = driver.public_key().hex()
    assert owned(api, child, identity)
    assert any(s["session_id"] == "new-1" for s in history(api, identity))
    assert api.saved == before
    child["terms"]["weekly_parent_hash"] = "0" * 64
    assert not owned(api, child, identity)
