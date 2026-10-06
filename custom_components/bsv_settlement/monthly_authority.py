"""Staged monthly consent and durable accounting service, not registered at runtime.

The future transport must authenticate callers and supply trusted wallet/session
adapters. Every operation shares SettlementCoordinator.lock and the checkpointed
operator store. No signing, transaction creation, broadcasting or retry occurs.
"""
import asyncio
import copy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP
import secrets
from uuid import uuid4

from .api import WalletError
from .monthly_allowance import Allowance, Attempt, Month, AllowanceError
from .monthly_consent import (
    VERSION, SCOPE, WalletPeriodPolicy, approval_payload, verify_proof,
    validate_terms, exact, public_key, text, timestamp, conversion_rate,
)
from .monthly_ownership import KEY, SCHEMA, ensure_no_legacy_owner
from .session_review import account_snapshot, decimal, digest


def encode(ledger):
    return asdict(ledger)


def _plain(value):
    """JSON shape (tuples as lists) so in-memory and reloaded state compare equal."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def decode(data):
    """Strict schema: corrupt state must never become a fresh allowance."""
    try:
        exact(data, ("authority_id", "wallet_timezone", "limit_sats", "cancelled", "attempts"))
        if not isinstance(data["attempts"], (list, tuple)):
            raise ValueError()
        attempts = []
        for item in data["attempts"]:
            exact(item, ("attempt_id", "account_id", "month", "reserved_sats", "state",
                         "spent_sats", "wallet_action_id", "evidence_ref"))
            exact(item["month"], ("year", "month", "timezone"))
            attempts.append(Attempt(**(item | {"month": Month(**item["month"])})))
        return Allowance(**(data | {"attempts": tuple(attempts)}))
    except (KeyError, TypeError, ValueError):
        raise WalletError("Invalid monthly allowance ledger; reconcile storage") from None


class MonthlyAuthorities:
    """Internal API. Not constructed by integration setup or exposed as a service.

    resolve_session(account_key, driver_identity) must obtain an owner-verified
    snapshot from trusted local evidence. wallet_grant(terms) must return a fresh
    adapter-verified grant, not data supplied by the requesting browser.
    """

    def __init__(self, coordinator, *, origin, policies, resolve_session=None, resolve_record=None,
                 wallet_grant=None, clock=None):
        self.coordinator = coordinator
        self.api = coordinator.api
        self.origin = origin
        self.policies = dict(policies)
        self.resolve_session = resolve_session
        self.resolve_record = resolve_record
        self.wallet_grant = wallet_grant
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.blocked = False
        self._state()  # Validate restored state before any operation.

    def _now(self):
        value = self.clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise WalletError("Invalid monthly service clock")
        return value

    def _context(self, terms):
        validate_terms(terms)
        if (terms["origin"] != self.origin
                or terms["operator_identity"] != self.api.identity["public_key"]
                or terms["operator_address"] != self.api.identity["address"]):
            raise WalletError("Monthly authority origin or operator changed")

    def _policy(self, terms):
        policy = self.policies.get(terms["period_policy"]["policy_id"])
        if not isinstance(policy, WalletPeriodPolicy) or policy.terms() != terms["period_policy"]:
            raise WalletError("Reviewed wallet period policy is unavailable")
        return policy

    def _state(self):
        if self.blocked or getattr(self.api, "_monthly_write_uncertain", False):
            raise WalletError("Monthly storage outcome uncertain; reload and reconcile")
        if KEY not in self.api.saved:
            return {"schema": SCHEMA, "revision": 0, "challenges": {},
                    "authorities": {}, "bindings": {}}
        state = copy.deepcopy(self.api.saved[KEY])
        try:
            exact(state, ("schema", "revision", "challenges", "authorities", "bindings"))
            if (state["schema"] != SCHEMA or type(state["revision"]) is not int
                    or state["revision"] < 1
                    or any(not isinstance(state[k], dict)
                           for k in ("challenges", "authorities", "bindings"))):
                raise ValueError()
            for aid, challenge in state["challenges"].items():
                exact(challenge, ("terms", "used"))
                self._context(challenge["terms"])
                if aid != challenge["terms"]["authority_id"] or type(challenge["used"]) is not bool:
                    raise ValueError()
            for aid, row in state["authorities"].items():
                exact(row, ("terms", "proof", "accepted_at", "ledger", "cancel_proof"))
                self._context(row["terms"])
                verify_proof(row["terms"], row["proof"])
                challenge = state["challenges"][aid]
                if not challenge["used"] or row["terms"] != challenge["terms"]:
                    raise ValueError()
                accepted = timestamp(row["accepted_at"])
                if not timestamp(row["terms"]["issued_at"]) <= accepted < timestamp(row["terms"]["accept_before"]):
                    raise ValueError()
                ledger = decode(row["ledger"])
                if (ledger.authority_id != aid
                        or ledger.wallet_timezone != row["terms"]["period_policy"]["timezone"]
                        or ledger.limit_sats != row["terms"]["monthly_limit_sats"]):
                    raise ValueError()
                if ledger.cancelled:
                    verify_proof(row["terms"], row["cancel_proof"], cancellation=True)
                elif row["cancel_proof"] is not None:
                    raise ValueError()
            if any(c["used"] and aid not in state["authorities"]
                   for aid, c in state["challenges"].items()):
                raise ValueError()
            identities = [r["terms"]["driver_identity"] for r in state["authorities"].values()]
            if len(identities) != len(set(identities)):
                raise ValueError()
            for key, binding in state["bindings"].items():
                self._validate_binding(binding)
                row = state["authorities"][binding["authority_id"]]
                if (binding["account_key"] != key
                        or binding["driver_identity"] != row["terms"]["driver_identity"]
                        or binding["station_id"] not in row["terms"]["station_ids"]):
                    raise ValueError()
            for aid, row in state["authorities"].items():
                attempts = decode(row["ledger"]).attempts
                actions = [a.wallet_action_id for a in attempts if a.wallet_action_id is not None]
                commits = [a.evidence_ref for a in attempts if a.state == "committed"]
                if len(actions) != len(set(actions)) or len(commits) != len(set(commits)):
                    raise ValueError()
                for attempt in attempts:
                    binding = state["bindings"][attempt.account_id]
                    if binding["authority_id"] != aid or binding["final_account"] is None:
                        raise ValueError()
                    debit = binding["final_account"]["debit_sats"]
                    if attempt.reserved_sats < debit or (
                            attempt.state == "committed" and attempt.spent_sats < debit):
                        raise ValueError()
        except (KeyError, TypeError, ValueError, WalletError):
            raise WalletError("Invalid monthly authority store; reconcile storage") from None
        return state

    def _check_revision(self, expected_revision):
        if type(expected_revision) is not int or expected_revision != self._state()["revision"]:
            raise WalletError("Monthly ledger revision conflict; reload without retrying payment")

    async def _persist(self, state, expected_revision):
        self._check_revision(expected_revision)
        state["revision"] = expected_revision + 1
        snapshot = copy.deepcopy(self.api.saved)
        snapshot[KEY] = copy.deepcopy(state)
        try:
            await self.api.store.async_save(snapshot)
        except (Exception, asyncio.CancelledError):
            self.blocked = True
            self.api._monthly_write_uncertain = True
            raise
        # Publish in-memory state only after durable save succeeds.
        self.api.saved = snapshot
        return state["revision"]

    def snapshot(self):
        """Internal operator-only snapshot; must not be exposed as a public API."""
        return self._state()

    async def issue(self, *, driver_identity, station_ids, policy_id,
                    expected_revision, included_session=None):
        async with self.coordinator.lock:
            state = self._state()
            public_key(driver_identity)
            if any(r["terms"]["driver_identity"] == driver_identity
                   for r in state["authorities"].values()):
                raise WalletError("Existing monthly authority must be amended without resetting its ledger")
            policy = self.policies.get(policy_id)
            if not isinstance(policy, WalletPeriodPolicy):
                raise WalletError("Reviewed wallet period policy is required")
            now = self._now()
            terms = {
                "version": VERSION, "scope": SCOPE, "network": "BSV mainnet", "authority_id": str(uuid4()),
                "nonce": secrets.token_hex(32), "driver_identity": driver_identity,
                "operator_identity": self.api.identity["public_key"],
                "operator_address": self.api.identity["address"], "origin": self.origin,
                "station_ids": copy.deepcopy(station_ids), "monthly_limit_sats": 30_000,
                "period_policy": policy.terms(), "issued_at": now.isoformat(),
                "accept_before": (now + timedelta(minutes=10)).isoformat(),
                "effective_at": now.isoformat(), "recurs_until_cancelled": True,
                "collection_policy": "one_final_net_payment_per_session_on_closure",
                "credits_refill": False, "unused_carries_forward": False,
                "conversion_policy": "freeze_configured_sat_per_aud_at_session_binding",
                "included_session": copy.deepcopy(included_session),
            }
            validate_terms(terms)
            state["challenges"][terms["authority_id"]] = {"terms": terms, "used": False}
            revision = await self._persist(state, expected_revision)
            return {"terms": copy.deepcopy(terms), "payload": approval_payload(terms), "revision": revision}

    async def accept(self, authority_id, proof, *, expected_revision):
        async with self.coordinator.lock:
            state = self._state()
            challenge = state["challenges"].get(authority_id)
            if challenge is None:
                raise WalletError("Unknown monthly challenge")
            terms = challenge["terms"]
            verify_proof(terms, proof)
            if authority_id in state["authorities"]:
                if state["authorities"][authority_id]["proof"] != proof:
                    raise WalletError("Monthly consent proof cannot be replaced")
                return copy.deepcopy(state["authorities"][authority_id])
            self._policy(terms)
            now = self._now()
            if not timestamp(terms["issued_at"]) <= now < timestamp(terms["accept_before"]):
                raise WalletError("Monthly consent challenge expired or is not yet valid")
            if any(r["terms"]["driver_identity"] == terms["driver_identity"]
                   for r in state["authorities"].values()):
                raise WalletError("Another monthly authority owns this driver's ledger")
            challenge["used"] = True
            row = {"terms": copy.deepcopy(terms), "proof": copy.deepcopy(proof),
                   "accepted_at": now.isoformat(), "cancel_proof": None,
                   "ledger": encode(Allowance(authority_id, terms["period_policy"]["timezone"]))}
            state["authorities"][authority_id] = row
            await self._persist(state, expected_revision)
            return copy.deepcopy(row)

    @staticmethod
    def _validate_binding(binding):
        exact(binding, ("authority_id", "account_key", "driver_identity", "station_id",
                        "transaction_id", "opened_at", "bound_at", "satoshis_per_aud",
                        "ownership_evidence", "final_account"))
        for key in ("authority_id", "account_key", "station_id", "transaction_id", "ownership_evidence"):
            text(binding[key])
        public_key(binding["driver_identity"])
        if timestamp(binding["opened_at"]) > timestamp(binding["bound_at"]):
            raise WalletError("Session binding precedes opening")
        conversion_rate(binding["satoshis_per_aud"])
        if len(binding["account_key"].split("|")) != 2 or not all(binding["account_key"].split("|")):
            raise WalletError("Invalid recorder/session account key")
        final = binding["final_account"]
        if final is not None:
            exact(final, ("account", "source_hash", "debit_sats"))
            account = final["account"]
            if (not isinstance(account, dict) or final["source_hash"] != digest(account)
                    or account.get("session_id") != binding["account_key"].split("|")[1]
                    or account.get("ocpp_transaction_id") != binding["transaction_id"]
                    or account.get("opened_at") != binding["opened_at"]
                    or not timestamp(account["opened_at"]) < timestamp(account["ended_at"])
                    or type(final["debit_sats"]) is not int or final["debit_sats"] <= 0
                    or final["debit_sats"] != int((decimal(account["net_amount_aud"]) *
                       decimal(binding["satoshis_per_aud"])).quantize(decimal("1"), rounding=ROUND_HALF_UP))):
                raise WalletError("Invalid frozen per-session account")

    async def bind(self, authority_id, account_key, *, expected_revision):
        async with self.coordinator.lock:
            state = self._state()
            row = state["authorities"][authority_id]
            ledger = decode(row["ledger"])
            if ledger.cancelled:
                raise WalletError("Monthly authority cancelled")
            self._policy(row["terms"])
            old = state["bindings"].get(account_key)
            if old:
                if old["authority_id"] != authority_id:
                    raise WalletError("Another monthly authority owns this account")
                return copy.deepcopy(old)
            ensure_no_legacy_owner(self.api, account_key)
            if self.resolve_session is None:
                raise WalletError("Verified session ownership adapter is unavailable")
            observed = await self.resolve_session(account_key, row["terms"]["driver_identity"])
            exact(observed, ("account_key", "driver_identity", "station_id", "transaction_id",
                             "opened_at", "satoshis_per_aud", "ownership_evidence", "ended_at"))
            if (observed["account_key"] != account_key
                    or observed["driver_identity"] != row["terms"]["driver_identity"]
                    or observed["station_id"] not in row["terms"]["station_ids"]):
                raise WalletError("Session identity or station scope mismatch")
            opened = timestamp(observed["opened_at"])
            if observed["ended_at"] is not None and not opened <= timestamp(observed["ended_at"]) <= self._now():
                raise WalletError("Invalid retained session end")
            if opened < timestamp(row["accepted_at"]):
                included = row["terms"]["included_session"]
                if (observed["ended_at"] is not None or included is None
                        or any(observed[k] != included[k] for k in included)):
                    raise WalletError("Historical account was not explicitly included as the current session")
            binding = {k: copy.deepcopy(v) for k, v in observed.items() if k != "ended_at"}
            binding.update(authority_id=authority_id, bound_at=self._now().isoformat(), final_account=None)
            self._validate_binding(binding)
            ensure_no_legacy_owner(self.api, account_key)
            state["bindings"][account_key] = binding
            await self._persist(state, expected_revision)
            return copy.deepcopy(binding)

    async def _account(self, binding):
        if self.resolve_record is None:
            raise WalletError("Closed session accounting adapter is unavailable")
        record = await self.resolve_record(copy.deepcopy(binding))
        account = account_snapshot(record)
        if (account["session_id"] != binding["account_key"].split("|")[1]
                or account["ocpp_transaction_id"] != binding["transaction_id"]
                or account["opened_at"] != binding["opened_at"]
                or not timestamp(account["opened_at"]) < timestamp(account["ended_at"]) <= self._now()):
            raise WalletError("Closed session no longer matches its immutable binding")
        amount = int((decimal(account["net_amount_aud"]) * decimal(binding["satoshis_per_aud"])).quantize(
            decimal("1"), rounding=ROUND_HALF_UP))
        if amount <= 0:
            raise WalletError("This session needs the separate credit or zero-balance route")
        final = {"account": account, "source_hash": digest(account), "debit_sats": amount}
        if binding["final_account"] is not None and binding["final_account"] != final:
            raise WalletError("Frozen per-session account changed; reconcile without recollecting")
        return final

    async def _grant(self, terms):
        self._policy(terms)
        if self.wallet_grant is None:
            raise WalletError("Verified native monthly wallet permission is unavailable")
        grant = await self.wallet_grant(copy.deepcopy(terms))
        now = self._now()  # Adapter awaits may cross a month boundary.
        exact(grant, ("driver_identity", "origin", "policy_id", "monthly_limit_sats",
                      "network", "month", "remaining_sats", "observed_at", "evidence_ref"))
        if (any(grant[k] != terms[k] for k in ("driver_identity", "origin", "monthly_limit_sats", "network"))
                or grant["policy_id"] != terms["period_policy"]["policy_id"]
                or type(grant["monthly_limit_sats"]) is not int
                or type(grant["remaining_sats"]) is not int
                or not 0 <= grant["remaining_sats"] <= terms["monthly_limit_sats"]
                or not 0 <= (now - timestamp(grant["observed_at"])).total_seconds() <= 30):
            raise WalletError("Native wallet permission is stale or mismatched")
        exact(grant["month"], ("year", "month", "timezone"))
        month = Month.at(now, wallet_timezone=terms["period_policy"]["timezone"])
        if Month(**grant["month"]) != month:
            raise WalletError("Native wallet permission period mismatch")
        text(grant["evidence_ref"])
        return grant, month

    async def prune_challenges(self, *, expected_revision):
        """Drop unused challenges past their acceptance window.

        Used challenges are retained as consent evidence; restore cross-checks
        them against their authority. An unused, expired challenge can never be
        accepted, so removing it changes no authority, ledger or binding.
        """
        async with self.coordinator.lock:
            state = self._state()
            now = self._now()
            stale = [aid for aid, c in state["challenges"].items()
                     if not c["used"] and now >= timestamp(c["terms"]["accept_before"])]
            if not stale:
                if type(expected_revision) is not int or expected_revision != state["revision"]:
                    raise WalletError("Monthly ledger revision conflict; reload without retrying payment")
                return {"pruned": 0, "revision": state["revision"]}
            for aid in stale:
                del state["challenges"][aid]
            revision = await self._persist(state, expected_revision)
            return {"pruned": len(stale), "revision": revision}

    async def observe_grant(self, authority_id):
        """Read-only native grant observation for status. Never persisted or trusted later."""
        row = self._state()["authorities"][authority_id]
        grant, month = await self._grant(row["terms"])
        return {"month": asdict(month), "remaining_sats": grant["remaining_sats"],
                "observed_at": grant["observed_at"]}

    async def transition(self, authority_id, action, data, *, expected_revision):
        """Internal accounting transitions only, NEVER a permission to broadcast.

        Data for commit must originate from independent wallet reconciliation.
        S3 must not expose commit/uncertain as arbitrary browser status assertions.
        """
        allowed = {
            "reserve": {"attempt_id", "account_id", "debit_sats", "fee_reserve_sats"},
            "wallet_pending": {"attempt_id", "wallet_action_id"},
            "uncertain": {"attempt_id"},
            "commit": {"attempt_id", "actual_spent_sats", "evidence_ref", "booking_month"},
            "release_uninvoked": {"attempt_id", "reason"},
        }
        if action not in allowed:
            raise WalletError("Unsupported monthly transition")
        exact(data, allowed[action])
        async with self.coordinator.lock:
            state = self._state()
            row = state["authorities"][authority_id]
            ledger = decode(row["ledger"])
            args = copy.deepcopy(data)
            if action in {"reserve", "wallet_pending"}:
                if ledger.cancelled:
                    raise WalletError("Monthly authority cancelled")
                if action == "reserve":
                    binding = state["bindings"].get(data["account_id"])
                    if not binding or binding["authority_id"] != authority_id:
                        raise WalletError("No exclusive monthly binding for this account")
                    ensure_no_legacy_owner(self.api, data["account_id"])
                    final = await self._account(binding)
                    if type(data["debit_sats"]) is not int or data["debit_sats"] != final["debit_sats"]:
                        raise WalletError("Debit must equal the final net amount of this session")
                    binding["final_account"] = final
                    grant, month = await self._grant(row["terms"])
                    args["month"] = month
                    # Validate amount types via pure model before comparing.
                    proposed = ledger.reserve(**args)
                    amount = next(a.reserved_sats for a in proposed.attempts
                                  if a.attempt_id == data["attempt_id"])
                else:
                    attempt = ledger._attempt(data["attempt_id"])
                    if any(a.attempt_id != attempt.attempt_id and
                           a.wallet_action_id == data["wallet_action_id"] for a in ledger.attempts):
                        raise WalletError("Wallet operation already belongs to another session")
                    ensure_no_legacy_owner(self.api, attempt.account_id)
                    await self._account(state["bindings"][attempt.account_id])
                    grant, month = await self._grant(row["terms"])
                    amount = attempt.reserved_sats
                    args["current_month"] = month
                    proposed = ledger.wallet_pending(**args)
                if amount > grant["remaining_sats"]:
                    raise WalletError("Native wallet monthly allowance is insufficient")
            else:
                if action == "commit":
                    attempt = ledger._attempt(data["attempt_id"])
                    debit = state["bindings"][attempt.account_id]["final_account"]["debit_sats"]
                    if type(data["actual_spent_sats"]) is not int or data["actual_spent_sats"] < debit:
                        raise WalletError("Verified wallet spend is below this session's debit")
                    if any(a.attempt_id != attempt.attempt_id and a.state == "committed"
                           and a.evidence_ref == data["evidence_ref"] for a in ledger.attempts):
                        raise WalletError("Payment evidence already belongs to another session")
                    exact(args["booking_month"], ("year", "month", "timezone"))
                    args["booking_month"] = Month(**args["booking_month"])
                try:
                    proposed = getattr(ledger, action)(**args)
                except AllowanceError as exc:
                    raise WalletError(str(exc)) from None
            row["ledger"] = encode(proposed)
            if _plain(state) == _plain(self.api.saved.get(KEY)):
                # Idempotent replay: nothing changed, so no write and no revision bump.
                self._check_revision(expected_revision)
                return {"ledger": copy.deepcopy(row["ledger"]), "revision": state["revision"]}
            revision = await self._persist(state, expected_revision)
            return {"ledger": copy.deepcopy(row["ledger"]), "revision": revision}

    async def cancel(self, authority_id, proof, *, expected_revision):
        async with self.coordinator.lock:
            state = self._state()
            row = state["authorities"][authority_id]
            verify_proof(row["terms"], proof, cancellation=True)
            ledger = decode(row["ledger"])
            if ledger.cancelled:
                return {"cancelled": True, "wallet_permission_revoked": "not_verified",
                        "revision": state["revision"]}
            row["cancel_proof"] = copy.deepcopy(proof)
            row["ledger"] = encode(ledger.cancel())
            revision = await self._persist(state, expected_revision)
            return {"cancelled": True, "wallet_permission_revoked": "not_verified", "revision": revision}
