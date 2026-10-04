# Demonstration acceptance and evidence

This is the working evidence plan for issues #1 and #22. It does not authorise
funding, transfers, live charger control, a restart, a release or a wider pilot.
The baseline is [487d677](https://github.com/purcell-lab/ha-bsv-settlement/commit/487d677742288c1089ce67ac4d6633703e723158),
with 181 Python/HA, 24 driver JavaScript and five operator tests passing in
[baseline CI](https://github.com/purcell-lab/ha-bsv-settlement/actions/runs/37066045552).
Later branch tests are additional evidence, not retrospective baseline results.

## Current checkpoint: 4 October 2026

The deployed checkpoint is [PR #58](https://github.com/purcell-lab/ha-bsv-settlement/pull/58),
commit `309e8a8cacdb3e120ded0dd1b4da29a674c76579`. Its
[CI](https://github.com/purcell-lab/ha-bsv-settlement/actions/runs/37160686208)
passed 729 Python/HA, 86 driver JavaScript and 56 operator JavaScript tests.
The original baseline above is retained as historical evidence, not replaced.

Since the earlier batches, implementation includes seven-day aggregate driver
consent, fee-aware credits, completed-session resolution, separate receipt
acceptance reporting, receipt synchronisation and the static wallet-sign-in
portal with expandable one-line session history. These are implemented features,
not closure of the complete roadmap acceptance criteria.

The [driver assurance tranche](driver-collection-assurance.md) adds explicit
confirmation-loss correction and deterministic driver interruption tests.
Its branch evidence remains separate from the deployed checkpoint.
Protected restore, independent security/accounting, native-wallet compatibility,
native OCPP and safe physical charger enforcement remain open gates.

## Acceptance inventory

The machine-readable [matrix](../validation/acceptance-matrix.json) maps all ten
roadmap outcomes to actual Python test functions, open issues and remaining
gates. CI checks its coverage and reference integrity. A valid pointer proves
that a test exists; a passing test proves only the case it exercises.

| Outcome | Baseline assessment | Remaining gate |
|---|---|---|
| D01 Driver proof and consent | Partial | Named wallet/version and independent endpoint assessment |
| D02 No start without consent | Blocked | Exposure engine and safely arbitrated charger control |
| D03 Directional interval attribution | Partial | Native OCPP and independent meter reconciliation |
| D04 Missing-data/exhaustion behaviour | Partial | Stop reserve, physical pause and failure escalation |
| D05 Independent final accounts | Blocked | Golden accounts, meter tolerance and tariff finality |
| D06 Debit, credit and zero outcomes | Partial | Real-device debit and unified zero/tiny-account evidence |
| D07 Immutable terms and payment evidence | Partial | Full provenance, finality and recovery assurance |
| D08 No duplicate effects under interruption | Partial | All transition boundaries, restore and device failures |
| D09 Honest session/payment UX | Partial | Native-device accessibility and distinction between portal login, wallet connection and payment authority |
| D10 Recovery and public-data protection | Blocked | Protected isolated restore and independent review |

Do not mark the whole demonstration complete by counting unit tests. Checked
criteria in an issue may describe implemented behaviour while that issue's
upstream acceptance or independent validation remains open.

## Evidence classes

- **Offline automated:** fictional identities/provider responses, deterministic
  assertions, exact commit and CI run. No real-funds claim.
- **Isolated browser:** actual frontend bundle with a fake wallet/HA transport,
  viewport/theme and exercised actions. Not native-wallet compatibility.
- **Provider observed:** read-only provider result for exact signed bytes,
  timestamp and confirmation count. Not independent header-chain proof.
- **Real-wallet accepted:** wallet/version and receipt import result with
  redacted capture. Acceptance is not a new transfer or irrevocable finality.
- **Physical charger:** measured device/connector behaviour under separately
  approved safe test conditions. Simulated export is not physical V2G evidence.
- **Independent:** reviewer/calculation method, differences, acceptance and
  unresolved findings. Developer assertions are not independent review.

Keep public records free of actual wallet addresses, transaction references
that identify a wallet, capability URLs, HA entry/entity/site identifiers,
seeds and signing material. Retain sensitive evidence privately under the
operator's approved retention/access policy.

## Failure-injection sequence

Use fictional or isolated environments first. Capture one immutable record and
prove that each scenario ends in the same payment or an explicit unresolved
obligation, never a replacement transfer.

| Stage | Cases to execute | Required invariant |
|---|---|---|
| Consent | Wrong wallet/operator, altered terms, expiry, revocation, legacy receipt, exact retry | No authority expansion; identical retry is idempotent |
| Assignment | New registration, missing registration, revoked latest, restart window, policy generation | Recipient/rate fixed per route; no historical sweep |
| Metering | Reset, gap, wrong units, out-of-order/late sample, Occupied/resume, source unavailable/recovered | No invented energy; correct stable activity identity |
| Pricing | Missing/estimated/corrected rates, negative rates both directions, offset/DST, rounding | Held or reproducible account; paid account not silently rewritten |
| Freeze | Account changes before/after signing, source record reopens | Reject changed account; preserve original obligation |
| Funds | No suitable confirmed output, stale spent input, manual/automatic conflict | No double reservation or unchecked new signature |
| Persist | Failure before claim, before signature, after signature before network, after network before result | Durable intent before effect; recover same bytes |
| Provider | Timeout, malformed response, wrong raw bytes/ID, disappearance, confirmation loss/return | Explicit uncertainty, no replacement or automatic resend |
| Wallet | Refusal, insufficient funds, background/closed browser, permission prompt, reconnect, import retry | No implied collection; import does not send money |
| Restore | Matching unfunded backup, missing/changed key, stale ledger, conflicting current evidence | Identity preserved or fail closed; broadcasting remains disabled |
| Interface | Missing/stale values, recovered status, long IDs, keyboard, mobile, dark theme | No false readiness, zero substitution or confirmation claim |

Record coverage of each await/persistence boundary in #7; this table alone does
not claim all cases have been executed.

## Run record template

Each retained run must include:

```text
Run ID and UTC timestamp with offset:
Commit and environment versions:
Outcome IDs and issue criteria:
Evidence class:
Named test functions or manual procedure:
Fictional/private fixture identifier:
Preconditions and separate approval reference, if required:
Expected result:
Observed result and redacted artifact:
Broadcast count and immutable payment/reference comparison:
Pass / fail / blocked:
Reviewer and unresolved findings:
Cleanup / recovery outcome:
```

Earlier authorised live credit observations must be recorded honestly as
limited observations, with their missing prerequisites visible. They cannot
retroactively satisfy a backup, independent-review or complete go/no-go gate.

## Next decisions

Finish the [recovery drill](operator-recovery-drill.md) and
[threat-review checklist](security-review-checklist.md), then consolidate
actual-wallet evidence. Review payment confirmation loss and session-status
recovery fixes before deployment. Native OCPP, budget exposure and physical
control follow their issue dependencies. Pilot expansion remains separately
gated by #24; no release date or individual assignment is implied.
