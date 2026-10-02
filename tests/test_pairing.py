"""Real aiohttp relay tests. No wallets, provider calls or live HA access."""
import asyncio
import json
from types import SimpleNamespace

import pytest
from aiohttp import web, WSServerHandshakeError, WSMsgType
from aiohttp.test_utils import TestClient, TestServer

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.pairing import (
    KEY, PairingHub, PairingDiscoveryView, PairingSocketView, external_origin, MAX_WIRE,
)

pytestmark = pytest.mark.asyncio
ORIGIN = "https://charging.example.com"
KEY_HEX = "02" + "11" * 32


def fixture():
    row = {"terms": {"budget_id": "budget-one"}, "state": "awaiting_driver_consent"}
    coord = SimpleNamespace(api=SimpleNamespace(
        saved={"session_budgets": {"budget-one": row}},
        budgets=SimpleNamespace(public=lambda row: row)))
    hass = SimpleNamespace(
        config=SimpleNamespace(external_url=ORIGIN),
        data={"bsv_settlement": {"entry": coord}}, async_create_task=asyncio.create_task)
    hub = hass.data[KEY] = PairingHub(hass)
    return hass, hub, coord, row


async def server():
    hass, hub, coord, row = fixture()
    app = web.Application()
    discovery, sockets = PairingDiscoveryView(hass), PairingSocketView(hass)
    async def discover(request):
        return await discovery.get(request, request.match_info["topic"])
    app.router.add_get(discovery.url, discover)
    app.router.add_get(sockets.url, sockets.get)
    client = TestClient(TestServer(app))
    await client.start_server()
    s = await hub.create(coord, row, KEY_HEX, ORIGIN)
    return client, hub, row, s


def desktop_headers(s):
    return {"Origin": ORIGIN, "Sec-WebSocket-Protocol":
            "bsv-wallet-relay, bsv-wallet-relay-token." + s["desktop_token"]}


def wire(s, **extra):
    return json.dumps({"topic": s["topic"], "ciphertext": "YWJj", **extra})


async def test_bidirectional_transport_discovery_redaction_and_disconnect():
    client, hub, row, s = await server()
    try:
        r = await client.get("/api/session/" + s["topic"])
        assert r.headers["Cache-Control"] == "no-store"
        assert await r.json() == {"relay": "wss://charging.example.com"}
        desktop = await client.ws_connect("/ws?role=desktop&topic=" + s["topic"], headers=desktop_headers(s))
        mobile = await client.ws_connect("/ws?role=mobile&topic=" + s["topic"])
        await mobile.send_str(wire(s, mobileIdentityKey=KEY_HEX))
        assert (await desktop.receive(timeout=1)).data == wire(s, mobileIdentityKey=KEY_HEX)
        await desktop.send_str(wire(s))
        assert (await mobile.receive(timeout=1)).data == wire(s)
        assert hub.sessions[s["topic"]]["connected"]
        assert row["state"] == "awaiting_driver_consent"  # Pairing is NOT consent.
        await mobile.close()
        await desktop.receive(timeout=1)
        assert s["topic"] not in hub.sessions
    finally:
        await hub.close_all()
        await client.close()


@pytest.mark.parametrize("origin", [None, "http://charging.example.com", "https://localhost",
    "https://192.168.1.2", "https://user:pass@charging.example.com", "https://charging.example.com/path",
    "https://charging.example.com?key=secret"])
async def test_invalid_configured_origin(origin):
    hass, hub, coord, row = fixture()
    hass.config.external_url = origin
    with pytest.raises(WalletError):
        external_origin(hass)


async def test_create_scope_limits_and_no_persistent_secrets():
    hass, hub, coord, row = fixture()
    try:
        with pytest.raises(WalletError, match="public HTTPS"):
            await hub.create(coord, row, KEY_HEX, "https://attacker.example.com")
        with pytest.raises(WalletError, match="identity"):
            await hub.create(coord, row, "bad", ORIGIN)
        first = await hub.create(coord, row, KEY_HEX, ORIGIN)
        second = await hub.create(coord, row, KEY_HEX, ORIGIN)
        assert first["topic"] not in hub.sessions
        assert first["desktop_token"] != second["desktop_token"]
        assert "desktop_token" not in json.dumps(coord.api.saved)
        for _ in range(10):
            await hub.create(coord, row, KEY_HEX, ORIGIN)
        with pytest.raises(WalletError, match="Too many"):
            await hub.create(coord, row, KEY_HEX, ORIGIN)
    finally:
        await hub.close_all()


async def test_expiry_revocation_and_unload_fail_closed():
    hass, hub, coord, row = fixture()
    try:
        s = await hub.create(coord, row, KEY_HEX, ORIGIN)
        row["state"] = "revoked"
        with pytest.raises(WalletError, match="revoked"):
            hub.valid(s["topic"])
        row["state"] = "awaiting_driver_consent"
        hass.data["bsv_settlement"].clear()
        with pytest.raises(WalletError, match="not loaded"):
            hub.valid(s["topic"])
        hass.data["bsv_settlement"]["entry"] = coord
        hub.sessions[s["topic"]]["deadline"] = 0
        with pytest.raises(WalletError, match="expired"):
            hub.valid(s["topic"])
    finally:
        await hub.close_all()


@pytest.mark.parametrize("role,headers,suffix", [
    ("desktop", {}, ""), ("desktop", {"Origin": ORIGIN}, ""),
    ("desktop", {"Origin": "https://evil.example.com"}, ""),
    ("mobile", {"Origin": "https://evil.example.com"}, ""),
    ("observer", {}, ""), ("desktop", {}, "&token=not-allowed"),
])
async def test_socket_authentication(role, headers, suffix):
    client, hub, row, s = await server()
    try:
        with pytest.raises(WSServerHandshakeError):
            await client.ws_connect(f"/ws?role={role}&topic={s['topic']}{suffix}", headers=headers)
        assert s["topic"] in hub.sessions  # Bad auth cannot tear down a valid connection.
    finally:
        await hub.close_all()
        await client.close()


async def test_duplicate_mobile_and_post_revocation_frame():
    client, hub, row, s = await server()
    try:
        path = "/ws?topic=" + s["topic"]
        desktop = await client.ws_connect(path + "&role=desktop", headers=desktop_headers(s))
        mobile = await client.ws_connect(path + "&role=mobile")
        with pytest.raises(WSServerHandshakeError):
            await client.ws_connect(path + "&role=mobile")
        row["state"] = "revoked"
        await mobile.send_str(wire(s))
        assert (await desktop.receive(timeout=1)).type in (WSMsgType.CLOSE, WSMsgType.CLOSED)
    finally:
        await hub.close_all()
        await client.close()


@pytest.mark.parametrize("raw", ['[]', '{}', '{"topic":"wrong","ciphertext":"YWJj"}',
    '{"topic":"wrong","ciphertext":"++++"}', 'x' * (MAX_WIRE + 1)])
async def test_malformed_and_oversized_envelope(raw):
    hass, hub, coord, row = fixture()
    try:
        s = await hub.create(coord, row, KEY_HEX, ORIGIN)
        with pytest.raises(WalletError):
            await hub.forward(s["topic"], "mobile", raw)
    finally:
        await hub.close_all()


async def test_qr_expiry_blocks_late_mobile():
    client, hub, row, s = await server()
    try:
        desktop = await client.ws_connect("/ws?role=desktop&topic=" + s["topic"], headers=desktop_headers(s))
        hub.sessions[s["topic"]]["expiry"] = 0
        with pytest.raises(WSServerHandshakeError):
            await client.ws_connect("/ws?role=mobile&topic=" + s["topic"])
        await desktop.close()
    finally:
        await hub.close_all()
        await client.close()


async def test_capacity_limit_and_close_all_remove_memory_state():
    hass, hub, coord, row = fixture()
    try:
        for i in range(8):
            next_row = {"terms": {"budget_id": f"budget-{i}"}, "state": "awaiting_driver_consent"}
            coord.api.saved["session_budgets"][f"budget-{i}"] = next_row
            await hub.create(coord, next_row, KEY_HEX, ORIGIN)
        with pytest.raises(WalletError, match="capacity"):
            await hub.create(coord, row, KEY_HEX, ORIGIN)
        assert len(hub.sessions) == 8
        await hub.close_all()
        assert not hub.sessions
    finally:
        await hub.close_all()
