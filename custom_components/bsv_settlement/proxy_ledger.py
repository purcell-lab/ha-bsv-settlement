"""Pure, read-only reconstruction and provisional pricing of sensor sessions."""
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import NAMESPACE_URL, uuid5

ACTIVE = {"Charging", "Discharging"}
PREPARING = {"Occupied", "Preparing Comm", "Preparing Insulation"}
TERMINAL = {"Ended", "Idle"}
D = Decimal


def instant(value):
    return datetime.fromisoformat(value)


def number(value):
    try:
        result = D(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def duration(start, end):
    delta = end - start
    return D(delta.days * 86400 + delta.seconds) + D(delta.microseconds) / 1_000_000


def intersect(a, b, c, d):
    start, end = max(a, c), min(b, d)
    return (start, end) if start < end else None


def select_prices(rows):
    selected = {}
    for row in rows:
        if row.get("unit") != "$/kWh":
            continue
        rate = number(row["value"])
        try:
            start, end = instant(row["start"]), instant(row["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if rate is None or start >= end:
            continue
        final = row.get("estimate") is False
        priority = (final, instant(row["t"]))
        key = (start, end)
        if key not in selected or priority > selected[key]["priority"]:
            selected[key] = {"start": start, "end": end, "rate": rate,
                             "final": final, "priority": priority}
    result = sorted(selected.values(), key=lambda p: p["start"])
    if any(a["end"] > b["start"] for a, b in zip(result, result[1:])):
        return [], ["overlapping_tariff_periods"]
    return result, []


def infer_sessions(rows):
    sessions, current, previous = [], None, None
    for row in rows:
        state = row["value"]
        if state == previous:
            continue
        previous = state
        if current is None and state in PREPARING | ACTIVE:
            current = {"opened_at": row["t"], "energy_started_at": None,
                       "ended_at": None, "transitions": [],
                       "partial_start": row is rows[0]}
        if current is not None:
            current["transitions"].append(row)
            if state in ACTIVE and current["energy_started_at"] is None:
                current["energy_started_at"] = row["t"]
            if state in TERMINAL:
                current["ended_at"] = row["t"]
                if current["energy_started_at"]:
                    sessions.append(current)
                current = None
    if current and current["energy_started_at"]:
        sessions.append(current)
    return sessions


def price_direction(session, rows, prices, direction, as_of):
    """Quarantine decreases; never count recovery from a transient zero as energy."""
    start, end = instant(session["opened_at"]), instant(session["ended_at"] or as_of)
    before = [r for r in rows if instant(r["t"]) <= start]
    issues = set()
    if not before:
        return {"wh": None, "amount": None, "unpriced_wh": None,
                "estimated_rate_wh": None}, ["missing_counter_baseline"]
    baseline = before[-1]
    prev = number(baseline["value"])
    if baseline.get("unit") != "MWh" or prev is None or prev < 0:
        return {"wh": None, "amount": None, "unpriced_wh": None,
                "estimated_rate_wh": None}, ["invalid_counter_baseline"]
    previous_time = start
    segments = []
    matching = "Charging" if direction == "import" else "Discharging"
    transitions = session["transitions"]
    for index, row in enumerate(transitions):
        following = instant(transitions[index+1]["t"]) if index+1 < len(transitions) else end
        if row["value"] == matching and instant(row["t"]) < following:
            segments.append((instant(row["t"]), following))
    energy_total = D(0)
    amount = D(0)
    unpriced = D(0)
    estimated = D(0)
    held = False
    for row in rows:
        stamp = instant(row["t"])
        if stamp <= start or stamp > end:
            continue
        value = number(row["value"])
        if row.get("unit") != "MWh" or value is None or value < 0:
            held = True
            issues.add("invalid_counter_reading")
            continue
        if value < prev:
            held = True
            issues.add("counter_decrease_quarantined")
            continue
        held = False
        energy = (value - prev) * 1_000_000
        if energy:
            spans = [part for a, b in segments
                     if (part := intersect(previous_time, stamp, a, b))]
            if not spans:
                spans = [(previous_time, stamp)]
                issues.add("energy_without_matching_state")
            length = sum((duration(a, b) for a, b in spans), D(0))
            if length <= 0:
                issues.add("nonpositive_sample_duration")
                held = True
                continue
            covered = D(0)
            for a, b in spans:
                for tariff in prices:
                    part = intersect(a, b, tariff["start"], tariff["end"])
                    if not part:
                        continue
                    wh = energy * duration(*part) / length
                    covered += wh
                    amount += wh / 1000 * tariff["rate"]
                    if not tariff["final"]:
                        estimated += wh
            if energy - covered > D("1e-12"):
                unpriced += energy - covered
            energy_total += energy
        prev, previous_time = value, stamp
    if held:
        issues.add("counter_not_recovered")
    if unpriced > D("1e-12"):
        issues.add("missing_tariff")
    if estimated:
        issues.add("estimated_tariff")
    return {"wh": None if held else str(energy_total),
            "amount": None if held or unpriced > D("1e-12") else str(amount),
            "unpriced_wh": str(unpriced), "estimated_rate_wh": str(estimated)}, sorted(issues)


def build_records(observations, state_entity, as_of):
    """Deterministic IDs use local ISO timestamps, matching the original proxy."""
    histories = {
        name: sorted(rows, key=lambda row: instant(row["t"]))
        for name, rows in observations.items()
    }
    tariffs = {}
    tariff_issues = []
    for direction in ("import", "export"):
        tariffs[direction], issues = select_prices(histories[direction + "_price"])
        tariff_issues.extend(f"{direction}:{item}" for item in issues)
    records = []
    for session in infer_sessions(histories["state"]):
        transaction = str(uuid5(NAMESPACE_URL, state_entity + "|" + session["opened_at"]))
        flags = {"interval_energy_allocation_estimated", "not_a_final_bill", *tariff_issues}
        if session["partial_start"]:
            flags.add("history_starts_mid_session")
        if any(r["value"] not in ACTIVE | PREPARING | TERMINAL for r in session["transitions"]):
            flags.add("unknown_running_state")
        amounts = {}
        for direction in ("import", "export"):
            amounts[direction], issues = price_direction(
                session, histories[direction], tariffs[direction], direction, as_of)
            flags.update(f"{direction}:{issue}" for issue in issues)
        imp, exp = amounts["import"], amounts["export"]
        net = D(imp["amount"]) - D(exp["amount"]) if imp["amount"] is not None and exp["amount"] is not None else None
        if session["partial_start"]:
            net = None
        records.append({
            "session_id": "sigen-proxy-" + transaction,
            "ocpp_transaction_id": transaction, "transaction_id_source": "proxy_generated",
            "native_ocpp_transaction_id": None, "transaction_id_verified_by_charger": False,
            "opened_at": session["opened_at"], "energy_started_at": session["energy_started_at"],
            "ended_at": session["ended_at"], "as_of": as_of,
            "status": "ended_observed" if session["ended_at"] else "active_observed",
            "running_state": session["transitions"][-1]["value"],
            "import_kwh": float(D(imp["wh"]) / 1000) if imp["wh"] is not None else None,
            "export_kwh": float(D(exp["wh"]) / 1000) if exp["wh"] is not None else None,
            "net_cost_aud": float(net.quantize(D(".01"), rounding=ROUND_HALF_UP)) if net is not None else None,
            "net_cost_aud_unrounded": str(net) if net is not None else None,
            "import_cost_aud": imp["amount"], "export_credit_aud": exp["amount"],
            "unpriced_import_wh": imp["unpriced_wh"], "unpriced_export_wh": exp["unpriced_wh"],
            "estimated_rate_import_wh": imp["estimated_rate_wh"],
            "estimated_rate_export_wh": exp["estimated_rate_wh"],
            "quality_flags": sorted(flags), "billing_eligible": False,
            "payment_state": "not_requested",
        })
    return records
