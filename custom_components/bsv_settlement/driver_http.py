"""Narrow capability endpoint: read invitation/rates or submit signed consent.

No HA tokens, arbitrary service calls, private keys or payment actions exposed.
"""
from collections import deque
import json
import time
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from .api import WalletError
from .const import DOMAIN


class DriverBudgetView(HomeAssistantView):
    url = "/api/bsv_settlement/driver"
    name = "api:bsv_settlement:driver"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass
        self.requests = deque()

    async def post(self, request):
        headers = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
        clock = time.monotonic()
        while self.requests and self.requests[0] < clock - 60:
            self.requests.popleft()
        if len(self.requests) >= 120:
            return web.json_response({"error":"Too many requests; try again shortly."},status=429,headers=headers)
        self.requests.append(clock)
        if request.content_type != "application/json" or request.headers.get("Sec-Fetch-Site") == "cross-site":
            return web.json_response({"error":"Unsupported request"},status=403,headers=headers)
        body = bytearray()
        async for part in request.content.iter_chunked(4096):
            body.extend(part)
            if len(body) > 20000:
                return web.json_response({"error":"Request too large"},status=413,headers=headers)
        try:
            data = json.loads(body)
            if not isinstance(data, dict) or data.get("action") not in ("read", "approve"):
                raise WalletError("Unsupported action")
            budget_id, token = data.get("budget_id"), data.get("token")
            if not isinstance(budget_id,str) or len(budget_id) != 36:
                raise WalletError("Invalid driver link")
            coord = next((c for c in self.hass.data.get(DOMAIN, {}).values()
                          if getattr(c,"mode",None) == "embedded_mainnet"
                          and budget_id in c.api.saved.get("session_budgets",{})), None)
            if coord is None:
                raise WalletError("Invalid driver link")
            async with coord.lock:
                row = coord.api.budgets.driver_access(budget_id, token)
                if data["action"] == "approve":
                    # Retry an already accepted receipt even if live rates are temporarily unavailable.
                    if not row.get("receipt") and not coord.api.budgets.prices(row)["valid"]:
                        raise WalletError("Current Amber prices are unavailable or stale. Try again later.")
                    await coord.api.budgets.accept(row, data.get("receipt"), "driver_capability")
                result = coord.api.budgets.driver_view(row)
            return web.json_response(result,headers=headers)
        except (ValueError, TypeError, RecursionError, WalletError) as exc:
            message = str(exc) if isinstance(exc,WalletError) else "Invalid request"
            return web.json_response({"error":message},status=400,headers=headers)
