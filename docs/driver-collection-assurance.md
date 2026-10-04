# Driver collection interruption and confirmation evidence

This tranche advances #7, #19 and #22. It changes evidence reporting and adds
offline tests. It does not change recipients, signed budgets, fees, payment
permissions, wallet signing, broadcast policy or charger controls.

## Confirmation reassessment

Reconciliation compares the provider's raw transaction with the exact stored
signed bytes, then checks the transaction ID, authorised shape, output ownership
and confirmation evidence. A valid zero count means `provider_unconfirmed`.
A positive count means `provider_confirmed`, labelled as provider evidence.
Unavailable, malformed or mismatching evidence means `broadcast_unknown`,
with the current confirmation count cleared.

Previously a failed check could leave a prior `provider_confirmed` state and
count in place. The correction prevents stale confirmation from masquerading
as a successful current check. It does not imply that the payment failed.
The original transaction, signed bytes, immutable account, attempt and any
received-output ownership remain retained. A repeated report only reconciles
the original transaction; it cannot broadcast a replacement.

These checks also support the subsequent
[bounded background reassessment worker](driver-confirmation-scheduling.md).
The worker adds fair polling for existing driver transactions, including manual
receipt parity, without provider calls from the read-only history portal.
Operator-credit reassessment retains its separate scheduling policy.

## Deterministic interruption matrix

`tests/test_collection_interruption_matrix.py` cancels execution at 14 boundaries:

| Boundary | Before | After | Recovery invariant |
|---|---|---|---|
| Frozen quote persistence | Yes | Yes | Same account and one collection owner if durable |
| Wallet-attempt claim persistence | Yes | Yes | Durable claim cannot be claimed again |
| One-use signing permit persistence | Yes | Yes | Durable permit cannot be issued again |
| Signed intent persistence | Yes | Yes | Provider effect requires previously durable signed intent |
| Provider submission | Yes | Yes | Lost acknowledgement cannot create a second broadcast |
| Submission acknowledgement persistence | Yes | Yes | Recover original signed bytes only |
| Confirmation persistence | Yes | Yes | Reconcile existing transaction without payment mutation |

Every case reloads a new API object from real Home Assistant storage and uses
a fictional recording provider. Repeated reconciliation cannot add an external
effect. Before a signed transaction is durable, this test deliberately does
not automatically retry wallet creation or signing. An unresolved browser
attempt still needs the established review path.

`tests/test_collection_reassessment.py` adds nine cases: provider outage,
different/malformed/missing raw bytes, wrong transaction ID, missing/negative/
boolean confirmation counts, and a valid zero-count reversal and return.
The failure cases reload storage, repeat the original report and restore
provider evidence with exactly one total fictional broadcast.

## Evidence limits and next tranche

These tests do not prove a native wallet behaved correctly, an isolated
protected backup was restored, or a coherent old backup can be reconciled
against independent chain evidence. They do not provide independently verified
header-chain finality or prove every manual-payment interruption.

Next work is bounded driver reassessment scheduling, manual-payment parity,
an isolated unfunded protected restore, and a pinned native-wallet device
matrix. No funding, transfer, production restart or pilot expansion is
authorised by passing this suite.
