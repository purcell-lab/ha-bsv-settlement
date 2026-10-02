"""Persistent mock settlement API. No keys, signing, broadcasting or real funds."""
from __future__ import annotations

import hmac
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utcnow():
    return datetime.now(timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def fail(status, code, message):
    raise HTTPException(status, {"code": code, "message": message, "retryable": False})


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Summary(Strict):
    import_amount_aud: Decimal
    export_credit_aud: Decimal
    rounding_adjustment_aud: Decimal
    prices_final: Literal[True]

    @field_validator("import_amount_aud", "export_credit_aud", "rounding_adjustment_aud")
    @classmethod
    def finite(cls, v):
        if not v.is_finite() or abs(v) > Decimal("1000000"):
            raise ValueError("Amount must be finite and within demo limits")
        return v


class Prepare(Strict):
    session_id: str = Field(min_length=1, max_length=200)
    ledger_revision: int = Field(default=1, ge=1)
    ledger_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    driver_binding_id: Literal["driver-demo-01"]
    operator_binding_id: Literal["operator-demo-01"]
    currency: Literal["AUD"]
    net_amount_minor: int = Field(strict=True, ge=-100000, le=100000)
    import_wh: int = Field(strict=True, ge=0, le=10000000)
    export_wh: int = Field(strict=True, ge=0, le=10000000)
    pricing_summary: Summary
    ended_at: datetime

    @model_validator(mode="after")
    def amounts(self):
        s = self.pricing_summary
        raw = s.import_amount_aud - s.export_credit_aud
        rounded = raw.quantize(Decimal(".01"), rounding=ROUND_HALF_UP)
        if rounded * 100 != self.net_amount_minor:
            raise ValueError("Net amount does not match rounded summary")
        if rounded - raw != s.rounding_adjustment_aud:
            raise ValueError("Rounding adjustment does not match")
        if self.ended_at.utcoffset() is None:
            raise ValueError("ended_at needs an explicit timezone")
        return self


class RequestPayment(Strict):
    quote_id: str


class Requote(Strict):
    replaces_quote_id: str


class Decision(Strict):
    approval_request_id: str
    quote_id: str
    decision: Literal["approve", "decline"]


def create_app(db_path=None, api_token=None, approval_token=None, quote_seconds=300):
    api_token = api_token or os.environ.get("MOCK_API_TOKEN", "")
    approval_token = approval_token or os.environ.get("MOCK_APPROVAL_TOKEN", "")
    if len(api_token) < 24 or len(approval_token) < 24 or api_token == approval_token:
        raise RuntimeError("Set distinct MOCK_API_TOKEN and MOCK_APPROVAL_TOKEN (24+ characters).")
    db_path = str(db_path or os.environ.get("MOCK_DB", "./data/wallet.sqlite3"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title="BSV session settlement MOCK", version="0.1.0",
                  description="Simulated payments only. No blockchain transactions.")

    @app.exception_handler(HTTPException)
    async def error_handler(request, exc):
        body = exc.detail if isinstance(exc.detail, dict) else {
            "code": "HTTP_ERROR", "message": exc.detail, "retryable": False}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.middleware("http")
    async def body_limit(request, call_next):
        # Read/count streamed bodies as well as Content-Length; not just header trust.
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > 65536:
                return JSONResponse(status_code=413, content={"code": "BODY_TOO_LARGE"})
            chunks.append(chunk)
        request._body = b"".join(chunks)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def authorize(expected, header):
        token = header[7:] if header and header.startswith("Bearer ") else ""
        if not hmac.compare_digest(token, expected):
            fail(401, "UNAUTHORIZED", "A valid bearer token is required")

    def auth(authorization: str | None = Header(default=None)):
        authorize(api_token, authorization)

    def approver(authorization: str | None = Header(default=None)):
        authorize(approval_token, authorization)

    @contextmanager
    def db():
        conn = sqlite3.connect(db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS settlements (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL UNIQUE,
            request TEXT NOT NULL, record TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS quote_replacements (
            settlement_id TEXT, old_quote_id TEXT, quote TEXT,
            PRIMARY KEY(settlement_id, old_quote_id))""")

    def load(c, sid):
        row = c.execute("SELECT * FROM settlements WHERE id=?", (str(sid),)).fetchone()
        if not row:
            fail(404, "NOT_FOUND", "Settlement not found")
        return json.loads(row["record"])

    def save(c, rec):
        rec["updated_at"] = stamp(utcnow())
        c.execute("UPDATE settlements SET record=? WHERE id=?",
                  (json.dumps(rec), rec["settlement_id"]))

    def quote():
        now = utcnow()
        return {"quote_id": str(uuid4()), "sats_per_aud": "10000",
                "source": "synthetic_demo_fixed_rate", "quoted_at": stamp(now),
                "expires_at": stamp(now + timedelta(seconds=quote_seconds)),
                "rounding": "ROUND_HALF_UP"}

    def expired(rec):
        return utcnow() >= datetime.fromisoformat(rec["quote"]["expires_at"].replace("Z", "+00:00"))

    def read(c, sid):
        rec = load(c, sid)
        if rec["state"] in ("prepared", "awaiting_approval") and expired(rec):
            rec["state"] = "expired"
            rec["required_action"] = "replace_quote"
            save(c, rec)
        return rec

    protected = [Depends(auth)]

    @app.get("/v1/health", dependencies=protected)
    def health():
        return {"mode": "mock", "network": "mock", "backend_available": True,
                "features": ["simulated_approval", "persistent_receipts", "quote_replacement"],
                "live_payments_supported": False}

    @app.get("/v1/wallet-bindings/{binding_id}", dependencies=protected)
    def binding(binding_id: str):
        if binding_id not in ("driver-demo-01", "operator-demo-01"):
            fail(404, "UNKNOWN_BINDING", "Only the two synthetic demo bindings exist")
        return {"binding_id": binding_id, "verification_status": "synthetic_mock_only",
                "network": "mock", "can_send": True, "can_receive": True}

    @app.put("/v1/settlements/{sid}", dependencies=protected)
    def prepare(sid: UUID, body: Prepare):
        canonical = json.dumps(body.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        with db() as c:
            row = c.execute("SELECT request FROM settlements WHERE id=?", (str(sid),)).fetchone()
            if row:
                if row["request"] != canonical:
                    fail(409, "IMMUTABLE_CONFLICT", "Settlement contents cannot be changed")
                return read(c, sid)
            if c.execute("SELECT 1 FROM settlements WHERE session_id=?", (body.session_id,)).fetchone():
                fail(409, "SESSION_ALREADY_PREPARED", "This session already has a settlement")
            amount = body.net_amount_minor
            direction = "driver_to_operator" if amount > 0 else (
                "operator_to_driver" if amount < 0 else "none")
            payer = body.driver_binding_id if amount > 0 else body.operator_binding_id
            recipient = body.operator_binding_id if amount > 0 else body.driver_binding_id
            rec = {"settlement_id": str(sid), "session_id": body.session_id,
                   "state": "prepared" if amount else "no_payment_due",
                   "mode": "mock", "network": "mock", "simulated": True,
                   "direction": direction, "payer_binding_id": payer if amount else None,
                   "recipient_binding_id": recipient if amount else None,
                   "currency": "AUD", "net_amount_minor": amount,
                   "amount_sats": abs(amount) * 100, "fee_policy": "payer_additional",
                   "quote": quote(), "txid": None, "network_fee_sats": 0,
                   "recipient_accepted": False, "confirmations": 0, "receipt_id": None,
                   "approval_request_id": None, "approval_url": None,
                   "required_action": "request_payment" if amount else None,
                   "last_error": None, "created_at": stamp(utcnow())}
            c.execute("INSERT INTO settlements VALUES (?,?,?,?)",
                      (str(sid), body.session_id, canonical, json.dumps(rec)))
            return JSONResponse(status_code=201, content=rec)

    @app.get("/v1/settlements/{sid}", dependencies=protected)
    def status(sid: UUID):
        with db() as c:
            return read(c, sid)

    @app.post("/v1/settlements/{sid}/request-payment", dependencies=protected)
    def request_payment(sid: UUID, body: RequestPayment):
        with db() as c:
            rec = read(c, sid)
            if body.quote_id != rec["quote"]["quote_id"]:
                fail(409, "STALE_QUOTE", "The quote has been replaced")
            if rec["state"] == "expired":
                fail(409, "QUOTE_EXPIRED", "Replace the quote before approval")
            if rec["state"] in ("awaiting_approval", "mock_received", "mock_confirmed", "no_payment_due"):
                return rec
            if rec["state"] != "prepared":
                fail(409, "INVALID_STATE", "Cannot request payment in this state")
            rec.update(state="awaiting_approval", required_action="simulated_payer_approval",
                       approval_request_id=str(uuid4()))
            save(c, rec)
            return JSONResponse(status_code=202, content=rec)

    @app.post("/v1/settlements/{sid}/quotes", dependencies=protected)
    def replace_quote(sid: UUID, body: Requote):
        with db() as c:
            rec = read(c, sid)
            prior = c.execute("SELECT quote FROM quote_replacements WHERE settlement_id=? AND old_quote_id=?",
                              (str(sid), body.replaces_quote_id)).fetchone()
            if prior:
                return {"settlement_id": str(sid), "quote": json.loads(prior["quote"]),
                        "state": rec["state"], "mode": "mock"}
            if rec["quote"]["quote_id"] != body.replaces_quote_id:
                fail(409, "STALE_QUOTE", "Quote is not the current quote")
            if rec["state"] != "expired":
                fail(409, "INVALID_STATE", "Only an expired, unsubmitted quote can be replaced")
            rec.update(quote=quote(), state="prepared", approval_request_id=None,
                       required_action="request_payment")
            c.execute("INSERT INTO quote_replacements VALUES (?,?,?)",
                      (str(sid), body.replaces_quote_id, json.dumps(rec["quote"])))
            save(c, rec)
            return rec

    @app.post("/v1/mock/settlements/{sid}/decision", dependencies=[Depends(approver)])
    def decide(sid: UUID, body: Decision):
        with db() as c:
            rec = read(c, sid)
            if (body.quote_id != rec["quote"]["quote_id"]
                    or body.approval_request_id != rec["approval_request_id"]):
                fail(409, "STALE_APPROVAL", "Approval does not match the current request")
            previous = rec.get("mock_decision")
            if previous:
                if previous != body.decision:
                    fail(409, "DECISION_CONFLICT", "This approval has already been decided")
                return rec
            if rec["state"] != "awaiting_approval":
                fail(409, "INVALID_STATE", "No current approval is waiting")
            rec["mock_decision"] = body.decision
            rec["required_action"] = None
            if body.decision == "decline":
                rec["state"] = "declined"
            else:
                rec.update(state="mock_received", recipient_accepted=True,
                           receipt_id="MOCK-" + str(uuid4()), simulated_at=stamp(utcnow()))
            save(c, rec)
            return rec

    @app.post("/v1/mock/settlements/{sid}/confirm", dependencies=[Depends(approver)])
    def confirm(sid: UUID):
        with db() as c:
            rec = load(c, sid)
            if rec["state"] not in ("mock_received", "mock_confirmed"):
                fail(409, "INVALID_STATE", "A simulated received payment is required")
            # Deliberately never fabricate actual chain confirmations or a txid.
            rec.update(state="mock_confirmed", simulated_confirmations=1)
            save(c, rec)
            return rec

    return app
