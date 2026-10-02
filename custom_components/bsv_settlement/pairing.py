"""Ephemeral, capability-scoped blind relay for BSV Browser QR pairing.

The desktop owns an ephemeral SDK ProtoWallet, not a funded wallet. HA relays
bounded ciphertext only. Nothing here signs transactions or changes consent.
"""
import asyncio
from collections import deque
import ipaddress
import json
import re
import secrets
import time
from urllib.parse import urlsplit

from aiohttp import web, WSMsgType
from homeassistant.components.http import HomeAssistantView

from .api import WalletError
from .const import DOMAIN

KEY = DOMAIN + "_pairing"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
MAX_WIRE = 1_000_000  # Deliberately bounded; large BEEF requires fallback.
PUBLIC_KEY = re.compile(r"0[23][0-9a-f]{64}")


def external_origin(hass):
    """Never sign a Host header or accept a caller-selected relay destination."""
    raw = getattr(hass.config, "external_url", None)
    try:
        u = urlsplit(raw or "")
        if (u.scheme != "https" or not u.hostname or u.username or u.password
                or u.path not in ("", "/") or u.query or u.fragment
                or u.hostname.endswith((".local", ".localhost", ".internal"))
                or "." not in u.hostname):
            raise ValueError()
        try:
            address = ipaddress.ip_address(u.hostname)
        except ValueError:
            address = None
        if address and not address.is_global:
            raise ValueError()
        return f"https://{u.netloc.lower()}".rstrip("/")
    except (ValueError, TypeError):
        raise WalletError("Pairing needs a configured public HTTPS external URL") from None


class PairingHub:
    """In-memory sessions die on disconnect, restart, timeout or revocation."""

    def __init__(self, hass):
        self.hass = hass
        self.sessions = {}
        self.creations = deque()

    async def close(self, topic):
        item = self.sessions.pop(topic, None)
        if item:
            item["timer"].cancel()
            for ws in tuple(item["sockets"].values()):
                await ws.close(code=1000, message=b"Pairing ended; reconnect explicitly")

    async def close_all(self, *_):
        for topic in list(self.sessions):
            await self.close(topic)

    def valid(self, topic):
        item = self.sessions.get(topic)
        if not item or time.monotonic() >= item["deadline"]:
            raise WalletError("Pairing expired or disconnected")
        coord = item["coord"]
        if coord not in self.hass.data.get(DOMAIN, {}).values():
            raise WalletError("Wallet integration is not loaded")
        row = coord.api.saved.get("session_budgets", {}).get(item["budget_id"])
        if not row or coord.api.budgets.public(row)["state"] in ("expired", "revoked"):
            raise WalletError("Driver invitation is expired or revoked")
        return item

    async def create(self, coord, row, key, request_origin):
        origin = external_origin(self.hass)
        if request_origin != origin:
            raise WalletError("Open this driver link at the configured public HTTPS address")
        if not isinstance(key, str) or not PUBLIC_KEY.fullmatch(key):
            raise WalletError("Invalid pairing identity")
        now = time.monotonic()
        while self.creations and self.creations[0] < now - 60:
            self.creations.popleft()
        if len(self.creations) >= 12:
            raise WalletError("Too many pairing requests; try again shortly")
        # Retire only this capability's previous connection. Never touch consent.
        for topic, item in list(self.sessions.items()):
            if item["budget_id"] == row["terms"]["budget_id"]:
                await self.close(topic)
        if len(self.sessions) >= 8:
            raise WalletError("Pairing capacity reached; try again shortly")
        self.creations.append(now)
        topic, desktop_token = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        expiry = int(time.time()) + 120
        item = {
            "coord": coord, "budget_id": row["terms"]["budget_id"],
            "origin": origin, "key": key, "desktop_token": desktop_token,
            "expiry": expiry, "deadline": now + 120, "sockets": {},
            "connected": False, "messages": deque(),
        }
        self.sessions[topic] = item
        item["timer"] = asyncio.get_running_loop().call_later(
            120, lambda: self.hass.async_create_task(self.close(topic)))
        return {"topic": topic, "desktop_token": desktop_token, "origin": origin,
                "backendIdentityKey": key, "expiry": str(expiry),
                "relay": origin.replace("https://", "wss://", 1)}

    def joined(self, item, role, ws):
        if role in item["sockets"]:
            raise WalletError("This pairing role is already connected")
        if role == "mobile" and ("desktop" not in item["sockets"]
                                 or time.time() >= item["expiry"]):
            raise WalletError("Pairing invitation is not available")
        item["sockets"][role] = ws

    async def forward(self, topic, role, raw):
        item = self.valid(topic)
        now = time.monotonic()
        while item["messages"] and item["messages"][0] < now - 60:
            item["messages"].popleft()
        if len(item["messages"]) >= 120:
            raise WalletError("Pairing message limit exceeded")
        item["messages"].append(now)
        if not isinstance(raw, str) or not 0 < len(raw) <= MAX_WIRE:
            raise WalletError("Invalid relay frame")
        try:
            data = json.loads(raw)
            if (not isinstance(data, dict)
                    or set(data) - {"topic", "ciphertext", "mobileIdentityKey"}
                    or data.get("topic") != topic
                    or not isinstance(data.get("ciphertext"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]+", data["ciphertext"])
                    or len(data["ciphertext"]) % 4 == 1):
                raise ValueError()
            if "mobileIdentityKey" in data and not (
                    role == "mobile" and isinstance(data["mobileIdentityKey"], str)
                    and PUBLIC_KEY.fullmatch(data["mobileIdentityKey"])):
                raise ValueError()
        except (ValueError, TypeError, RecursionError):
            raise WalletError("Invalid relay envelope") from None
        peer = item["sockets"].get("mobile" if role == "desktop" else "desktop")
        if peer is None or peer.closed:
            raise WalletError("The other device disconnected")
        # Only the authenticated desktop can extend a successfully paired
        # connection, after validating the encrypted mobile handshake.
        if role == "desktop" and not item["connected"]:
            item["connected"] = True
            item["deadline"] = now + 1800
            item["timer"].cancel()
            item["timer"] = asyncio.get_running_loop().call_later(
                1800, lambda: self.hass.async_create_task(self.close(topic)))
        await peer.send_str(raw)


class PairingDiscoveryView(HomeAssistantView):
    # Upstream mobile currently hardcodes this route and /ws, not custom paths.
    url = "/api/session/{topic}"
    name = "api:bsv_settlement:pairing_discovery"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, topic):
        try:
            item = self.hass.data[KEY].valid(topic)
            # Discovery is public but discloses no desktop token or driver data.
            return web.json_response(
                {"relay": item["origin"].replace("https://", "wss://", 1)},
                headers=HEADERS)
        except WalletError:
            return web.json_response({"error": "Pairing unavailable"}, status=404, headers=HEADERS)


class PairingSocketView(HomeAssistantView):
    url = "/ws"
    name = "api:bsv_settlement:pairing_socket"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request):
        hub = self.hass.data[KEY]
        topic, role = request.query.get("topic"), request.query.get("role")
        ws = None
        try:
            item = hub.valid(topic)
            if role not in ("desktop", "mobile") or set(request.query) != {"topic", "role"}:
                raise WalletError("Unsupported relay request")
            offered = [p.strip() for p in request.headers.get("Sec-WebSocket-Protocol", "").split(",")]
            if role == "desktop":
                expected = "bsv-wallet-relay-token." + item["desktop_token"]
                if (request.headers.get("Origin") != item["origin"]
                        or "bsv-wallet-relay" not in offered
                        or not any(secrets.compare_digest(p, expected) for p in offered)):
                    raise WalletError("Desktop authentication required")
            elif request.headers.get("Origin") not in (None, item["origin"]):
                raise WalletError("Mobile origin is not allowed")
            ws = web.WebSocketResponse(
                protocols=("bsv-wallet-relay",) if role == "desktop" else (),
                max_msg_size=MAX_WIRE, heartbeat=20, receive_timeout=90)
            hub.joined(item, role, ws)
            try:
                await ws.prepare(request)
                async for message in ws:
                    if message.type == WSMsgType.TEXT:
                        await hub.forward(topic, role, message.data)
                    else:
                        break
            finally:
                await hub.close(topic)
            return ws
        except (WalletError, TimeoutError, ConnectionError):
            if ws is not None and ws.prepared:
                await hub.close(topic)
                return ws
            return web.json_response({"error": "Pairing unavailable"}, status=403, headers=HEADERS)
