"""Versioned, append-only tariff provenance for sensor-proxy session accounts.

Evidence only. Nothing here prices energy, changes an account, alters which
sessions settle, or decides finality. The resolved intervals are produced by
the unchanged production selector (``proxy_ledger.select_prices``); this module
records *which published source observation* supplied each resolved price so a
reviewer can reproduce the account after the provider's history has expired.

Each version is immutable once captured. A later change in source evidence
(for example a final rate replacing an estimate) creates a new version linked
to the then-current account digest; it never rewrites an earlier version. A
frozen payment/review account is linked to the version whose
``account_digest`` equals its ``source_hash`` (``digest(account_snapshot)``).
"""
import copy
import hashlib
import json

from .proxy_ledger import instant, intersect, number, select_prices

SCHEMA = "bsv_settlement.tariff_provenance.v1"
PRICE_UNIT = "$/kWh"
DIRECTIONS = ("import", "export")
MAX_INTERVALS = 2000      # per direction per session; beyond this the record is marked incomplete
MAX_VERSIONS = 12         # per session; further changes are flagged, never overwrite
MAX_SESSIONS = 200        # oldest captured sessions are dropped first (documented)
ROW_KEYS = ("t", "value", "unit", "start", "end", "estimate")
NOT_RECORDED = {
    "status": "not_recorded",
    "reason": ("No tariff provenance was captured for this account. It predates "
               "provenance capture, or source history had expired. Nothing is inferred."),
}


def digest(value):
    """Canonical SHA-256 (same canonical JSON form as session_review.digest)."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def source_row(row):
    """The published observation exactly as normalised from HA, plus its digest."""
    kept = {key: copy.deepcopy(row[key]) for key in ROW_KEYS if key in row}
    return kept | {"row_digest": digest(kept)}


def _valid(row):
    """Mirror the selector's acceptance test without changing it."""
    if row.get("unit") != PRICE_UNIT or number(row.get("value")) is None:
        return None
    try:
        start, end, observed = instant(row["start"]), instant(row["end"]), instant(row["t"])
    except (KeyError, TypeError, ValueError):
        return None
    if any(t.tzinfo is None for t in (start, end, observed)) or start >= end:
        return None
    return start, end, observed


def _estimate_label(row):
    value = row.get("estimate")
    return "final" if value is False else "estimate" if value is True else "not_published"


def capture_direction(rows, window_start, window_end, source_entity):
    """Resolved intervals intersecting the session window and their source rows."""
    segments, _ = select_prices(rows)
    parsed = [(row, bounds) for row in rows if (bounds := _valid(row))]
    intervals, covered, truncated = [], 0, False
    for segment in segments:
        if not intersect(window_start, window_end, segment["start"], segment["end"]):
            continue
        if len(intervals) >= MAX_INTERVALS:
            truncated = True
            break
        covering = [(row, b) for row, b in parsed
                    if b[0] <= segment["start"] and b[1] >= segment["end"]]
        entry = {
            "start": segment["start"].isoformat(), "end": segment["end"].isoformat(),
            "unit": PRICE_UNIT, "price_basis": "final" if segment["final"] else "estimate",
            "observations_covering_interval": len(covering),
        }
        if segment["rate"] is None:
            entry |= {"status": "ambiguous", "published_price": None,
                      "candidate_prices": sorted({str(number(r["value"])) for r, _ in covering}),
                      "source": None}
        else:
            matches = [(row, b) for row, b in covering
                       if number(row["value"]) == segment["rate"]
                       and (row.get("estimate") is False) == segment["final"]]
            winner = max(matches, key=lambda m: m[1][2])[0] if matches else None
            entry |= {
                "status": "resolved", "published_price": str(segment["rate"]),
                "negative_price": segment["rate"] < 0, "zero_price": segment["rate"] == 0,
                "estimate_flag": _estimate_label(winner) if winner else None,
                "retrieved_at": winner["t"] if winner else None,
                "source": source_row(winner) if winner else None,
            }
            if winner is None:
                entry["status"] = "source_row_not_identified"
            covered += 1
        intervals.append(entry)
    return {
        "source_entity": source_entity, "unit": PRICE_UNIT, "intervals": intervals,
        "interval_count": len(intervals), "truncated": truncated,
        "estimate_interval_count": sum(i["price_basis"] == "estimate" for i in intervals),
        "ambiguous_interval_count": sum(i["status"] == "ambiguous" for i in intervals),
        "negative_price_interval_count": sum(bool(i.get("negative_price")) for i in intervals),
    }


def capture(observations, records, sources):
    """{session_id: provenance} for closed sessions in ``records``. Pure."""
    histories = {key: sorted(observations.get(key + "_price", []), key=lambda r: instant(r["t"]))
                 for key in DIRECTIONS}
    result = {}
    for record in records:
        if not record or record.get("status") != "ended_observed" or not record.get("ended_at"):
            continue
        start, end = instant(record["opened_at"]), instant(record["ended_at"])
        body = {
            "schema": SCHEMA, "session_id": record["session_id"],
            "window": {"opened_at": record["opened_at"], "ended_at": record["ended_at"]},
            "selection": "proxy_ledger.select_prices; docs/tariff-overlap-reconciliation.md",
            "allocation": "time-proportional within matching running state; docs/golden-accounts.md",
            "rounding": "net AUD quantised to 0.01 with ROUND_HALF_UP; sats to 1 with ROUND_HALF_UP",
            "quality_flags": sorted(record.get("quality_flags") or []),
            "estimated_rate_wh": {d: record.get(f"estimated_rate_{d}_wh") for d in DIRECTIONS},
            "unpriced_wh": {d: record.get(f"unpriced_{d}_wh") for d in DIRECTIONS},
            "directions": {
                d: capture_direction(histories[d], start, end, sources.get(d + "_price"))
                for d in DIRECTIONS},
        }
        result[record["session_id"]] = body
    return result


def summary(version):
    """Compact operator view: never the full source rows."""
    if not version:
        return copy.deepcopy(NOT_RECORDED)
    body = version["provenance"]
    return {
        "status": "recorded", "schema": body["schema"], "version": version["version"],
        "captured_at": version["captured_at"], "provenance_digest": version["provenance_digest"],
        "account_digest": version["account_digest"],
        "directions": {d: {key: body["directions"][d][key] for key in (
            "source_entity", "unit", "interval_count", "estimate_interval_count",
            "ambiguous_interval_count", "negative_price_interval_count", "truncated")}
            for d in DIRECTIONS},
    }


class TariffProvenanceLedger:
    """Append-only per-session versions. Invalid stored data is ignored, not repaired."""

    def __init__(self, saved=None):
        self.invalid_store, self.raw = False, None
        self.data = {"schema": SCHEMA, "sessions": {}}
        if saved is None:
            return
        if (isinstance(saved, dict) and saved.get("schema") == SCHEMA
                and isinstance(saved.get("sessions"), dict)):
            self.data = copy.deepcopy(saved)
        else:
            # Preserve the unrecognised value byte-for-byte and stop capturing.
            self.invalid_store, self.raw = True, copy.deepcopy(saved)

    def stored(self):
        return copy.deepcopy(self.raw if self.invalid_store else self.data)

    def versions(self, session_id):
        return self.data["sessions"].get(session_id, {}).get("versions", [])

    def observe(self, session_id, provenance, account_digest, captured_at):
        """Append a version when evidence or the linked account changed. Returns bool."""
        if self.invalid_store:
            return False
        body_digest = digest(provenance)
        entry = self.data["sessions"].setdefault(
            session_id, {"versions": [], "version_limit_reached": False})
        if any(v["provenance_digest"] == body_digest and v["account_digest"] == account_digest
               for v in entry["versions"]):
            return False
        if len(entry["versions"]) >= MAX_VERSIONS:
            changed = not entry["version_limit_reached"]
            entry["version_limit_reached"] = True
            return changed
        entry["versions"].append({
            "version": len(entry["versions"]) + 1, "captured_at": captured_at,
            "account_digest": account_digest, "provenance_digest": body_digest,
            "provenance": copy.deepcopy(provenance),
        })
        sessions = self.data["sessions"]
        while len(sessions) > MAX_SESSIONS:
            oldest = min(sessions, key=lambda k: sessions[k]["versions"][0]["captured_at"]
                         if sessions[k]["versions"] else "")
            if oldest == session_id:
                break
            sessions.pop(oldest)
        return True

    def lookup(self, session_id, account_digest):
        """The version captured for exactly this frozen account, else None."""
        if account_digest is None:
            return None
        matches = [v for v in self.versions(session_id) if v["account_digest"] == account_digest]
        return copy.deepcopy(matches[-1]) if matches else None


def verify(version):
    """True when a stored version's digest still matches its body."""
    return bool(version) and digest(version["provenance"]) == version["provenance_digest"]
