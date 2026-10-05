# Monthly wallet adapters: staged follow-on series

This follows deployed S1–S5 under [issue #79](https://github.com/purcell-lab/ha-bsv-settlement/issues/79). Implementation is authorised; merge, deployment, monthly activation and real-value tests are not authorised by this document.

## Sequence and boundaries

| Slice | Deliverable | Status / prerequisite |
|---|---|---|
| A1 | Exact retained HA account adapter | [PR #85](https://github.com/purcell-lab/ha-bsv-settlement/pull/85), staged and internal only; no startup wiring |
| A2 | Read-only wallet compatibility probe and pinned-source findings | Implemented in this stacked slice; see [native evidence](wallet-native-evidence.md); does not grant or verify spending authority |
| A3 | Reviewed native period/grant bridge and missing-only receiving setup | Requires evidence from the actual supported wallet/version and a trusted transport; no browser self-attestation |
| A4 | Verified physical session ownership and explicit current-session inclusion | Requires reviewed station/connector ownership evidence; never infer ownership from latest login |
| A5 | Exactly-once per-session execution, reconciliation, credit/zero routes | Requires A3/A4; uncertain attempts retain operation IDs and never cause replacement signing |
| A6 | Native device matrix and gated pilot | Separate approval, amounts/recipients/fees and activation review |

The 30,000 sat monthly limit includes driver-paid fees. Collection remains one final net payment per closed charging session. Credits do not refill the app allowance. No offline signing capability or funded reserve is inferred.

## A1: retained session accounting

`RetainedSessionAccounts(hass, stations)` supplies the `resolve_record` callable for `MonthlyAuthorities`. The station-to-recorder mapping is reviewed operator configuration, never a browser payload.

It refreshes the exact loaded `sensor_proxy` recorder, requires successful refresh, an observation timestamp no more than 30 seconds old and no source issues, then searches latest, previous and retained history for the exact session. A debounced refresh cannot make a stale or future-dated snapshot count as fresh. It verifies the frozen transaction and opening timestamp, closed-account pricing/quality requirements and end time. Missing, conflicting or stale accounts fail closed; no newer session is substituted.

Returned records are deep copies. Positive, negative and zero accounts retain their original amounts; selecting a debit, credit or zero route remains the caller's responsibility. Existing monthly debit logic still rejects credit/zero accounts.

This is not an ownership verifier. Existing bound metadata must come from the durable monthly authority service after trusted ownership verification. The adapter is not installed into startup and creates no authority, payment, HA service or endpoint.

## Verification limits

Tests use fictional retained records around the real adapter and existing `account_snapshot` rules. They cover history rotation, ambiguous copies, wrong station/transaction, stale/replaced recorders, failed refresh, invalid pricing, open accounts and copy isolation. They do not establish native wallet capability, physical vehicle ownership or live settlement.

## BSV Browser decision

The operator selected BSV Browser and authorised merging/deploying the open
PRs on 6 October 2026. That changes the deployment scope, not permission to
fake native grants or move pilot funds. The reviewed native API exposes
per-session payment methods but no monthly permission request/query/revoke
bridge. See [BSV Browser acceptance and the explicit architecture options](bsv-browser-acceptance.md).
A3–A6 are therefore incomplete, not implicitly satisfied by A1/A2 deployment.
