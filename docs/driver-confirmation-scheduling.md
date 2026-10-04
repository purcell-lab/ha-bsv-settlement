# Background driver-payment confirmation checks

This tranche advances #7, #17, #19 and #22. It adds bounded read-only provider
checks for existing driver collections and already attributed manual driver
payments. It creates no payment, invitation, signature, signing permit or
broadcast. Operator-credit scheduling is unchanged.

## Scheduling policy

- The mainnet coordinator runs the worker under its existing wallet lock.
- At most two due records are checked per tick, shared across both driver paths.
  Each check uses at most two provider GET requests, with a five-second combined
  network deadline. This bounds this worker, not all integration network traffic.
- Pending or unavailable evidence is due after 60 seconds; confirmed evidence
  is due after 300 seconds. These are minimum intervals, not guaranteed latency
  when the queue is large, HA is unavailable or other work delays the coordinator.
- A sorted round-robin cursor prevents early records from starving later ones.
  Cursor and a 15-second minimum tick interval are persisted before network reads
  in the existing checkpointed ledger.
- Missing, malformed, naive or future check timestamps are treated as due.
  The shared cursor and tick budget still apply. Restart retains the cursor.
- Expiry or revocation does not prevent reassessment of an already signed
  transaction. Unsigned, held, recovery-ready and waived collection states are
  excluded. Manual requests without an attributed receipt are excluded.
- The history portal remains a cached read. This worker does not add provider
  calls to page loads, nor infer payment from an address balance.

## Evidence parity

Both driver paths require a matching transaction ID and either a non-negative
integer confirmation count or complete matching unmined transaction metadata.
Missing or malformed counts cannot silently become zero. A valid zero means
awaiting block confirmation; provider-confirmed is not independent chain finality.

Automatic collections continue to compare exact retained signed bytes and the
authorised draft. Manual receipts continue to require the administrator's
original payer-reference attribution and exact output/recipient/amount checks.
New successful manual checks retain raw bytes privately. Legacy attributed
receipts without those bytes adopt them only after matching the original
transaction ID, output and exclusive ownership. Raw bytes are not returned by
the public review/status serializer.

An outage, timeout or invalid/mismatching evidence clears the current
confirmation count. Automatic collections become `broadcast_unknown`; manual
receipts become `driver_payment_evidence_unavailable`. Original transaction
identity, frozen terms, output ownership and collection attempts stay retained.
Successful later reads can recover the evidence state without a new transfer.
Cancellation propagates and checkpoint/storage failures remain fail-closed.

## Boundaries

This does not resolve an issued manual request with no known payment, including
replacement by a fresh wallet-consent invitation. That requires a separate,
reviewed recovery design; this worker must not assume no receipt means unpaid.
It cannot release a hold, waive an account, rotate a recipient or extend consent.
It cannot import a receipt into BSV Browser or make an offline driver sign.

Independent header-chain verification, coherent stale-backup detection and
native-wallet acceptance remain separate roadmap gates. No production
installation, restart, funding or real-funds test is part of this PR.

## Offline validation

`tests/test_confirmation_scheduler.py` exercises queue fairness across both
paths, restart/cooldown behaviour, malformed and future timestamps, eligibility,
checkpoint failure, network deadlines, cancellation, coordinator locking,
revoked-approval reconciliation, manual confirmation loss/recovery, exclusive
output ownership and legacy raw-evidence adoption. All provider data is
fictional; no real wallet or broadcaster is contacted.
