"""F1: readiness is operational evidence, not merely saved consent."""
import asyncio
from unittest.mock import patch

import pytest

from custom_components.bsv_settlement.monthly_portal import MonthlyPortal, status
from test_monthly_authority import approved, reserved, DRIVER, POLICY, proof, revision, transition

pytestmark = pytest.mark.asyncio


async def project(service, health=None):
    with patch("custom_components.bsv_settlement.monthly_portal.receiving",
               return_value={"registered": True, "routes_new_credits": True}):
        return await status(MonthlyPortal(service, ("station-1",), POLICY.policy_id, health),
                            service.api, DRIVER.public_key().hex())


def runtime(service):
    async def health(identity):
        assert identity == DRIVER.public_key().hex()
        return {"ready": True, "checked_at": service._now().isoformat()}
    return health


async def test_missing_runtime_and_exhausted_native_allowance_not_ready():
    svc, _, _ = await approved()
    original = svc.wallet_grant

    async def zero(terms):
        return (await original(terms)) | {"remaining_sats": 0}
    svc.wallet_grant = zero
    svc.resolve_session = svc.resolve_record = None
    result = await project(svc)
    assert result["readiness"]["automatic_collection"] is False
    assert set(result["readiness"]["missing"]) == {
        "session_ownership_unavailable", "session_accounting_unavailable",
        "collection_service_unavailable", "wallet_allowance_exhausted"}


async def test_positive_health_is_required_and_allowance_exhaustion_is_distinct():
    svc, _, terms = await reserved()
    assert (await project(svc, runtime(svc)))["readiness"]["automatic_collection"]
    # Reserve the full allowance including fees using the public internal API.
    await transition(svc, terms, "release_uninvoked", attempt_id="attempt", reason="fixture")
    await transition(svc, terms, "reserve", attempt_id="full", account_id="proxy|session",
                     debit_sats=100, fee_reserve_sats=29900)
    result = await project(svc, runtime(svc))
    assert result["allowance"]["remaining_sats"] == 0
    assert "allowance_exhausted" in result["readiness"]["missing"]
    assert not result["readiness"]["automatic_collection"]


async def test_cancel_during_grant_read_never_reports_ready():
    svc, _, terms = await approved()
    entered, resume = asyncio.Event(), asyncio.Event()
    original = svc.wallet_grant

    async def delayed(terms):
        entered.set()
        await resume.wait()
        return await original(terms)
    svc.wallet_grant = delayed
    pending = asyncio.create_task(project(svc, runtime(svc)))
    await entered.wait()
    await svc.cancel(terms["authority_id"], proof(terms, cancel=True),
                     expected_revision=revision(svc))
    resume.set()
    result = await pending
    assert not result["readiness"]["automatic_collection"]
    assert "authority_changed" in result["readiness"]["missing"]


async def test_unverified_health_cannot_be_used_as_runtime_evidence():
    svc, _, _ = await approved()
    for result in (True, {"ready": True}, {"ready": False, "checked_at": svc._now().isoformat()},
                   {"ready": True, "checked_at": "2000-01-01T00:00:00+00:00"}):
        async def health(identity):
            return result
        assert "collection_service_unavailable" in (await project(svc, health))["readiness"]["missing"]
