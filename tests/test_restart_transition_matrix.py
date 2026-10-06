"""Restart at every manual, collection and monthly transition (issue #7).

A restart reloads the wallet ledger from its persisted HA store into a fresh
object, as in test_record_versioning. Each cell restarts before and after one
transition, replays that transition, and compares the hash-chained audit with an
uninterrupted run. Fictional keys only; providers fail on any extra broadcast.
"""
import asyncio
from dataclasses import asdict
from datetime import timedelta

import pytest
from bsv import PrivateKey, P2PKH, Transaction, TransactionInput, TransactionOutput

from custom_components.bsv_settlement.api import WalletError
from custom_components.bsv_settlement.audit import project, verify
from custom_components.bsv_settlement.collection import transaction_shape
from custom_components.bsv_settlement.coordinator import SettlementCoordinator
from custom_components.bsv_settlement.monthly_allowance import Month
from custom_components.bsv_settlement.monthly_authority import MonthlyAuthorities, decode
from custom_components.bsv_settlement.session_review import now
from test_budget import OPEN_HASS, consent, no_network  # noqa: F401  (autouse: no client session, closes hass)
from test_collection import CollectionChain, claim_data, payment
from test_mainnet import FictionalChain, approval, setup_wallet
from test_monthly_authority import POLICY, proof as monthly_proof, service as monthly_service
from test_record_versioning import reload as reload_ledger
from test_session_review import prepare_data, review_approval, session, source

pytestmark = pytest.mark.asyncio


async def reload(api):
    restored = await reload_ledger(api)
    restored.ctx = getattr(api, "ctx", None)  # Test-side fixture handles, not ledger state.
    return restored


def guard(chain, allowed):
    """Shared by every reload; a broadcast beyond the expected count fails the cell."""
    original = chain.broadcast

    async def broadcast(raw):
        assert len(chain.posts) < allowed, "unexpected broadcast after restart"
        return await original(raw)
    chain.broadcast = broadcast
    return chain


def history(api):
    """Audit entries with one-way refs renamed by first use, so two runs compare."""
    names = {}
    return [(e["event"], e["ns"], names.setdefault(e["ref"], len(names)), e["from"], e["to"])
            for e in api.store.audit.doc["entries"]]


def verified(api):
    verify(api.store.audit.doc, api.entry.data["operator_public_key"])
    assert api.store.audit.state == "ready"


async def refused(call, match):
    with pytest.raises(WalletError, match=match):
        await call


async def drive(api, steps, cut):
    """Run every step; restart before and after ``cut`` (or all) and replay it."""
    for name, step, replay in steps:
        if cut in (name, "all"):
            seen = history(api)
            api = await reload(api)  # Restart before the transition...
            assert history(api) == seen  # ...rewrites no earlier history.
        await step(api)
        if cut in (name, "all"):
            api = await reload(api)
            seen, records, posts = history(api), project(api.saved), list(api.chain.posts)
            await replay(api)  # Same request again after the restart.
            assert (history(api), project(api.saved), api.chain.posts) == (seen, records, posts), name
            api = await reload(api)
        verified(api)
    return api


# --- Manual session review: driver payment and operator credit ----------------

class ManualChain(FictionalChain):
    """Evidence exists only for posted bytes or the one fictional driver payment."""

    def __init__(self, address):
        super().__init__(address)
        self.driver_tx, self.confirmations = None, 0

    async def request(self, method, path, raw=False):
        assert method == "GET" and self.driver_tx is not None
        return self.driver_tx.hex()

    async def details(self, txid):
        known = {Transaction.from_hex(raw).txid() for raw in self.posts}
        if self.driver_tx:
            known.add(self.driver_tx.txid())
        if txid not in known:
            raise WalletError("Fictional provider has no observation")
        return {"txid": txid, "confirmations": self.confirmations}


async def manual(tmp_path, amount):
    hass, entry, api = await setup_wallet(tmp_path)
    OPEN_HASS.append(hass)
    source(hass, session(amount))
    api.chain = guard(ManualChain(api.identity["address"]), 1)
    if not amount.startswith("-"):
        api.chain.driver_tx = Transaction([TransactionInput(source_txid="22" * 32)], [
            TransactionOutput(P2PKH().lock(api.identity["address"]), satoshis=189)])
    return api


def review(api):
    rows = list(api.saved["session_reviews"].values())
    assert len(rows) == 1 and len(api.saved["session_review_index"]) == 1
    return rows[0]


def receipt_fields(api):
    return {"review_id": review(api)["review_id"], "txid": api.chain.driver_tx.txid(),
            "output_index": 0, "confirm_driver_payment_reference": True}


def credit_fields(api):
    r = review(api)
    draft = api.reviews.public(r)["credit_draft"]
    return approval(draft) | {"review_id": r["review_id"], "terms_hash": r["terms_hash"]}


async def prepare(api):
    await api.reviews.prepare(prepare_data(), "admin")


async def prepare_again(api):
    assert (await api.reviews.prepare(prepare_data(), "admin"))["review_id"] == review(api)["review_id"]


async def approve(api):
    await api.reviews.approve(review_approval(api.reviews.public(review(api))), "admin")


async def report_driver_payment(api):
    api.chain.confirmations = 0
    result = await api.reviews.verify_driver_payment(receipt_fields(api), "admin")
    assert result["state"] == "driver_payment_provider_unconfirmed"


async def confirm_driver_payment(api):
    api.chain.confirmations = 1
    result = await api.reviews.reconcile_driver_payment(review(api)["review_id"])
    assert result["state"] == "driver_payment_provider_confirmed"


async def prepare_credit(api):
    r = review(api)
    await api.reviews.prepare_credit({"review_id": r["review_id"], "terms_hash": r["terms_hash"], "fee_sats": 10})


async def broadcast_credit(api):
    assert (await api.reviews.broadcast_credit(credit_fields(api), "admin"))["state"] == "credit_submitted"


async def broadcast_credit_unknown(api):
    api.chain.fail = True
    await refused(api.reviews.broadcast_credit(credit_fields(api), "admin"), "uncertain")
    api.chain.fail = False
    assert api.reviews.latest()["state"] == "credit_broadcast_unknown"


async def rebroadcast_refused(api):
    """Approval replay of a signed credit returns the record; it never re-sends."""
    state = api.reviews.latest()["state"]
    assert (await api.reviews.broadcast_credit(credit_fields(api), "admin"))["state"] == state


async def confirm_credit(api):
    api.chain.confirmations = 1
    await api.refresh_chain()
    assert api.reviews.latest()["state"] == "credit_provider_confirmed"


DRIVER_PAYMENT = [
    ("prepare", prepare, prepare_again),
    ("approve", approve, approve),
    ("payment_reported", report_driver_payment, report_driver_payment),
    ("confirmed", confirm_driver_payment, confirm_driver_payment),
]
CREDIT = [
    ("prepare", prepare, prepare_again),
    ("approve", approve, approve),
    ("credit_prepared", prepare_credit, prepare_credit),
    ("broadcast", broadcast_credit, rebroadcast_refused),
    ("confirmed", confirm_credit, confirm_credit),
]
CREDIT_UNKNOWN = [*CREDIT[:3], ("broadcast_unknown", broadcast_credit_unknown, rebroadcast_refused),
                  ("confirmed", confirm_credit, confirm_credit)]
MANUAL = {"driver_payment": ("1.89", DRIVER_PAYMENT), "credit": ("-1.89", CREDIT),
          "credit_unknown": ("-1.89", CREDIT_UNKNOWN)}


def manual_cells():
    for path, (_, steps) in MANUAL.items():
        for cut in [name for name, _, _ in steps] + ["all"]:
            yield pytest.param(path, cut, id=f"{path}-{cut}")


@pytest.mark.parametrize("path,cut", list(manual_cells()))
async def test_manual_review_restart_at_every_transition(tmp_path, path, cut):
    amount, steps = MANUAL[path]
    straight = await drive(await manual(tmp_path / "straight", amount), steps, None)
    api = await drive(await manual(tmp_path / "restarted", amount), steps, cut)
    r = review(api)
    assert len(api.saved["payments"]) == (0 if path == "driver_payment" else 1)
    if path == "driver_payment":
        assert r["state"] == "driver_payment_provider_confirmed" and not api.chain.posts
        assert list(api.saved["received_outpoints"].values()) == [r["review_id"]]
    else:
        (credit,) = api.saved["payments"].values()
        assert api.chain.posts == [credit["signed_raw"]]  # One signed tx, sent once.
        assert credit["state"] == "provider_confirmed" and r["credit_draft_id"] == credit["draft_id"]
    assert history(api) == history(straight)


@pytest.mark.parametrize("boundary", ["signed_not_posted", "posted_not_acknowledged"])
async def test_manual_credit_restart_inside_broadcast_never_resends(tmp_path, boundary):
    """The automatic and collection interruption matrices have no manual-credit row."""
    straight = await drive(await manual(tmp_path / "straight", "-1.89"), CREDIT_UNKNOWN, None)
    api = await drive(await manual(tmp_path / "restarted", "-1.89"), CREDIT[:3], None)
    send = api.chain.broadcast

    async def interrupted(raw):
        if boundary == "posted_not_acknowledged":
            await send(raw)
        raise asyncio.CancelledError()
    api.chain.broadcast = interrupted
    with pytest.raises(asyncio.CancelledError):
        await api.reviews.broadcast_credit(credit_fields(api), "admin")
    api.chain.broadcast = send
    (signed,) = api.saved["payments"].values()
    api = await reload(api)
    (saved,) = api.saved["payments"].values()
    assert saved["state"] == "broadcast_unknown"
    assert (saved["txid"], saved["signed_raw"]) == (signed["txid"], signed["signed_raw"])
    for _ in range(2):
        await rebroadcast_refused(api)
        api = await reload(api)
    api.chain.confirmations = 1
    if boundary == "signed_not_posted":
        # No provider evidence: stays unknown for operator reconciliation, never resent.
        await refused(api.refresh_chain(), "no observation")
        assert api.reviews.latest()["state"] == "credit_broadcast_unknown" and not api.chain.posts
        assert history(api) == history(straight)[:len(history(api))]
        # Current behaviour: the missing observation also withholds the balance.
        assert api.saved["chain"]["balance_sats"] is None
        assert api.saved["chain"]["error"] == "chain_check_failed"
    else:
        await confirm_credit(api)
        assert api.chain.posts == [saved["signed_raw"]]
        assert history(api) == history(straight)
    verified(api)


# --- Driver collection --------------------------------------------------------

async def collection(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    OPEN_HASS.append(hass)
    record = session()
    record["ended_at"] = None
    proxy = source(hass, record)
    proxy.sources = {"import_price": "sensor.demo_import_price", "export_price": "sensor.demo_export_price"}
    driver = PrivateKey()
    api.chain = guard(CollectionChain(driver.address()), 1)
    api.ctx = {"proxy": proxy, "driver": driver, "tx": payment(api, driver),
               "data": {"proxy_config_entry_id": "proxy-entry", "session_id": "session-1",
                        "conversion_rate_entity": "sensor.demo_rate", "max_total_sats": 1000,
                        "max_fee_sats": 10, "valid_minutes": 120}}
    return api


def budget(api):
    rows = list(api.saved["session_budgets"].values())
    assert len(rows) == 1
    return rows[0]


def args(api):
    ctx = api.ctx
    if "args" not in ctx:
        ctx["args"] = claim_data(api.collections.get(budget(api)), budget(api), ctx["driver"])
    return ctx["args"]


async def invite(api):
    await api.budgets.execute("create_session_budget", api.ctx["data"], "admin")


async def invite_again(api):
    again = await api.budgets.execute("create_session_budget", api.ctx["data"], "admin")
    assert again["invitation_reused"] and again["terms"]["budget_id"] == budget(api)["terms"]["budget_id"]


async def accept(api):
    ctx = api.ctx
    ctx.setdefault("receipt", consent(budget(api), ctx["driver"]))
    await api.budgets.execute("accept_session_budget", {
        "budget_id": budget(api)["terms"]["budget_id"], "receipt": ctx["receipt"]}, "admin")


async def quote(api):
    api.ctx["proxy"].data["latest_session"].update(ended_at=now().isoformat(), net_cost_aud_unrounded="1.89")
    assert (await api.collections.status(budget(api)))["state"] == "ready"


async def quote_again(api):
    old = api.collections.get(budget(api))["quote"]
    assert (await api.collections.status(budget(api)))["quote"] == old


async def claim(api):
    await api.collections.claim(budget(api), args(api))


async def authorise(api):
    await api.collections.authorise(budget(api), args(api) | {"draft": transaction_shape(api.ctx["tx"])})


async def report(api):
    result = await api.collections.report(budget(api), args(api) | {"raw_tx": api.ctx["tx"].hex()})
    assert result["state"] == "provider_confirmed"


async def report_unknown(api):
    api.chain.fail = True
    result = await api.collections.report(budget(api), args(api) | {"raw_tx": api.ctx["tx"].hex()})
    assert result["state"] == "broadcast_unknown"


async def report_again(api):
    """A lost response re-reports the same bytes: reconcile only, never re-send."""
    state = api.collections.get(budget(api))["state"]
    result = await api.collections.report(budget(api), args(api) | {"raw_tx": api.ctx["tx"].hex()})
    assert result["state"] == state


async def confirm(api):
    api.chain.fail = False
    assert (await api.collections.reconcile(budget(api)))["state"] == "provider_confirmed"


COLLECTION = [
    ("invitation", invite, invite_again),
    ("consent", accept, accept),
    ("collection_created", quote, quote_again),
    ("claimed", claim, lambda api: refused(claim(api), "already has an attempt")),
    ("permit", authorise, lambda api: refused(authorise(api), "permit was already issued")),
    ("signed_reported", report, report_again),
    ("confirmed", confirm, confirm),
]
COLLECTION_UNKNOWN = [*COLLECTION[:5], ("broadcast_unknown", report_unknown, report_again),
                      ("confirmed", confirm, confirm)]
COLLECTIONS = {"collection": COLLECTION, "collection_unknown": COLLECTION_UNKNOWN}


def collection_cells():
    for path, steps in COLLECTIONS.items():
        for cut in [name for name, _, _ in steps] + ["all"]:
            yield pytest.param(path, cut, id=f"{path}-{cut}")


@pytest.mark.parametrize("path,cut", list(collection_cells()))
async def test_driver_collection_restart_at_every_transition(tmp_path, path, cut):
    steps = COLLECTIONS[path]
    straight = await drive(await collection(tmp_path / "straight"), steps, None)
    api = await collection(tmp_path / "restarted")
    api = await drive(api, steps, cut)
    row = budget(api)
    item = api.collections.get(row)
    assert len(api.saved["driver_collections"]) == len(api.saved["driver_collection_index"]) == 1
    assert item["state"] == "provider_confirmed" and item["txid"] == api.ctx["tx"].txid()
    assert api.chain.posts == [item["signed_raw"]] == [api.ctx["tx"].hex()]
    assert list(api.saved["received_outpoints"].values()) == ["collection:" + row["terms"]["budget_id"]]
    assert history(api) == history(straight)


# --- Monthly authority (runtime disabled; reachable service transitions) -------

ACCOUNT = "proxy|session"


async def monthly(tmp_path):
    hass, entry, api = await setup_wallet(tmp_path)
    OPEN_HASS.append(hass)
    api.chain = guard(api.chain, 0)  # Monthly accounting never signs or broadcasts.
    template, clock = monthly_service()
    api.ctx = {"template": template, "clock": clock, "driver": PrivateKey(17)}
    return api


def authority(api):
    """One service per loaded ledger, rebuilt from storage after each restart."""
    ctx = api.ctx
    if ctx.get("api") is not api:
        t = ctx["template"]
        ctx["api"], ctx["svc"] = api, MonthlyAuthorities(
            SettlementCoordinator(api.hass, api.entry, api), origin=t.origin, policies=t.policies,
            resolve_session=t.resolve_session, resolve_record=t.resolve_record,
            wallet_grant=t.wallet_grant, clock=t.clock)
    return ctx["svc"]


def attempts(api):
    rows = authority(api).snapshot()["authorities"]
    assert len(rows) <= 1
    return [asdict(a) for row in rows.values() for a in decode(row["ledger"]).attempts]


def aid(api):
    (value,) = authority(api).snapshot()["authorities"] or authority(api).snapshot()["challenges"]
    return value


def revision(api):
    return authority(api).snapshot()["revision"]


def month(api):
    return asdict(Month.at(api.ctx["clock"][0], wallet_timezone="UTC"))


TRANSITIONS = {
    "reserve": lambda api: dict(attempt_id="a1", account_id=ACCOUNT, debit_sats=100, fee_reserve_sats=10),
    "wallet_pending": lambda api: dict(attempt_id="a1", wallet_action_id="op-1"),
    "uncertain": lambda api: dict(attempt_id="a1"),
    "commit": lambda api: dict(attempt_id="a1", actual_spent_sats=105, evidence_ref="fixture-spend",
                               booking_month=month(api)),
}


def call(name, stale=None):
    async def step(api):
        svc, ctx, key = authority(api), api.ctx, api.ctx["driver"]
        rev = revision(api) if stale is None else stale
        if stale is None:
            ctx["rev"] = rev  # What the caller saw; a replayed request reuses it.
        if name == "issue":
            await svc.issue(driver_identity=key.public_key().hex(), station_ids=["station-1"],
                            policy_id=POLICY.policy_id, expected_revision=rev)
        elif name == "accept":
            terms = svc.snapshot()["challenges"][aid(api)]["terms"]
            await svc.accept(aid(api), monthly_proof(terms, key), expected_revision=rev)
        elif name == "bind":
            await svc.bind(aid(api), ACCOUNT, expected_revision=rev)
            if stale is None:
                ctx["clock"][0] += timedelta(seconds=1)  # The bound session has closed.
        elif name == "cancel":
            terms = svc.snapshot()["authorities"][aid(api)]["terms"]
            await svc.cancel(aid(api), monthly_proof(terms, key, cancel=True), expected_revision=rev)
        else:
            await svc.transition(aid(api), name, TRANSITIONS[name](api), expected_revision=rev)
    return step


def replay(name):
    """Same request after restart: stale revisions conflict; idempotent ops return as-is."""
    async def again(api):
        before = attempts(api)
        if name in ("accept", "bind", "cancel"):
            await call(name, api.ctx["rev"])(api)  # Returns the stored record unchanged.
        else:
            await refused(call(name, api.ctx["rev"])(api), "revision conflict")
        if name in TRANSITIONS:  # At the current revision: same attempt, no write (#104).
            rev = revision(api)
            await call(name, rev)(api)
            assert revision(api) == rev
        assert attempts(api) == before
    return again


MONTHLY_STEPS = [(name, call(name), replay(name)) for name in (
    "issue", "accept", "bind", "reserve", "wallet_pending", "uncertain", "commit", "cancel")]


@pytest.mark.parametrize("cut", [name for name, _, _ in MONTHLY_STEPS] + ["all"])
async def test_monthly_authority_restart_at_every_reachable_transition(tmp_path, cut):
    straight = await drive(await monthly(tmp_path / "straight"), MONTHLY_STEPS, None)
    api = await drive(await monthly(tmp_path / "restarted"), MONTHLY_STEPS, cut)
    state = authority(api).snapshot()
    assert len(state["challenges"]) == len(state["authorities"]) == len(state["bindings"]) == 1
    (row,) = attempts(api)
    assert (row["state"], row["spent_sats"], row["wallet_action_id"]) == ("committed", 105, "op-1")
    assert decode(state["authorities"][aid(api)]["ledger"]).cancelled and not api.chain.posts
    same = lambda svc: {k: v for k, v in svc.snapshot()["bindings"][ACCOUNT].items() if k != "authority_id"}
    assert same(authority(api)) == same(authority(straight))
    assert history(api) == history(straight)
