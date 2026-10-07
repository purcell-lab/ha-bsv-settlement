"""Wallet proof-of-control login and owner-scoped charging settlement.

No budget capabilities, operator keys or authority renewal. Debit operations
delegate to the existing guarded engine under independently signed budgets.
Monthly consent actions are delegated to ``monthly_portal`` and stay disabled
unless a reviewed activation configures them; no accounting transition is exposed.
Login state is memory-only and is lost on restart. Receipt reports use the
existing independently wallet-signed acknowledgement, not login authority.
"""
import copy
import json
import re
import secrets
import time
from collections import deque

from aiohttp import web
from bsv import PrivateKey, PublicKey
from homeassistant.components.http import HomeAssistantView

from .api import WalletError
from .budget import approval_payload, canonical, message_hash, sha, signature_protocol
from .const import DOMAIN
from .pairing import external_origin, KEY as PAIRING_KEY
from . import monthly_portal, portal_debits, portal_registration

KEY = DOMAIN + "_portal"
COOKIE = "__Host-bsv_driver_portal"
PROTOCOL = "ev portal login"
SCOPE = "read_own_sessions_sync_receipts_and_collect_signed_session_budgets"
HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "Vary": "Origin"}
IDENTITY = re.compile(r"^(02|03)[0-9a-f]{64}$")


def current_prices(api):
    """Public indicative tariffs only; never infer a site from a driver's ledger."""
    from .session_review import now
    unavailable = {"checked_at": now().isoformat(), "valid": False,
                   "import": {"available": False}, "export": {"available": False}}
    proxies = [c for c in api.hass.data.get(DOMAIN, {}).values()
               if getattr(c, "mode", None) == "sensor_proxy"]
    if len(proxies) != 1:
        return unavailable  # Multiple sites need explicit selection, never guess.
    sources = getattr(proxies[0], "sources", {})
    if not all(isinstance(sources.get(k), str) for k in ("import_price", "export_price")):
        return unavailable
    prices = api.budgets.prices({"terms": {
        "import_price_entity": sources["import_price"],
        "export_price_entity": sources["export_price"],
    }})
    # Explicit allowlist: no entity IDs, invitations, identities or private URLs.
    return {"checked_at": prices["checked_at"], "valid": prices["valid"],
            **{direction: {k: value for k, value in prices[direction].items()
                          if k in ("available", "aud_per_kwh", "start", "end", "estimate")}
               for direction in ("import", "export")}}


def owned(api, row, identity):
    """Verify historical signed ownership, even after spending expiry/revocation."""
    try:
        root = row
        if row.get("weekly_parent_id"):
            root = api.saved["session_budgets"][row["weekly_parent_id"]]
            if (root.get("weekly_parent_id") or row.get("receipt") != root.get("receipt")
                    or row["terms"].get("weekly_parent_hash") != sha(root["invitation"]["payload"])
                    or row["proxy_config_entry_id"] != root["proxy_config_entry_id"]):
                return False
        for candidate in (root, row):
            inv = candidate["invitation"]
            if (json.loads(inv["payload"]) != candidate["terms"]
                    or candidate["terms"]["operator_identity"] != api.identity["public_key"]
                    or not PublicKey(bytes.fromhex(api.identity["public_key"])).verify(
                        bytes.fromhex(inv["signature"]), inv["payload"].encode(), hasher=message_hash)):
                return False
        receipt = root["receipt"]
        expected = approval_payload(root["invitation"], identity)
        if receipt["driver_identity"] != identity or receipt["payload"] != expected:
            return False
        protocol = signature_protocol(root["terms"])
        key = PublicKey(bytes.fromhex(identity)).derive_child(
            PrivateKey(1), f"{protocol[0]}-{protocol[1]}-{root['terms']['budget_id']}")
        return key.verify(bytes.fromhex(receipt["signature"]), expected.encode(), hasher=message_hash)
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def ownership(api, identity):
    return {bid: row for bid, row in api.saved["session_budgets"].items() if owned(api, row, identity)}


def authorisations(api, identity):
    """Read-only evidence, not capabilities or a new wallet permission."""
    from .weekly import verify_parent, remaining
    from .budget import SPENDING_STATE
    result = []
    for row in ownership(api, identity).values():
        if row.get("weekly_parent_id"):
            continue
        t = row["terms"]
        state = api.budgets.state(row)
        active = state == SPENDING_STATE
        if t.get("version") == 3:
            try:
                verify_parent(api, row)
                active = active and remaining(api, row) > 0
            except WalletError:
                active = False
        result.append({
            "state": state, "spending_active": bool(active),
            "limit_sats": t["max_total_sats"], "expires_at": t["expires_at"],
            "scope": "weekly" if t.get("version") == 3 else "session",
            "receiving_registered": bool(row.get("credit_destination")),
        })
    return result


def wallet_metadata(api, identity):
    """Owner-only address evidence. No capabilities, proofs or wallet secrets."""
    result = []
    for bid, row in ownership(api, identity).items():
        if row.get("weekly_parent_id"):
            continue
        terms = row["terms"]
        destination = row.get("credit_destination") or {}
        receiving_address = None
        try:
            expected = api.auto_credits.registration_payload(row)
            verifier = PublicKey(bytes.fromhex(identity)).derive_child(
                PrivateKey(1), f"2-ev session spending-{bid}")
            proof = destination["proof"]
            if (destination["address"] == api.auto_credits.destination(row).address()
                    and proof["payload"] == expected
                    and verifier.verify(bytes.fromhex(proof["signature"]),
                                        expected.encode(), hasher=message_hash)):
                receiving_address = destination["address"]
        except (KeyError, ValueError, TypeError, AttributeError, WalletError):
            pass
        result.append({
            "budget_id": bid,
            "scope": "weekly" if terms.get("version") == 3 else "session",
            "state": api.budgets.state(row),
            "expires_at": terms["expires_at"],
            "operator_identity": terms["operator_identity"],
            "payment_address": terms["operator_address"],
            "receiving_address": receiving_address,
            "receiving_verified": receiving_address is not None,
        })
    return {"addresses": result}


def credit_owner(api, identity, credit_id):
    """Resolve the original registration, never the latest driver."""
    if not isinstance(credit_id, str):
        raise WalletError("Credit unavailable")
    route = api.ongoing_credits.routes.get(credit_id)
    if credit_id.startswith("adjustment:"):
        from .energy_adjustment import credit_item
        review, _ = credit_item(api, credit_id)
        route = {"recipient": review["adjustment_recipient"], "adjustment": True}
    bid = route["recipient"]["budget_id"] if route else credit_id
    row = api.saved["session_budgets"].get(bid)
    if not row or not owned(api, row, identity):
        raise WalletError("Credit unavailable")
    if route and route["recipient"].get("driver_identity") != identity:
        raise WalletError("Credit unavailable")
    from .receipt_ack import payment_for
    item = payment_for(api, row, credit_id if route else None)
    return row, item, route


def history(api, identity):
    """Project only stored, attributed records. Never tick, quote, bind or reconcile."""
    rows = ownership(api, identity)
    result = {}

    def session(proxy_id, sid):
        key = proxy_id + "|" + sid
        if key not in result:
            proxy = api.hass.data.get(DOMAIN, {}).get(proxy_id)
            records = api.ongoing_credits.records(proxy) if proxy else []
            record = next((r for r in records if r.get("session_id") == sid), {})
            from .driver_live import ocpp_display
            result[key] = {"session_key": key, "session_id": sid,
                "transaction_id": record.get("ocpp_transaction_id"), "opened_at": record.get("opened_at"),
                "ended_at": record.get("ended_at"), "running_state": record.get("running_state"),
                "ocpp": ocpp_display(api, proxy_id, record),
                "import_kwh": record.get("import_kwh"), "export_kwh": record.get("export_kwh"),
                "import_cost_aud": record.get("import_cost_aud"), "export_credit_aud": record.get("export_credit_aud"),
                "net_amount_aud": record.get("net_cost_aud"),
                "meter_updated_at": record.get("as_of") or (
                    (getattr(proxy, "data", None) or {}).get("updated_at") if proxy else None),
                "satoshis_per_aud": None,
                "quality_flags": copy.deepcopy(record.get("quality_flags", [])),
                "agreements": [], "transactions": []}
        return result[key]

    def transaction(target, item, direction, tid, budget_id, account=None):
        # Explicit whitelist: never expose signatures, raw transactions, permits or private links.
        fields = ("state", "txid", "amount_sats", "fee_sats", "confirmations", "created_at", "checked_at",
                  "recipient_address", "wallet_receipt_status", "wallet_imported_at",
                  "broadcast_attempted_at", "broadcast_acknowledged_at",
                  "provider_first_confirmed_at", "wallet_accepted_reported_at", "early_receipt_available")
        entry = {k: copy.deepcopy(item.get(k)) for k in fields}
        entry.update(id=tid, direction=direction, receiving_budget_id=budget_id if direction == "operator_to_driver" else None)
        if not any(p["id"] == tid for p in target["transactions"]):
            target["transactions"].append(entry)
        if account:
            for dest, source in (("import_kwh", "import_kwh"), ("export_kwh", "export_kwh"),
                                 ("net_amount_aud", "net_amount_aud"), ("ended_at", "ended_at"),
                                 ("transaction_id", "ocpp_transaction_id")):
                if target[dest] is None:
                    target[dest] = account.get(source)

    for bid, row in rows.items():
        sid = api.collections.session_id(row)
        if not sid:
            continue
        target = session(row["proxy_config_entry_id"], sid)
        target["transaction_id"] = target["transaction_id"] or row["terms"].get("transaction_id")
        target["agreements"].append({"budget_id": bid, "state": api.budgets.public(row)["state"],
                                    "expires_at": row["terms"]["expires_at"]})
        debit = api.collections.get(row)
        if debit:
            account = json.loads(debit["quote"]["payload"]).get("account", {}) if debit.get("quote") else {}
            amount = json.loads(debit["quote"]["payload"]).get("amount_sats") if debit.get("quote") else None
            transaction(target, debit | {"amount_sats": amount}, "driver_to_operator", "debit:" + bid, bid, account)
        credit = api.auto_credits.get(row)
        if credit:
            transaction(target, api.auto_credits.public(credit), "operator_to_driver", bid, bid, credit.get("account"))
        closure = api.saved.get("closed_sessions", {}).get(target["session_key"])
        if closure:
            target["closure"] = {k: copy.deepcopy(closure.get(k)) for k in ("state", "amount_sats")}
    for cid, route in api.ongoing_credits.routes.items():
        bid = route["recipient"]["budget_id"]
        if bid not in rows or route["recipient"].get("driver_identity") != identity:
            continue
        target = session(route["proxy_config_entry_id"], route["session_id"])
        target["transaction_id"] = target["transaction_id"] or route["transaction_id"]
        credit = api.ongoing_credits.get(api.ongoing_credits.wrapper(route))
        transaction(target, api.auto_credits.public(credit) if credit else {"state": route["state"]},
                    "operator_to_driver", cid, bid, (credit or {}).get("account"))
    for review in api.saved["session_reviews"].values():
        if review.get("account_kind") != "manual_energy_adjustment":
            continue
        recipient = review["adjustment_recipient"]
        if recipient["budget_id"] not in rows or recipient["driver_identity"] != identity:
            continue
        account = review["account"]
        target = session(review["proxy_config_entry_id"], account["session_id"])
        target["account_kind"] = "manual_energy_adjustment"
        target["quality_flags"] = ["manual_energy_adjustment"]
        target["adjustment_kwh"] = account["adjustment_kwh"]
        target["adjustment_direction"] = account["adjustment_direction"]
        target["adjustment_price_aud_per_kwh"] = review["price_aud_per_kwh"]
        target["opened_at"] = review["created_at"]
        target["wallet_connected_adjustment"] = bool(review.get("wallet_collection_enabled"))
        public = api.reviews.public(review)
        payment = api.saved["payments"].get(review.get("credit_draft_id"))
        item = api.auto_credits.public(payment) if payment else (
            public | (public.get("receipt") or {}))
        transaction(target, item, review["direction"], "adjustment:" + review["review_id"],
                    recipient["budget_id"], account)
    from .monthly_projection import owned_accounts
    for binding, ledger in owned_accounts(api, identity):
        proxy_id, sid = binding["account_key"].split("|", 1)
        target = session(proxy_id, sid)
        target["authority_kind"] = "monthly"
        target["transaction_id"] = binding["transaction_id"]
        target["opened_at"] = binding["opened_at"]
        target["satoshis_per_aud"] = binding["satoshis_per_aud"]
        target["agreements"].append({"authority_id": binding["authority_id"],
                                     "state": "cancelled" if ledger.cancelled else "active",
                                     "expires_at": None})
        final = binding["final_account"]
        if final is not None:
            account = final["account"]
            for field in ("import_kwh", "export_kwh", "net_amount_aud", "ended_at",
                          "import_cost_aud", "export_credit_aud", "quality_flags"):
                if field in account:
                    target[field] = copy.deepcopy(account[field])
        for attempt in ledger.attempts:
            if attempt.account_id != binding["account_key"]:
                continue
            # Accounting commit is not provider confirmation. No txid, receipt,
            # or chain result is inferred from an opaque evidence reference.
            debit = final["debit_sats"] if final else None
            target["transactions"].append({
                "id": "monthly:" + attempt.attempt_id, "direction": "driver_to_operator",
                "state": {"committed": "wallet_spend_recorded", "reserved": "monthly_reserved",
                          "wallet_pending": "wallet_attempt_reserved", "uncertain": "broadcast_unknown",
                          "released": "reservation_released"}[attempt.state],
                "amount_sats": debit, "fee_sats": attempt.spent_sats - debit
                if attempt.state == "committed" and debit is not None else None,
                "txid": None, "confirmations": None, "wallet_receipt_status": None,
                "wallet_imported_at": None, "receiving_budget_id": None})
    for target in result.values():
        if target.get("authority_kind") == "monthly":
            continue
        if target["ended_at"]:
            continue
        # Use this session's frozen receiving route first, matching the operator
        # card. Never use the latest driver or a different session's FX rate.
        route = api.ongoing_credits.routes.get("ongoing:" + target["session_key"])
        if (route and route["recipient"]["budget_id"] in rows
                and route["recipient"].get("driver_identity") == identity):
            target["satoshis_per_aud"] = route.get("satoshis_per_aud")
        else:
            rates = {
                str(row["terms"].get("satoshis_per_aud"))
                for row in rows.values()
                if row["proxy_config_entry_id"] + "|" + str(api.collections.session_id(row))
                == target["session_key"]
            }
            # Multiple historical approvals must not lead to a guessed rate.
            if len(rates) == 1:
                target["satoshis_per_aud"] = next(iter(rates))
    return sorted(result.values(), key=lambda s: (s["opened_at"] or s["ended_at"] or "", s["session_key"]), reverse=True)


class PortalState:
    def __init__(self, hass):
        self.hass = hass
        self.sessions = {}
        self.requests = deque()

    def prune(self):
        now = time.monotonic()
        self.sessions = {k: s for k, s in self.sessions.items() if s["deadline"] > now}
        while self.requests and self.requests[0] < now - 60:
            self.requests.popleft()

    def create(self, coord, origin, identity=None):
        self.prune()
        if len(self.sessions) >= 64:
            raise WalletError("Portal is busy; try again shortly")
        token = secrets.token_urlsafe(32)
        key = sha(token)
        item = {"coord": coord, "origin": origin, "identity": identity,
                "deadline": time.monotonic() + (900 if identity else 300)}
        self.sessions[key] = item
        return token, key, item

    def valid(self, key):
        item = self.sessions.get(key)
        if (not item or item["deadline"] <= time.monotonic()
                or item["origin"] != external_origin(self.hass)
                or item["coord"] not in self.hass.data.get(DOMAIN, {}).values()):
            raise WalletError("Sign in to view your sessions")
        return item

    async def close(self, key):
        self.sessions.pop(key, None)
        hub = self.hass.data.get(PAIRING_KEY)
        if hub:
            for topic, item in list(hub.sessions.items()):
                if item.get("portal_owner") == key:
                    await hub.close(topic)


class DriverPortalView(HomeAssistantView):
    url = "/api/bsv_settlement/portal"
    name = "api:bsv_settlement:portal"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass
        self.state = hass.data[KEY] = PortalState(hass)

    async def post(self, request):
        state = self.state
        state.prune()
        if len(state.requests) >= 120:
            return web.json_response({"error": "Too many requests"}, status=429, headers=HEADERS)
        state.requests.append(time.monotonic())
        try:
            origin = external_origin(self.hass)
            if (request.headers.get("Origin") != origin or request.content_type != "application/json"
                    or request.headers.get("Sec-Fetch-Site") == "cross-site"):
                return web.json_response({"error": "Same-origin JSON required"}, status=403, headers=HEADERS)
            body = bytearray()
            async for part in request.content.iter_chunked(4096):
                body.extend(part)
                if len(body) > 20000:
                    return web.json_response({"error": "Request too large"}, status=413, headers=HEADERS)
            data = json.loads(body)
            if not isinstance(data, dict):
                raise WalletError("Invalid request")
            action = data.get("action")
            if action not in ("prices", "challenge", "login", "sessions", "logout", "pairing_create", "pairing_cancel",
                              "credit_receipt", "acknowledge_credit_receipt", "station", *monthly_portal.ACTIONS,
                              *portal_debits.ACTIONS, *portal_registration.ACTIONS):
                raise WalletError("Unsupported portal action")
            coords = [c for c in self.hass.data.get(DOMAIN, {}).values()
                      if getattr(c, "mode", None) == "embedded_mainnet"]
            if len(coords) != 1:
                raise WalletError("Driver portal is unavailable")
            coord = coords[0]
            if action == "prices":
                return web.json_response(current_prices(coord.api), headers=HEADERS)
            if action == "station":
                return web.json_response(monthly_portal.public_station(self.hass, coord, origin), headers=HEADERS)
            token = request.cookies.get(COOKIE, "")
            key = sha(token)
            item = state.sessions.get(key)
            if action == "logout":
                await state.close(key)
                response = web.json_response({"signed_out": True}, headers=HEADERS)
                response.del_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Strict")
                return response
            cookie = None
            if action in ("challenge", "pairing_create") and (
                    not item or item["deadline"] <= time.monotonic() or item["coord"] is not coord):
                token, key, item = state.create(coord, origin)
                cookie = token
            item = state.valid(key)
            if item["coord"] is not coord:
                raise WalletError("Sign in again")
            if action == "challenge":
                nonce = secrets.token_urlsafe(32)
                payload = canonical({"action": "sign_in_driver_portal", "version": 1,
                    "origin": origin, "nonce": nonce, "browser_binding": key, "scope": SCOPE,
                    "issued_at": int(time.time()), "expires_at": int(time.time()) + 120})
                item["challenge"] = {"payload": payload, "nonce": nonce, "deadline": time.monotonic() + 120}
                result = {"payload": payload, "protocolID": [2, PROTOCOL], "keyID": nonce}
            elif action == "login":
                challenge = item.pop("challenge", None)  # Single use, including failed attempts.
                identity, signature = data.get("identity"), data.get("signature")
                if (not challenge or challenge["deadline"] <= time.monotonic()
                        or data.get("payload") != challenge["payload"]
                        or not isinstance(identity, str) or not IDENTITY.fullmatch(identity)
                        or not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{16,144}", signature)):
                    raise WalletError("Wallet sign-in failed; request a fresh challenge")
                public = PublicKey(bytes.fromhex(identity)).derive_child(
                    PrivateKey(1), f"2-{PROTOCOL}-{challenge['nonce']}")
                if not public.verify(bytes.fromhex(signature), challenge["payload"].encode(), hasher=message_hash):
                    raise WalletError("Wallet sign-in failed; request a fresh challenge")
                cookie, new_key, _ = state.create(coord, origin, identity)
                state.sessions.pop(key, None)  # Rotate against fixation; retain only this browser's relay.
                hub = self.hass.data.get(PAIRING_KEY)
                if hub:
                    for relay in hub.sessions.values():
                        if relay.get("portal_owner") == key:
                            relay["portal_owner"] = new_key
                result = {"identity": identity, "expires_in": 900, "scope": SCOPE}
            elif action.startswith("pairing_"):
                hub = self.hass.data.get(PAIRING_KEY)
                if not hub:
                    raise WalletError("Wallet pairing unavailable")
                if action == "pairing_create":
                    result = await hub.create_portal(coord, key, data.get("backend_identity"), origin)
                else:
                    topic = data.get("topic")
                    relay = hub.sessions.get(topic) if isinstance(topic, str) else None
                    if relay and relay.get("portal_owner") != key:
                        raise WalletError("Pairing unavailable")
                    await hub.close(topic)
                    result = {"disconnected": True}
            elif action in monthly_portal.ACTIONS:
                if not item["identity"]:
                    raise WalletError("Sign in to view your sessions")
                # The monthly service takes coord.lock itself (not re-entrant).
                try:
                    result = await monthly_portal.handle(self.hass, coord, origin, item["identity"], data)
                except monthly_portal.MonthlyRefusal as refusal:
                    # Not an authentication failure: the page must keep its sign-in.
                    return web.json_response({"error": monthly_portal.REFUSALS[refusal.code],
                                              "code": refusal.code}, status=409, headers=HEADERS)
            else:
                if not item["identity"]:
                    raise WalletError("Sign in to view your sessions")
                async with coord.lock:
                    api = coord.api
                    if action in portal_registration.ACTIONS:
                        try:
                            result = await portal_registration.handle(api, item["identity"], data)
                        except WalletError:
                            return web.json_response({"error": "Wallet registration is unavailable or its session is already assigned."},
                                                     status=409, headers=HEADERS)
                    elif action in portal_debits.ACTIONS:
                        try:
                            result = await portal_debits.handle(api, item["identity"], data)
                        except WalletError:
                            return web.json_response({"error": "Collection is unavailable or held. No recovery was authorised."},
                                                     status=409, headers=HEADERS)
                    elif action == "sessions":
                        offset = data.get("offset", 0)
                        if type(offset) is not int or not 0 <= offset <= 100000:
                            raise WalletError("Invalid page offset")
                        sessions = history(api, item["identity"])
                        result = {"identity": item["identity"], "sessions": sessions[offset:offset + 25],
                                  "total": len(sessions), "offset": offset, "has_more": offset + 25 < len(sessions),
                                  "expires_in": max(0, int(item["deadline"] - time.monotonic())),
                                  "authorisations": authorisations(api, item["identity"]),
                                  "wallet_metadata": wallet_metadata(api, item["identity"])}
                    else:
                        row, payment, route = credit_owner(api, item["identity"], data.get("credit_id"))
                        if action == "credit_receipt":
                            if data.get("allow_unconfirmed") is True:
                                try:
                                    receipt = await api.auto_credits.receipt_for_item(row, payment, allow_early=True)
                                except WalletError:
                                    return web.json_response({"deferred_until_confirmation": True}, headers=HEADERS)
                                receipt = receipt | {"budget_id": row["terms"]["budget_id"],
                                                     "credit_id": data["credit_id"]}
                            elif route and route.get("adjustment"):
                                receipt = await api.auto_credits.receipt_for_item(row, payment)
                                # The payment ID is not its receiving approval.
                                # Match the ongoing-credit envelope: retain both
                                # IDs, without rewriting the stored payment.
                                receipt = receipt | {
                                    "budget_id": row["terms"]["budget_id"],
                                    "credit_id": data["credit_id"],
                                }
                            else:
                                receipt = await (api.ongoing_credits.driver_receipt(row, data["credit_id"]) if route
                                                 else api.auto_credits.receipt(row))
                            result = {"invitation": copy.deepcopy(row["invitation"]), "receipt": receipt}
                        else:
                            from .receipt_ack import acknowledge
                            result = await acknowledge(api, row, {
                                **({"credit_id": data["credit_id"]} if route else {}),
                                "acknowledgement": data.get("acknowledgement"),
                                "delivery_stage": data.get("delivery_stage")})
            response = web.json_response(result, headers=HEADERS)
            if cookie:
                response.set_cookie(COOKIE, cookie, secure=True, httponly=True, samesite="Strict", path="/",
                                    max_age=900 if action == "login" else 300)
            return response
        except (WalletError, ValueError, TypeError, KeyError, RecursionError):
            # Do not disclose registration existence, signatures, tokens or ledger details.
            return web.json_response({"error": "Sign-in or request could not be verified. Sign in again if needed."},
                                     status=401, headers=HEADERS)
