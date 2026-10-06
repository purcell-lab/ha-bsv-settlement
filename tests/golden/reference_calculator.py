#!/usr/bin/env python3
"""Independent reference calculator for golden session accounts.

Written from the written policy (docs/golden-accounts.md and
docs/tariff-overlap-reconciliation.md), NOT from the production code. It imports
nothing from ``custom_components`` and uses only the Python standard library.

Method (deliberately different from production):

* Every timestamp is converted to an integer UTC epoch second. Wall-clock time,
  time zones and DST play no part after parsing the explicit ISO offset.
* The session is evaluated one second at a time. For each second the running
  state and the effective tariff are looked up directly from the source rows.
* Arithmetic is exact rational (``fractions.Fraction``); nothing is rounded until
  the final AUD amount (to cents) and the satoshi conversion (to whole sats),
  both ROUND_HALF_UP (ties away from zero).

Usage, for an external reviewer::

    python3 reference_calculator.py fixtures/g01_brisbane_import.json [...]

prints the recomputed account for each fixture and whether it matches the
``expected`` block recorded in the file. Exit status 1 on any mismatch.
"""
from datetime import datetime
from functools import lru_cache
from fractions import Fraction
import json
import sys

PREPARING = {"Occupied", "Preparing Comm", "Preparing Insulation"}
ACTIVE = {"Charging", "Discharging"}
TERMINAL = {"Ended", "Idle"}
MATCHING = {"import": "Charging", "export": "Discharging"}
PRICE_UNIT = "$/kWh"
ENERGY_UNIT = "MWh"


@lru_cache(maxsize=None)
def epoch(text):
    """ISO 8601 with an explicit offset -> integer UTC seconds."""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"timestamp without offset: {text}")
    seconds = moment.timestamp()
    if seconds != int(seconds):
        raise ValueError(f"reference requires whole-second timestamps: {text}")
    return int(seconds)


def exact(text):
    """Decimal string -> exact Fraction (never via float)."""
    return Fraction(str(text))


def round_half_up(value, quantum):
    """Round a Fraction to a multiple of ``quantum`` with ties away from zero."""
    sign = -1 if value < 0 else 1
    steps = (abs(value) / quantum + Fraction(1, 2)).__floor__()
    return sign * steps * quantum


def session_window(states):
    """Open at the first preparing/active state, close at the next terminal state."""
    opened = closed = None
    for row in sorted(states, key=lambda r: epoch(r["t"])):
        if opened is None and row["value"] in PREPARING | ACTIVE:
            opened = row
        elif opened is not None and row["value"] in TERMINAL:
            closed = row
            break
    if opened is None or closed is None:
        raise ValueError("fixture must contain one closed session")
    return opened, closed


def state_at(states, second):
    current = None
    for row in states:
        if epoch(row["t"]) <= second:
            current = row["value"]
    return current


def tariff_at(rows, second):
    """(rate | None, is_final) for one second, applying the documented policy.

    1. Ignore rows without the $/kWh unit, a decimal value, or a valid period.
    2. For rows with identical effective bounds keep the best (final first,
       then latest observation). Equal best rows with different rates conflict.
    3. If any effective period covering this second is final, only finals count.
       Otherwise use the newest-observed estimate; ties go to the narrower period.
    4. More than one surviving rate is a conflict: the second is unpriced.
    """
    groups = {}
    for row in rows:
        if row.get("unit") != PRICE_UNIT:
            continue
        try:
            rate = exact(row["value"])
            start, end, seen = epoch(row["start"]), epoch(row["end"]), epoch(row["t"])
        except (KeyError, ValueError, ZeroDivisionError):
            continue
        if not start <= second < end:
            continue
        final = row.get("estimate") is False
        key = (start, end)
        rank = (final, seen)
        best = groups.get(key)
        if best is None or rank > best["rank"]:
            groups[key] = {"rank": rank, "rates": {rate}, "final": final, "width": end - start,
                           "seen": seen}
        elif rank == best["rank"]:
            best["rates"].add(rate)
    if not groups:
        return None, False
    finals = [g for g in groups.values() if g["final"]]
    if finals:
        winners = finals
    else:
        newest = max(g["seen"] for g in groups.values())
        latest = [g for g in groups.values() if g["seen"] == newest]
        narrowest = min(g["width"] for g in latest)
        winners = [g for g in latest if g["width"] == narrowest]
    rates = set().union(*(g["rates"] for g in winners))
    return (rates.pop() if len(rates) == 1 else None), bool(finals)


def direction_account(fixture, direction, open_s, close_s):
    obs = fixture["observations"]
    states = sorted(obs["state"], key=lambda r: epoch(r["t"]))
    readings = sorted(obs[direction], key=lambda r: epoch(r["t"]))
    prices = obs[direction + "_price"]
    baseline = [r for r in readings if epoch(r["t"]) <= open_s]
    if not baseline or baseline[-1].get("unit") != ENERGY_UNIT:
        raise ValueError(f"{direction}: missing MWh counter baseline")
    previous_value, previous_s = exact(baseline[-1]["value"]), open_s
    wh = cost = unpriced = estimated = Fraction(0)
    for row in readings:
        stamp = epoch(row["t"])
        if stamp <= open_s or stamp > close_s:
            continue
        value = exact(row["value"])
        if row.get("unit") != ENERGY_UNIT or value < previous_value:
            raise ValueError("golden fixtures must not contain invalid or decreasing counters")
        energy = (value - previous_value) * 1_000_000          # MWh -> Wh
        if energy:
            seconds = list(range(previous_s, stamp))
            matching = [s for s in seconds if state_at(states, s) == MATCHING[direction]]
            spread = matching or seconds                        # no matching state: whole interval
            share = energy / len(spread)
            for second in spread:
                rate, final = tariff_at(prices, second)
                if rate is None:
                    unpriced += share
                    continue
                cost += share / 1000 * rate                    # Wh -> kWh x $/kWh
                if not final:
                    estimated += share
            wh += energy
        previous_value, previous_s = value, stamp
    return {"wh": wh, "aud": cost, "unpriced_wh": unpriced, "estimated_wh": estimated}


def fraction_text(value):
    return f"{value.numerator}/{value.denominator}"


def decimal_text(value, places=12):
    """Display only: exact value rounded half-up to ``places`` decimals."""
    rounded = round_half_up(value, Fraction(1, 10 ** places))
    sign = "-" if rounded < 0 else ""
    scaled = abs(rounded) * 10 ** places
    whole, part = divmod(int(scaled), 10 ** places)
    return f"{sign}{whole}.{part:0{places}d}"


def calculate(fixture):
    opened, closed = session_window(fixture["observations"]["state"])
    open_s, close_s = epoch(opened["t"]), epoch(closed["t"])
    imp = direction_account(fixture, "import", open_s, close_s)
    exp = direction_account(fixture, "export", open_s, close_s)
    complete = imp["unpriced_wh"] == 0 and exp["unpriced_wh"] == 0
    net = imp["aud"] - exp["aud"]
    result = {
        "opened_at": opened["t"], "ended_at": closed["t"],
        "duration_seconds": close_s - open_s,
        "import_wh": fraction_text(imp["wh"]), "export_wh": fraction_text(exp["wh"]),
        "import_cost_aud_exact": fraction_text(imp["aud"]),
        "export_credit_aud_exact": fraction_text(exp["aud"]),
        "import_cost_aud": decimal_text(imp["aud"]),
        "export_credit_aud": decimal_text(exp["aud"]),
        "unpriced_import_wh": fraction_text(imp["unpriced_wh"]),
        "unpriced_export_wh": fraction_text(exp["unpriced_wh"]),
        "estimated_rate_import_wh": fraction_text(imp["estimated_wh"]),
        "estimated_rate_export_wh": fraction_text(exp["estimated_wh"]),
        "pricing_complete": complete,
    }
    if not complete:
        return result | {"net_aud_exact": None, "net_aud": None, "direction": None,
                         "amount_sats": None}
    cents = round_half_up(net, Fraction(1, 100))
    rate = exact(fixture["sat_per_aud"])
    sats = int(round_half_up(abs(cents) * rate, Fraction(1)))
    return result | {
        "net_aud_exact": fraction_text(net), "net_aud_unrounded": decimal_text(net),
        "net_aud": decimal_text(cents, 2),
        "direction": ("driver_to_operator" if cents > 0 else
                      "operator_to_driver" if cents < 0 else "none"),
        "amount_sats": sats,
    }


def main(paths):
    failed = False
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            fixture = json.load(handle)
        result = calculate(fixture)
        match = result == fixture.get("expected")
        failed |= not match
        print(json.dumps({"fixture": fixture["id"], "matches_expected": match,
                          "account": result}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
