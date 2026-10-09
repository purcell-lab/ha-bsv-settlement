"""Per-request wallet signatures for state-changing driver portal actions.

The sign-in cookie proves a recent wallet login, not consent to a specific
change. Each action in ``SIGNED_ACTIONS`` needs a fresh signature by the
signed-in wallet over the exact request body, bound to this origin, this
browser's sign-in and a single-use server nonce. The signed bytes are parsed as
the request, so no canonical re-encoding has to match between client and server.

This adds a proof; it never replaces the existing claim, draft, transaction or
receipt checks, and it is not a spending mandate.
"""
import json
import re
import secrets
import time

from bsv import PrivateKey, PublicKey

from .api import WalletError
from .budget import message_hash

PROTOCOL = "ev portal request"
PURPOSE = "signed_driver_portal_request"
SIGNED_ACTIONS = ("registration_offer", "debit_authorise", "debit_failure")
LIFETIME = 120
MAX_PENDING = 8
NONCE = re.compile(r"^[A-Za-z0-9_-]{43}$")
SIGNATURE = re.compile(r"^[0-9a-f]{16,144}$")
FIELDS = {"version", "action", "origin", "identity", "browser_binding", "nonce",
          "issued_at", "expires_at", "request"}


class RequestRefused(Exception):
    """A missing, expired, replayed or invalid request signature."""


def _pending(item):
    now = time.monotonic()
    pending = {n: g for n, g in item.get("request_nonces", {}).items() if g["deadline"] > now}
    item["request_nonces"] = pending
    return pending


def issue(item, key, origin):
    """Return a single-use grant for the signed-in wallet in this browser."""
    if not item.get("identity"):
        raise WalletError("Sign in to view your sessions")
    pending = _pending(item)
    if len(pending) >= MAX_PENDING:
        raise WalletError("Too many unsigned requests; try again shortly")
    issued = int(time.time())
    grant = {"nonce": secrets.token_urlsafe(32), "browser_binding": key, "identity": item["identity"],
             "origin": origin, "issued_at": issued, "expires_at": issued + LIFETIME}
    pending[grant["nonce"]] = grant | {"deadline": time.monotonic() + LIFETIME}
    return {k: grant[k] for k in ("nonce", "browser_binding", "identity", "issued_at", "expires_at")} | {
        "protocolID": [2, PROTOCOL], "keyID": grant["nonce"]}


def verify(item, key, origin, action, data):
    """Return the signed request body, consuming its nonce even on failure."""
    signed = data.get("signed_request")
    if set(data) != {"action", "signed_request"} or not isinstance(signed, dict):
        raise RequestRefused()
    payload, signature = signed.get("payload"), signed.get("signature")
    if (set(signed) != {"payload", "signature"} or not isinstance(payload, str) or len(payload) > 18000
            or not isinstance(signature, str) or not SIGNATURE.fullmatch(signature)):
        raise RequestRefused()
    try:
        p = json.loads(payload)
    except (ValueError, RecursionError):
        raise RequestRefused() from None
    if not isinstance(p, dict) or set(p) != FIELDS or not isinstance(p["nonce"], str):
        raise RequestRefused()
    grant = _pending(item).pop(p["nonce"], None)  # Single use, including failed attempts.
    request = p["request"]
    if (grant is None or not NONCE.fullmatch(p["nonce"])
            or type(p["version"]) is not int or p["version"] != 1 or p["action"] != PURPOSE
            or p["origin"] != origin or grant["origin"] != origin
            or p["identity"] != item.get("identity") or grant["identity"] != item.get("identity")
            or p["browser_binding"] != key or grant["browser_binding"] != key
            or type(p["issued_at"]) is not int or p["issued_at"] != grant["issued_at"]
            or type(p["expires_at"]) is not int or p["expires_at"] != grant["expires_at"]
            or time.time() > p["expires_at"]
            or not isinstance(request, dict) or request.get("action") != action
            or "signed_request" in request):
        raise RequestRefused()
    try:
        verifier = PublicKey(bytes.fromhex(p["identity"])).derive_child(
            PrivateKey(1), f"2-{PROTOCOL}-{p['nonce']}")
        valid = verifier.verify(bytes.fromhex(signature), payload.encode(), hasher=message_hash)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise RequestRefused()
    return request
