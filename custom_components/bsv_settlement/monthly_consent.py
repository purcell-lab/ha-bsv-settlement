"""Version 4 monthly app consent. Not a native wallet spending grant.

Internal S2 API only: no HTTP or HA service is registered. Wallet capability
policies come from reviewed server configuration, never a driver's request.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re

from bsv import PrivateKey, PublicKey

from .api import WalletError
from .budget import canonical, message_hash, sha
from .monthly_allowance import DEFAULT_MONTHLY_LIMIT_SATS, Month

VERSION = 4
PROTOCOL = [2, "ev monthly spending"]
SCOPE = "recurring_calendar_month_charging_including_driver_fees"
IDENTITY = re.compile(r"^(02|03)[0-9a-f]{64}$")
TOKEN = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
ORIGIN = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def exact(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise WalletError("Invalid monthly record fields")


def text(value):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise WalletError("Invalid monthly text field")
    return value


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (TypeError, ValueError):
        raise WalletError("Monthly timestamps must include a timezone") from None


def public_key(value):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise WalletError("Invalid monthly wallet identity")
    try:
        PublicKey(bytes.fromhex(value))
    except Exception:
        raise WalletError("Invalid monthly wallet identity") from None
    return value


@dataclass(frozen=True)
class WalletPeriodPolicy:
    """Trusted compatibility evidence supplied by a reviewed wallet adapter.

    No production policy is shipped or inferred. This describes wallet/version
    behaviour, not evidence that an individual driver has granted permission.
    """

    policy_id: str
    wallet_version: str
    timezone: str
    booking_event: str
    fee_basis: str
    evidence_ref: str

    def __post_init__(self):
        for value in (self.policy_id, self.wallet_version, self.booking_event,
                      self.fee_basis, self.evidence_ref):
            text(value)
        Month(2000, 1, self.timezone)
        if self.fee_basis != "all_driver_paid_wallet_debits":
            raise WalletError("Monthly fee accounting has not been aligned")

    def terms(self):
        return dict(policy_id=self.policy_id, wallet_version=self.wallet_version,
                    timezone=self.timezone, booking_event=self.booking_event,
                    fee_basis=self.fee_basis, evidence_ref=self.evidence_ref)


def validate_terms(terms):
    exact(terms, (
        "version", "scope", "network", "authority_id", "nonce", "driver_identity",
        "operator_identity", "operator_address", "origin", "station_ids",
        "monthly_limit_sats", "period_policy", "issued_at", "accept_before",
        "effective_at", "recurs_until_cancelled", "credits_refill",
        "unused_carries_forward", "conversion_policy", "included_session", "collection_policy",
    ))
    if (type(terms["version"]) is not int or terms["version"] != VERSION
            or terms["scope"] != SCOPE
            or terms["network"] != "BSV mainnet"
            or terms["collection_policy"] != "one_final_net_payment_per_session_on_closure"
            or type(terms["monthly_limit_sats"]) is not int
            or terms["monthly_limit_sats"] != DEFAULT_MONTHLY_LIMIT_SATS
            or terms["recurs_until_cancelled"] is not True
            or terms["credits_refill"] is not False
            or terms["unused_carries_forward"] is not False):
        raise WalletError("Unsupported monthly authority contract")
    if not TOKEN.fullmatch(text(terms["authority_id"])) or not HEX64.fullmatch(text(terms["nonce"])):
        raise WalletError("Invalid monthly challenge identifiers")
    public_key(terms["driver_identity"])
    public_key(terms["operator_identity"])
    if not ORIGIN.fullmatch(text(terms["origin"])):
        raise WalletError("Use an exact reviewed HTTPS origin hostname")
    # Existing mainnet operator is the payee. Arbitrary change of payment script
    # requires a new consent design, not an unverified address string.
    if terms["operator_address"] != PublicKey(bytes.fromhex(terms["operator_identity"])).address():
        raise WalletError("Monthly payee does not match the operator identity")
    stations = terms["station_ids"]
    if (not isinstance(stations, list) or not stations or len(stations) > 100
            or any(not isinstance(s, str) or not TOKEN.fullmatch(s) for s in stations)
            or stations != sorted(set(stations))):
        raise WalletError("Monthly station scope must be explicit and unique")
    policy = terms["period_policy"]
    exact(policy, ("policy_id", "wallet_version", "timezone", "booking_event", "fee_basis", "evidence_ref"))
    WalletPeriodPolicy(**policy)
    issued, before, effective = (timestamp(terms[k]) for k in ("issued_at", "accept_before", "effective_at"))
    if not 0 < (before - issued).total_seconds() <= 600 or effective != issued:
        raise WalletError("Invalid monthly challenge acceptance window")
    if terms["conversion_policy"] != "freeze_configured_sat_per_aud_at_session_binding":
        raise WalletError("Unsupported monthly conversion policy")
    included = terms["included_session"]
    if included is not None:
        exact(included, ("account_key", "transaction_id", "opened_at"))
        text(included["account_key"])
        text(included["transaction_id"])
        if timestamp(included["opened_at"]) > issued:
            raise WalletError("Included current session has not opened")
    return terms


def approval_payload(terms):
    validate_terms(terms)
    return canonical({"version": VERSION, "action": "authorise_monthly_charging",
                      "terms": terms})


def cancellation_payload(terms):
    validate_terms(terms)
    return canonical({"version": VERSION, "action": "cancel_monthly_charging",
                      "authority_id": terms["authority_id"],
                      "driver_identity": terms["driver_identity"],
                      "terms_hash": sha(approval_payload(terms))})


def verify_proof(terms, proof, *, cancellation=False):
    expected = cancellation_payload(terms) if cancellation else approval_payload(terms)
    try:
        exact(proof, ("payload", "signature"))
        if proof["payload"] != expected:
            raise ValueError()
        signature = bytes.fromhex(proof["signature"])
        child = PublicKey(bytes.fromhex(terms["driver_identity"])).derive_child(
            PrivateKey(1), f"2-{PROTOCOL[1]}-{terms['authority_id']}")
        if not 8 <= len(signature) <= 72 or not child.verify(
                signature, expected.encode(), hasher=message_hash):
            raise ValueError()
    except Exception:
        raise WalletError("Invalid wallet-signed monthly consent") from None


def conversion_rate(value):
    """Accept a finite, positive decimal string, not floats or booleans."""
    if not isinstance(value, str):
        raise WalletError("Invalid frozen monthly conversion")
    try:
        rate = Decimal(value)
        if not rate.is_finite() or not 0 < rate <= 100_000_000:
            raise ValueError()
        return str(rate)
    except (InvalidOperation, ValueError):
        raise WalletError("Invalid frozen monthly conversion") from None
