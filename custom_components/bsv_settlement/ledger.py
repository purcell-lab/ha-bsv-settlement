"""Pure, decimal-based session ledger. No Home Assistant dependencies."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json


def timestamp(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.utcoffset() is None:
        raise ValueError("Timestamps require a timezone")
    return dt.astimezone(timezone.utc)


def decimal(value):
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("Invalid decimal") from exc
    if not result.is_finite() or abs(result) > Decimal("1000000"):
        raise ValueError("Invalid or out-of-range decimal")
    return result


def energy(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 10000000:
        raise ValueError("Directional energy must be a nonnegative integer Wh quantity")
    return value


def validate_interval(interval):
    required = {"start", "end", "import_wh", "export_wh",
                "import_price_aud_per_kwh", "export_price_aud_per_kwh",
                "price_status", "tariff_version", "meter_quality"}
    if set(interval) != required:
        raise ValueError("Interval fields must exactly match the documented schema")
    if timestamp(interval["end"]) <= timestamp(interval["start"]):
        raise ValueError("Interval end must follow start")
    energy(interval["import_wh"])
    energy(interval["export_wh"])
    decimal(interval["import_price_aud_per_kwh"])
    decimal(interval["export_price_aud_per_kwh"])
    if interval["price_status"] != "final" or interval["meter_quality"] != "validated":
        raise ValueError("Only final prices and validated meter intervals can be used")
    if not isinstance(interval["tariff_version"], str) or not interval["tariff_version"]:
        raise ValueError("A tariff version is required")


def freeze(session, ended_at, final_import_wh, final_export_wh):
    intervals = sorted(session["intervals"], key=lambda x: timestamp(x["start"]))
    if not intervals:
        raise ValueError("At least one measured interval is required")
    cursor = timestamp(session["started_at"])
    imported = exported = 0
    costs = credits = Decimal("0")
    for interval in intervals:
        validate_interval(interval)
        if timestamp(interval["start"]) != cursor:
            raise ValueError("Intervals must cover the session without gaps or overlaps")
        cursor = timestamp(interval["end"])
        imported += interval["import_wh"]
        exported += interval["export_wh"]
        costs += Decimal(interval["import_wh"]) / 1000 * decimal(interval["import_price_aud_per_kwh"])
        credits += Decimal(interval["export_wh"]) / 1000 * decimal(interval["export_price_aud_per_kwh"])
    if cursor != timestamp(ended_at):
        raise ValueError("Final interval must end at session end")
    if (imported, exported) != (energy(final_import_wh), energy(final_export_wh)):
        raise ValueError("Interval sums do not reconcile with final session energy")
    raw = costs - credits
    rounded = raw.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
    evidence = {"session_id": session["session_id"], "started_at": session["started_at"],
                "ended_at": ended_at, "driver_binding_id": session["driver_binding_id"],
                "intervals": intervals, "final_import_wh": final_import_wh,
                "final_export_wh": final_export_wh}
    digest = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"session_id": session["session_id"], "ledger_revision": 1,
            "ledger_sha256": digest, "driver_binding_id": session["driver_binding_id"],
            "operator_binding_id": "operator-demo-01", "currency": "AUD",
            "net_amount_minor": int(rounded * 100), "import_wh": imported, "export_wh": exported,
            "pricing_summary": {"import_amount_aud": str(costs), "export_credit_aud": str(credits),
                                "rounding_adjustment_aud": str(rounded - raw), "prices_final": True},
            "ended_at": ended_at}
