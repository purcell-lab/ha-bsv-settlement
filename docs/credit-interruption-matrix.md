# Automatic-credit interruption matrix

These tests exercise session-approved and ongoing operator-credit workers with
real HA storage, fictional identities and a recording fake provider. No real
wallet, funded backup or network broadcaster is used.

## Executed boundary fixtures

`tests/test_credit_interruption_matrix.py` parametrises both credit workers
across these cancellation points:

| Interruption | Expected recovery |
|---|---|
| Before unsigned account persistence | No network effect; an eligible unsigned account may be recreated |
| After unsigned account persistence | Frozen account retained; first payment may proceed if unchanged |
| Before signed-intent persistence | No external effect; unsigned persisted state may resume |
| After signed-intent persistence, before broadcast invocation | Original bytes and reservation retained; uncertainty, no automatic resend |
| Before fake broadcaster accepts bytes | Original signed intent retained; uncertainty, no automatic resend |
| After fake acceptance, before response delivery | Reconcile the original transaction; no second broadcast |
| Before provider acknowledgement is persisted | Durable uncertain intent reconciles to the same transaction |
| After acknowledgement is persisted | Reconcile the submitted transaction, without replacement |

Every recovered signed record is compared against its exact transaction bytes,
ID, frozen account hash, recipient, amount, fee and source reservation. Three
subsequent worker ticks must not cause a duplicate external effect. The fake
provider refuses to invent evidence for a transaction it never observed.

Additional tests run four concurrent coordinator refreshes through the real
coordinator lock and assert one payment/reservation. A changed frozen account
after an unsigned restart remains blocked and cannot be paid.

## Scope and remaining gates

Cancellation is injected deterministically by raising `asyncio.CancelledError`
at the named awaited boundaries. This is not a power-cut filesystem durability
test, protected-backup restore or proof that every possible instruction is
covered. It does not complete manual-payment or driver-collection interruption
coverage, nor independent chain verification.

A durable signed intent with no provider observation is deliberately stranded
as unresolved. Absence is not evidence of failure and does not authorise a
replacement or resend. Resolve through a separately reviewed recovery process.

This matrix advances #7/#19/#22. Keep those issues open for remaining paths,
stale/coherent restores, supported migration and independent evidence.
