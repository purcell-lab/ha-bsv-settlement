"""F2/F5: real owner-filtered HTTP history for monthly-only session ownership."""
import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from bsv import PrivateKey

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.portal import history
from test_monthly_portal import setup, authorise
from test_monthly_authority import revision
from test_portal import login, post
from test_session_review import session
from test_budget import no_network  # noqa: F401

pytestmark = pytest.mark.asyncio


async def fixture(tmp_path):
    hass, api, driver, view, client, cookie, svc = await setup(tmp_path)
    challenge = await authorise(client, view, cookie, driver)
    clock = [datetime.now(timezone.utc) + timedelta(seconds=1)]
    svc.clock = lambda: clock[0]
    record = session("1.00", "monthly-session") | {
        "opened_at": clock[0].isoformat(), "ended_at": None, "as_of": clock[0].isoformat(),
        "ocpp_transaction_id": "monthly-tx", "running_state": "Discharging"}
    proxy = SimpleNamespace(data={"latest_session": record}, archive=[])
    hass.data["bsv_settlement"]["monthly-proxy"] = proxy

    async def resolve(account, identity):
        return dict(account_key=account, driver_identity=identity, station_id="station-1",
                    transaction_id="monthly-tx", opened_at=record["opened_at"], ended_at=None,
                    satoshis_per_aud="100", ownership_evidence="fictional-presence")
    svc.resolve_session = resolve
    await svc.bind(challenge["authority_id"], "monthly-proxy|monthly-session",
                   expected_revision=revision(svc))
    return hass, api, driver, view, client, cookie, svc, clock, record, challenge


async def test_monthly_open_and_closed_accounts_are_visible_only_to_owner(tmp_path):
    hass, api, driver, view, client, cookie, svc, clock, record, challenge = await fixture(tmp_path)
    try:
        before = copy.deepcopy(api.saved)
        rows = (await (await post(client, view, "sessions", cookie)).json())["sessions"]
        row = next(r for r in rows if r["session_id"] == "monthly-session")
        assert row["authority_kind"] == "monthly" and row["transaction_id"] == "monthly-tx"
        assert row["satoshis_per_aud"] == "100" and row["ended_at"] is None
        assert api.saved == before
        other_cookie, _, _ = await login(client, view, PrivateKey(333))
        assert (await (await post(client, view, "sessions", other_cookie)).json())["sessions"] == []
        clock[0] += timedelta(seconds=2)
        record.update(ended_at=clock[0].isoformat(), status="ended_observed")

        async def resolve_record(binding):
            return record
        svc.resolve_record = resolve_record
        await svc.transition(challenge["authority_id"], "reserve", {
            "attempt_id": "monthly-attempt", "account_id": "monthly-proxy|monthly-session",
            "debit_sats": 100, "fee_reserve_sats": 10}, expected_revision=revision(svc))
        row = next(r for r in history(api, driver.public_key().hex()) if r["session_id"] == "monthly-session")
        assert row["ended_at"] == record["ended_at"] and row["net_amount_aud"] == "1.00"
        assert row["transactions"][0]["state"] == "monthly_reserved"
        assert row["transactions"][0]["txid"] is None
        for forbidden in ("signature", "ownership_evidence", "evidence_ref", "raw_tx"):
            assert forbidden not in str(row)
    finally:
        await client.close()


async def test_portal_uses_guarded_ocpp_observer_not_legacy_state(tmp_path, monkeypatch):
    hass, api, driver, view, client, cookie, svc, clock, record, _ = await fixture(tmp_path)
    try:
        binding = {"status": {"entity_id": "sensor.review_ocpp"}}
        observer = SimpleNamespace(mode="ocpp_import_shadow", binding_error=False,
            sources={"status": "sensor.review_ocpp"},
            entry=SimpleNamespace(data={"source_binding": binding}),
            data={"updated_at": datetime.now(timezone.utc).isoformat(),
                  "recorder": {"legacy_entry_id": "monthly-proxy"}})
        hass.data["bsv_settlement"]["observer-review"] = observer
        hass.states.async_set("sensor.review_ocpp", "Charging")
        monkeypatch.setattr("custom_components.bsv_settlement.ocpp_shadow.source_binding", lambda *_: binding)
        row = next(r for r in history(api, driver.public_key().hex()) if r["session_id"] == "monthly-session")
        assert row["running_state"] == "Discharging" and row["ocpp"]["status"] == "Charging"
        observer.data["updated_at"] = (datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()
        assert not next(r for r in history(api, driver.public_key().hex())
                        if r["session_id"] == "monthly-session")["ocpp"]["available"]
        # Retained ownership remains available while runtime monthly activation is off.
        hass.data.pop("bsv_settlement_monthly_portal")
        assert any(r["session_id"] == "monthly-session" for r in history(api, driver.public_key().hex()))
        saved = api.saved["monthly_authorities"]
        aid = next(iter(saved["authorities"]))
        saved["authorities"][aid]["proof"]["signature"] = "00"
        with pytest.raises(WalletError):
            history(api, driver.public_key().hex())
    finally:
        await client.close()
