"""Capability endpoint for terms, signed approval and one-use collection permits.

No HA tokens, arbitrary services, private keys or operator-wallet broadcast exposed.
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
            if not isinstance(data, dict) or data.get("action") not in (
                    "read", "approve", "collection_status", "claim_collection",
                    "register_credit_destination", "credit_receipt", "ongoing_credit_receipt",
                    "authorise_collection", "report_collection", "reconcile_collection",
                    "pairing_create", "pairing_cancel", "report_collection_failure"):
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
                action = data["action"]
                row = coord.api.budgets.driver_access(budget_id, token, allow_terminal=action in (
                    "read", "collection_status", "report_collection", "reconcile_collection",
                    "credit_receipt", "ongoing_credit_receipt", "report_collection_failure"))
                if (action == "read" and row["state"] == "revoked" and not coord.api.auto_credits.get(row)
                        and not coord.api.ongoing_credits.driver_rows(row)):
                    raise WalletError("Driver link is revoked")
                if action in ("pairing_create", "pairing_cancel"):
                    from .pairing import KEY
                    hub = self.hass.data.get(KEY)
                    if hub is None:
                        raise WalletError("Wallet pairing is unavailable")
                    if action == "pairing_create":
                        return web.json_response(await hub.create(
                            coord, row, data.get("backend_identity"),
                            request.headers.get("Origin")), headers=headers)
                    topic = data.get("topic")
                    item = hub.sessions.get(topic) if isinstance(topic, str) else None
                    if item and item["budget_id"] != budget_id:
                        raise WalletError("Pairing does not belong to this invitation")
                    await hub.close(topic)
                    return web.json_response({"disconnected": True}, headers=headers)
                handlers = {
                    "register_credit_destination": lambda: coord.api.auto_credits.register(row, data),
                    "credit_receipt": lambda: coord.api.auto_credits.receipt(row),
                    "ongoing_credit_receipt": lambda: coord.api.ongoing_credits.driver_receipt(row, data.get("credit_id")),
                    "collection_status": lambda: coord.api.collections.status(row),
                    "claim_collection": lambda: coord.api.collections.claim(row, data),
                    "authorise_collection": lambda: coord.api.collections.authorise(row, data),
                    "report_collection": lambda: coord.api.collections.report(row, data),
                    "reconcile_collection": lambda: coord.api.collections.reconcile(row),
                }
                if action == "report_collection_failure":
                    from .collection_recovery import record_failure
                    return web.json_response(await record_failure(
                        coord.api.collections, row, data), headers=headers)
                if action in handlers:
                    return web.json_response(await handlers[action](), headers=headers)
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
