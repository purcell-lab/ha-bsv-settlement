"""CLI for mock service replay and explicit simulated approval.

Run from repository root: python scripts/mock_cli.py demo
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ledger", ROOT / "custom_components/bsv_settlement/ledger.py")
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)


def call(method, path, payload=None, approval=False):
    key = "MOCK_APPROVAL_TOKEN" if approval else "MOCK_API_TOKEN"
    token = os.environ.get(key)
    if not token:
        raise RuntimeError(f"Set {key} first")
    url = os.environ.get("MOCK_URL", "http://127.0.0.1:8091").rstrip("/")
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url + path, data=body, method=method,
                                     headers={"Authorization": "Bearer " + token,
                                              "Content-Type": "application/json"})
    # Do not follow redirects with a bearer credential.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=20) as response:
        return json.load(response)


def decide(sid, decision):
    rec = call("GET", f"/v1/settlements/{sid}")
    return call("POST", f"/v1/mock/settlements/{sid}/decision",
                {"approval_request_id": rec["approval_request_id"],
                 "quote_id": rec["quote"]["quote_id"], "decision": decision}, approval=True)


def demo():
    health = call("GET", "/v1/health")
    if health.get("mode") != "mock":
        raise RuntimeError("Refusing non-mock endpoint")
    for name, imported, exported in [("debit", 2000, 0), ("credit", 3000, 2000), ("zero", 2000, 1000)]:
        sid = str(uuid4())
        interval = {"start": "2026-10-02T02:00:00Z", "end": "2026-10-02T02:30:00Z",
                    "import_wh": imported, "export_wh": exported,
                    "import_price_aud_per_kwh": "0.30", "export_price_aud_per_kwh": "0.60",
                    "price_status": "final", "meter_quality": "validated", "tariff_version": "demo-v1"}
        session = {"session_id": "cli:" + name + ":" + sid, "started_at": interval["start"],
                   "driver_binding_id": "driver-demo-01", "intervals": [interval]}
        payload = ledger.freeze(session, interval["end"], imported, exported)
        rec = call("PUT", f"/v1/settlements/{sid}", payload)
        if rec["state"] != "no_payment_due":
            call("POST", f"/v1/settlements/{sid}/request-payment", {"quote_id": rec["quote"]["quote_id"]})
            rec = decide(sid, "approve")
        print(json.dumps({"case": name, "settlement_id": sid, "state": rec["state"],
                          "net_aud": str(payload["net_amount_minor"] / 100),
                          "amount_sats": rec["amount_sats"], "direction": rec["direction"],
                          "receipt_id": rec["receipt_id"], "txid": rec["txid"]}))


def main():
    parser = argparse.ArgumentParser(description="Simulated payments only. No real BSV.")
    parser.add_argument("command", choices=["demo", "status", "approve", "decline", "confirm"])
    parser.add_argument("settlement_id", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "demo":
            demo()
            return
        if not args.settlement_id:
            parser.error("settlement_id is required")
        if args.command in ("approve", "decline"):
            result = decide(args.settlement_id, args.command)
        elif args.command == "confirm":
            result = call("POST", f"/v1/mock/settlements/{args.settlement_id}/confirm", approval=True)
        else:
            result = call("GET", f"/v1/settlements/{args.settlement_id}")
        print(json.dumps(result, indent=2))
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode()}", file=sys.stderr)
        sys.exit(1)
    except (RuntimeError, urllib.error.URLError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
