import copy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sqlite3
import json
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from wallet_service.app import create_app

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ledger", ROOT / "custom_components/bsv_settlement/ledger.py")
ledger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ledger)
API = "api-" + "x" * 32
APPROVAL = "approval-" + "y" * 32
HEADERS = {"Authorization": "Bearer " + API}
APPROVER = {"Authorization": "Bearer " + APPROVAL}


def session(imported=3000, exported=2000):
    interval = {"start": "2026-10-02T02:00:00Z", "end": "2026-10-02T02:30:00Z",
                "import_wh": imported, "export_wh": exported,
                "import_price_aud_per_kwh": "0.30", "export_price_aud_per_kwh": "0.60",
                "price_status": "final", "tariff_version": "test-v1", "meter_quality": "validated"}
    return {"session_id": "test:" + str(uuid4()), "started_at": interval["start"],
            "driver_binding_id": "driver-demo-01", "intervals": [interval]}


def payload(imported=3000, exported=2000):
    return ledger.freeze(session(imported, exported), "2026-10-02T02:30:00Z", imported, exported)


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "wallet.sqlite3", API, APPROVAL)
    with TestClient(app, headers=HEADERS) as c:
        yield c


def prepare(client, body=None):
    sid = str(uuid4())
    r = client.put("/v1/settlements/" + sid, json=body or payload())
    assert r.status_code == 201, r.text
    return sid, r.json()


def request(client, sid, record):
    r = client.post(f"/v1/settlements/{sid}/request-payment",
                    json={"quote_id": record["quote"]["quote_id"]})
    assert r.status_code in (200, 202), r.text
    return r.json()


def decision(client, sid, rec, choice="approve", headers=APPROVER):
    return client.post(f"/v1/mock/settlements/{sid}/decision", headers=headers,
                       json={"approval_request_id": rec["approval_request_id"],
                             "quote_id": rec["quote"]["quote_id"], "decision": choice})


@pytest.mark.parametrize("imp,exp,cents,sats,direction", [
    (2000, 0, 60, 6000, "driver_to_operator"),
    (3000, 2000, -30, 3000, "operator_to_driver"),
    (2000, 1000, 0, 0, "none"),
])
def test_directions(client, imp, exp, cents, sats, direction):
    sid, rec = prepare(client, payload(imp, exp))
    assert (rec["net_amount_minor"], rec["amount_sats"], rec["direction"]) == (cents, sats, direction)
    if cents:
        rec = request(client, sid, rec)
        paid = decision(client, sid, rec).json()
        assert paid["state"] == "mock_received"
        assert paid["txid"] is None and paid["receipt_id"].startswith("MOCK-")
    else:
        assert rec["state"] == "no_payment_due"


def test_duplicate_immutable_and_session_unique(client):
    body = payload()
    sid, rec = prepare(client, body)
    assert client.put(f"/v1/settlements/{sid}", json=body).status_code == 200
    assert client.put(f"/v1/settlements/{uuid4()}", json=body).status_code == 409
    changed = {**body, "ledger_sha256": "f" * 64}
    assert client.put(f"/v1/settlements/{sid}", json=changed).status_code == 409


def test_repeat_approval_single_receipt(client):
    sid, rec = prepare(client)
    rec = request(client, sid, rec)
    first = decision(client, sid, rec).json()
    second = decision(client, sid, rec).json()
    assert first["receipt_id"] == second["receipt_id"]
    assert decision(client, sid, rec, "decline").status_code == 409


def test_roles_and_decline(client):
    assert client.get("/v1/health", headers={"Authorization": "Bearer bad"}).status_code == 401
    sid, rec = prepare(client)
    rec = request(client, sid, rec)
    assert decision(client, sid, rec, headers=HEADERS).status_code == 401
    assert decision(client, sid, rec, "decline").json()["state"] == "declined"
    assert client.post(f"/v1/settlements/{sid}/request-payment",
                       json={"quote_id": rec["quote"]["quote_id"]}).status_code == 409


def test_bad_summary_and_binding(client):
    body = payload()
    body["net_amount_minor"] = 999
    assert client.put(f"/v1/settlements/{uuid4()}", json=body).status_code == 422
    body = payload()
    body["driver_binding_id"] = "attacker-wallet"
    assert client.put(f"/v1/settlements/{uuid4()}", json=body).status_code == 422


def test_restart_receipt_persists(tmp_path):
    path = tmp_path / "restart.sqlite3"
    with TestClient(create_app(path, API, APPROVAL), headers=HEADERS) as c:
        sid, rec = prepare(c)
        rec = request(c, sid, rec)
        receipt = decision(c, sid, rec).json()["receipt_id"]
    with TestClient(create_app(path, API, APPROVAL), headers=HEADERS) as c:
        loaded = c.get(f"/v1/settlements/{sid}").json()
        assert loaded["receipt_id"] == receipt
        assert decision(c, sid, rec).json()["receipt_id"] == receipt


def test_concurrent_approval(tmp_path):
    path = tmp_path / "concurrent.sqlite3"
    with TestClient(create_app(path, API, APPROVAL), headers=HEADERS) as c:
        sid, rec = prepare(c)
        rec = request(c, sid, rec)
    def approve(_):
        with TestClient(create_app(path, API, APPROVAL), headers=HEADERS) as c:
            return decision(c, sid, rec).json()["receipt_id"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(approve, range(8)))
    assert len(set(receipts)) == 1


def test_quote_expiry_and_replacement(tmp_path):
    path = tmp_path / "expired.sqlite3"
    with TestClient(create_app(path, API, APPROVAL), headers=HEADERS) as c:
        sid, rec = prepare(c)
        pending = request(c, sid, rec)
        # Controlled clock fixture: expire just this quote in the test database.
        with sqlite3.connect(path) as db:
            stored = json.loads(db.execute("SELECT record FROM settlements WHERE id=?", (sid,)).fetchone()[0])
            stored["quote"]["expires_at"] = "2000-01-01T00:00:00Z"
            db.execute("UPDATE settlements SET record=? WHERE id=?", (json.dumps(stored), sid))
        assert decision(c, sid, pending).status_code == 409
        r = c.post(f"/v1/settlements/{sid}/quotes", json={"replaces_quote_id": rec["quote"]["quote_id"]})
        assert r.status_code == 200
        replacement = r.json()["quote"]["quote_id"]
        assert replacement != rec["quote"]["quote_id"]
        assert decision(c, sid, pending).status_code == 409
        r2 = c.post(f"/v1/settlements/{sid}/quotes", json={"replaces_quote_id": rec["quote"]["quote_id"]})
        assert r2.json()["quote"]["quote_id"] == replacement


def test_confirm_not_blockchain(client):
    sid, rec = prepare(client)
    rec = request(client, sid, rec)
    decision(client, sid, rec)
    r = client.post(f"/v1/mock/settlements/{sid}/confirm", headers=APPROVER).json()
    assert r["state"] == "mock_confirmed"
    assert r["confirmations"] == 0 and r["txid"] is None


def test_oversized_body(client):
    assert client.put(f"/v1/settlements/{uuid4()}", content="x" * 65537).status_code == 413


def test_negative_prices():
    s = session(1000, 0)
    s["intervals"][0]["import_price_aud_per_kwh"] = "-0.20"
    assert ledger.freeze(s, "2026-10-02T02:30:00Z", 1000, 0)["net_amount_minor"] == -20


@pytest.mark.parametrize("fault", ["gap", "overlap", "provisional", "counter", "nan", "timezone"])
def test_bad_ledger(fault):
    s = session()
    final_imp = 3000
    if fault == "gap":
        s["intervals"][0]["start"] = "2026-10-02T02:01:00Z"
    elif fault == "overlap":
        s["intervals"].append(copy.deepcopy(s["intervals"][0]))
    elif fault == "provisional":
        s["intervals"][0]["price_status"] = "provisional"
    elif fault == "counter":
        final_imp = 2999
    elif fault == "nan":
        s["intervals"][0]["import_price_aud_per_kwh"] = "NaN"
    else:
        s["started_at"] = "2026-10-02T02:00:00"
    with pytest.raises(ValueError):
        ledger.freeze(s, "2026-10-02T02:30:00Z", final_imp, 2000)


def test_multiple_tariffs_and_final_rounding():
    s = session(1, 0)
    one = s["intervals"][0]
    one["end"] = "2026-10-02T02:15:00Z"
    one["import_price_aud_per_kwh"] = "2.5"
    two = {**one, "start": one["end"], "end": "2026-10-02T02:30:00Z",
           "import_price_aud_per_kwh": "3.5"}
    s["intervals"].append(two)
    result = ledger.freeze(s, two["end"], 2, 0)
    assert result["net_amount_minor"] == 1
    assert Decimal(result["pricing_summary"]["rounding_adjustment_aud"]) == Decimal("0.004")


def test_weak_tokens_rejected(tmp_path):
    with pytest.raises(RuntimeError):
        create_app(tmp_path / "bad.sqlite3", "short", "short")
