"""Fresh provider-quoted fees for the bounded one-input/two-P2PKH-output wallet."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_CEILING

from .api import WalletError

SOURCE = "https://api.whatsonchain.com/v1/bsv/main/feerecommendation"
MODE = "provider_quote_per_transaction"
# 119 unsigned bytes + at most 108 bytes for a compressed-key P2PKH unlock.
# Verified again against actual signed bytes; not valid for other transaction shapes.
MAX_SIGNED_BYTES = 227
QUOTE_TTL_SECONDS = 60


def now():
    return datetime.now(timezone.utc)


def rate_value(value):
    if type(value) not in (int, float):
        raise WalletError("Fee quote must contain numeric sat/KB rates")
    rate = Decimal(str(value))
    if not rate.is_finite() or rate < 0:
        raise WalletError("Fee quote rate is invalid")
    return rate


def fee_for_bytes(rate, size):
    if type(size) is not int or not 1 <= size <= MAX_SIGNED_BYTES:
        raise WalletError("Transaction exceeds the supported signed-size bound")
    return max(1, int((rate * size / 1000).to_integral_value(rounding=ROUND_CEILING)))


async def quote(chain):
    data = await chain.fee_policy()
    if not isinstance(data, dict) or data.get("fee_unit") != "sat/KB":
        raise WalletError("Fee quote has missing or unsupported units")
    recommended = rate_value(data.get("fee"))
    minimum = rate_value(data.get("mempool_min_fee"))
    rate = max(recommended, minimum)
    if rate <= 0:
        raise WalletError("Fee quote has no positive rate; no fixed-fee fallback")
    return {"source": SOURCE, "unit": "sat/KB", "mode": MODE,
            "recommended_sat_per_kb": str(recommended),
            "mempool_min_sat_per_kb": str(minimum),
            "rate_sat_per_kb": str(rate), "estimated_signed_bytes": MAX_SIGNED_BYTES,
            "fee_sats": fee_for_bytes(rate, MAX_SIGNED_BYTES),
            "observed_at": now().isoformat(), "max_age_seconds": QUOTE_TTL_SECONDS}


def validate(quotation, fee, actual_bytes=MAX_SIGNED_BYTES):
    try:
        age = (now() - datetime.fromisoformat(quotation["observed_at"])).total_seconds()
        rate = Decimal(quotation["rate_sat_per_kb"])
        if (not 0 <= age <= QUOTE_TTL_SECONDS or not rate.is_finite() or rate <= 0
                or quotation["unit"] != "sat/KB" or quotation["source"] != SOURCE):
            raise ValueError()
    except (ValueError, TypeError, KeyError, InvalidOperation):
        raise WalletError("Fee quote is stale or invalid; obtain a fresh quote") from None
    minimum = fee_for_bytes(rate, actual_bytes)
    if type(fee) is not int or fee < minimum:
        raise WalletError(f"Reviewed fee is below the current quoted minimum of {minimum} sat; prepare and approve a new unsigned draft")
