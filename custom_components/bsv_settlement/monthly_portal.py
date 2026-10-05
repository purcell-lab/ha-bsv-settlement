"""S3 authenticated portal transport for monthly authority. Off unless configured.

Integration startup never configures this module. A reviewed activation (S5)
must place a ``MonthlyPortal`` in ``hass.data[KEY]`` with a ``MonthlyAuthorities``
service whose wallet-period, native-grant and session adapters are real. Until
then every monthly action reports ``disabled`` and nothing is persisted.

Only consent-level operations are exposed to an authenticated driver: read own
status, request/accept a monthly challenge, and request/submit cancellation.
Accounting transitions (bind, reserve, wallet_pending, uncertain, commit,
release) are deliberately absent: a browser can never assert wallet spending.
"""
import copy
from dataclasses import asdict, dataclass
from urllib.parse import urlparse

from .api import WalletError
from .const import DOMAIN
from .monthly_allowance import Month
from .monthly_authority import MonthlyAuthorities, decode
from .monthly_consent import PROTOCOL, approval_payload, cancellation_payload, timestamp

KEY = DOMAIN + "_monthly_portal"
ACTIONS = ("monthly_status", "monthly_challenge", "monthly_accept",
           "monthly_cancel_challenge", "monthly_cancel")
# Browser-visible refusal codes. Fixed text only: never echo ledger details.
REFUSALS = {
    "disabled": "Monthly charging is not enabled at this station.",
    "revision_conflict": "Your monthly authority changed in another window. Reload before trying again.",
    "exists": "This wallet already has a monthly authority.",
    "cancelled": "Monthly charging was cancelled for this wallet. A new authority needs an operator-reviewed amendment.",
    "expired": "The monthly approval request expired. Start again.",
    "invalid_proof": "The wallet signature did not match the monthly terms.",
    "none": "No monthly authority exists for this wallet.",
    "unavailable": "Monthly charging could not be updated. Nothing was charged.",
}


class MonthlyRefusal(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code if code in REFUSALS else "unavailable"


@dataclass(frozen=True)
class MonthlyPortal:
    """Reviewed activation record. Constructing it is an operator decision."""

    service: MonthlyAuthorities
    station_ids: tuple
    policy_id: str


def hostname(origin):
    return urlparse(origin).hostname or ""


def configured(hass, coord, origin):
    """Fail closed unless an activation for exactly this wallet and origin exists."""
    portal = hass.data.get(KEY)
    if (not isinstance(portal, MonthlyPortal)
            or portal.service.coordinator is not coord
            or portal.service.origin != hostname(origin)
            or portal.policy_id not in portal.service.policies):
        return None
    return portal


def public_station(hass, coord, origin):
    """Public station facts only: no driver, session, ledger or private link data."""
    portal = configured(hass, coord, origin)
    if portal is None:
        return {"monthly_enabled": False, "station_ids": []}
    return {"monthly_enabled": True, "station_ids": list(portal.station_ids),
            "operator_identity": coord.api.identity["public_key"], "monthly_limit_sats": 30_000}


def _refusal(exc):
    text = str(exc)
    for needle, code in (("revision conflict", "revision_conflict"),
                         ("without resetting", "exists"), ("owns this driver", "exists"),
                         ("cancelled", "cancelled"), ("expired", "expired"),
                         ("Invalid wallet-signed", "invalid_proof")):
        if needle in text:
            return MonthlyRefusal(code)
    return MonthlyRefusal("unavailable")


def _own(state, identity):
    return next(((aid, row) for aid, row in state["authorities"].items()
                 if row["terms"]["driver_identity"] == identity), (None, None))


def _open_challenge(service, state, identity):
    """Reuse an unexpired request instead of growing storage on repeated clicks."""
    now = service._now()
    for aid, challenge in state["challenges"].items():
        terms = challenge["terms"]
        if (not challenge["used"] and terms["driver_identity"] == identity
                and timestamp(terms["issued_at"]) <= now < timestamp(terms["accept_before"])):
            return aid, terms
    return None, None


def receiving(api, identity):
    """Whether operator credits have a verified destination for this identity."""
    from .portal import ownership
    registered = False
    for row in ownership(api, identity).values():
        if not row.get("credit_destination"):
            continue
        try:
            registered = api.ongoing_credits.verified_registration(row)["driver_identity"] == identity
        except WalletError:
            continue
        if registered:
            break
    try:
        routes = api.ongoing_credits.latest()["driver_identity"] == identity
    except (WalletError, KeyError, TypeError, ValueError):
        routes = False
    return {"registered": registered, "routes_new_credits": routes}


def _challenge_view(terms, revision):
    return {"authority_id": terms["authority_id"], "terms": copy.deepcopy(terms),
            "payload": approval_payload(terms), "protocolID": list(PROTOCOL),
            "keyID": terms["authority_id"], "revision": revision}


async def status(portal, api, identity):
    """Owner-only projection. Never includes other drivers or internal evidence."""
    service = portal.service
    state = service.snapshot()
    aid, row = _own(state, identity)
    missing = []
    result = {"enabled": True, "revision": state["revision"], "station_ids": list(portal.station_ids),
              "monthly_limit_sats": 30_000, "authority": None, "allowance": None,
              "native_grant": {"state": "not_applicable"}, "receiving": receiving(api, identity)}
    pending, _ = _open_challenge(service, state, identity)
    result["challenge_pending"] = pending is not None
    if row is None:
        missing.append("monthly_authority")
    else:
        ledger = decode(row["ledger"])
        terms = row["terms"]
        result["authority"] = {
            "authority_id": aid, "state": "cancelled" if ledger.cancelled else "active",
            "accepted_at": row["accepted_at"], "station_ids": copy.deepcopy(terms["station_ids"]),
            "period": {k: terms["period_policy"][k] for k in ("policy_id", "wallet_version", "timezone")},
            "wallet_permission_revoked": "not_verified" if ledger.cancelled else None}
        month = Month.at(service._now(), wallet_timezone=terms["period_policy"]["timezone"])
        try:
            summary = asdict(ledger.summary(month))
            summary["reasons"] = list(summary["reasons"])
        except Exception:  # Period regression etc.: show blocked, never a fresh allowance.
            summary = {"limit_sats": ledger.limit_sats, "blocked": True, "reasons": ["reconcile_required"],
                       "spent_sats": None, "reserved_sats": None, "remaining_sats": 0, "over_limit_sats": None}
        result["allowance"] = {"month": asdict(month), **summary}
        if ledger.cancelled:
            missing.append("monthly_authority")
        elif summary["blocked"]:
            missing.append("allowance_review")
        if not ledger.cancelled:
            try:
                grant = await service.observe_grant(aid)
                result["native_grant"] = {"state": "verified", **grant}
            except Exception:  # Any adapter failure is missing evidence, never readiness.
                result["native_grant"] = {"state": "unverified"}
                missing.append("wallet_monthly_permission")
    if not result["receiving"]["registered"]:
        missing.append("receiving_registration")
    result["readiness"] = {"automatic_collection": not missing, "missing": missing}
    return result


async def handle(hass, coord, origin, identity, data):
    """Dispatch one monthly action for an authenticated portal identity."""
    action = data.get("action")
    if action not in ACTIONS:
        raise MonthlyRefusal("unavailable")
    portal = configured(hass, coord, origin)
    if portal is None:
        if action == "monthly_status":
            return {"enabled": False, "readiness": {"automatic_collection": False, "missing": ["monthly_disabled"]}}
        raise MonthlyRefusal("disabled")
    service = portal.service
    try:
        if action == "monthly_status":
            return await status(portal, coord.api, identity)
        revision = data.get("revision")
        if type(revision) is not int:
            raise MonthlyRefusal("revision_conflict")
        state = service.snapshot()
        aid, row = _own(state, identity)
        if action == "monthly_challenge":
            if row is not None:
                raise MonthlyRefusal("cancelled" if decode(row["ledger"]).cancelled else "exists")
            pending, terms = _open_challenge(service, state, identity)
            if pending is not None:
                return _challenge_view(terms, state["revision"])
            issued = await service.issue(driver_identity=identity, station_ids=list(portal.station_ids),
                                         policy_id=portal.policy_id, expected_revision=revision)
            return _challenge_view(issued["terms"], issued["revision"])
        if action == "monthly_accept":
            authority_id, proof = data.get("authority_id"), data.get("proof")
            challenge = state["challenges"].get(authority_id) if isinstance(authority_id, str) else None
            # Another driver's challenge is indistinguishable from an unknown one.
            if challenge is None or challenge["terms"]["driver_identity"] != identity:
                raise MonthlyRefusal("expired")
            accepted = await service.accept(authority_id, proof, expected_revision=revision)
            return {"accepted": True, "authority_id": authority_id, "accepted_at": accepted["accepted_at"],
                    "revision": service.snapshot()["revision"]}
        if row is None:
            raise MonthlyRefusal("none")
        if action == "monthly_cancel_challenge":
            return {"authority_id": aid, "payload": cancellation_payload(row["terms"]),
                    "protocolID": list(PROTOCOL), "keyID": aid, "revision": state["revision"]}
        result = await service.cancel(aid, data.get("proof"), expected_revision=revision)
        return {"cancelled": True, "wallet_permission_revoked": result["wallet_permission_revoked"],
                "revision": result["revision"]}
    except MonthlyRefusal:
        raise
    except WalletError as exc:
        raise _refusal(exc) from None
    except Exception:
        # Includes a failed durable write: the service blocks itself until reload.
        raise MonthlyRefusal("unavailable") from None
