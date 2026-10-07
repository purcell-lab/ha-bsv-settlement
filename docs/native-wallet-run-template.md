# Native-wallet run record

Blank template only. Do not commit populated private evidence to the public
repository. A reviewer records outcomes; the helper cannot authenticate a
claim that an observation came from a native rather than mocked wallet.

## Scope

| Field | Value |
|---|---|
| Run alias / case IDs | Not run |
| UTC and local time / timezone | Not recorded |
| App commit / served bundle hash | Not recorded |
| Wallet product / installed build | Not recorded |
| Device / OS / transport | Not recorded |
| SDK version | Not recorded |
| Exact origin / network | Private record |
| Evidence class | Fixture / native-observed / provider-reported / independently verified |
| Scope approval and reviewer | Not recorded |
| Permitted effects | None until explicit scope approval |
| Private target-manifest reference | Not recorded; alias only in public summary |

## Before

- Exact original identity and target transaction/output privately verified:
  not checked.
- App mandate terms and native permission state recorded separately: not checked.
- Queued collections and receipt imports inspected without executing: not checked.
- Payment identities, recipient routes, allowance reservations and holds:
  not recorded.
- Provider observation timestamp, exact output match and confirmations:
  not recorded.
- Wallet local action status / receipt state: not recorded.
- No active or uncertain payment will be used for failure injection: not checked.

## Observation and outcome

| Field | Value |
|---|---|
| Case outcome | not_run |
| Helper reason / method-name trace | Not observed |
| Native prompt count and type | Not observed |
| Wallet local outgoing status | Not observed |
| Provider confirmation | Not checked |
| Wallet receipt acceptance | Not assessed |
| Native outgoing status repair | Not implemented |
| Actual external effects | Not observed |
| Before/after invariants | Not checked |
| Interruption / outstanding RPC | Not checked |
| Evidence location | Private, reviewed reference only |
| Reviewer and remaining blocker | Not assigned |

Allowed outcome values: `not_run`, `pass`, `fail`, `blocked`, `inconclusive`.
“Not observed” is not evidence that payment did not occur. A provider-confirmed
transaction is not proof of native wallet receipt acceptance or native status repair.

## Stop conditions

Stop on unexpected prompt, identity/origin/network change, extra queued action,
stale provider evidence, mismatched amount/output, timeout or ambiguous result.
Do not retry, reconnect, re-sign, reimport, waive or release a hold as a diagnostic
workaround. Preserve exact attempt identity and request a separately scoped decision.
