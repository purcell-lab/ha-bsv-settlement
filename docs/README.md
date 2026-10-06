# Design and implementation documents

Development code now combines a signed pre-session spending budget, sensor-proxy
session accounting, browser-open driver collection and optional server-side
automatic operator credits. A charger-enforced budget gate remains a target, not
an implemented control. The original no-budget mock documentation remains below.

## Current development

- **[Station-first implementation series](station-first-implementation.md):**
  approved 30,000 sat monthly driver workflow, coordinated PR dependencies and
  inactive accounting foundation. Not live monthly wallet authority.
- **[Monthly authority, per-session collection](monthly-authority.md):** staged
  signed consent and durable accounting. Each session settles separately; the
  monthly limit is spending authority, not a monthly bill.
- **[Monthly wallet setup](monthly-wallet-setup.md):** staged S3 portal transport
  and one-action wallet orchestrator. Disabled unless a reviewed activation configures it.
- **[Monthly charging release](monthly-release.md):** failure matrix with offline/mock/native
  evidence, migration, rollback hazards and the separately approved activation runbook.

- **[OCPP recorder investigation](ocpp-recorder-investigation.md):** live installed
  integration evidence and guarded Legacy Sigenergy / OCPP source selection.
- **[Recorder readiness](recorder-readiness.md):** read-only per-direction
  readiness ladder and aligned-window reconciliation of OCPP shadow spans
  against the legacy recorder; no selector or switching.
- **[OCPP lifecycle replay](ocpp-lifecycle-replay.md):** sanitised live and
  synthetic lifecycle fixtures, deterministic shadow replay, the session
  attribution contract (idTag is never identity) and a reproducible
  [shadow report](qa/ocpp-shadow-report-2026-10-05.md); offline, shadow only.
- **[Driver portal](driver-portal.md):** static wallet sign-in and owned session
  history; portal authentication is not spending authority.
- **[Driver collection assurance](driver-collection-assurance.md):** restart
  boundaries, retained attempts and honest confirmation-loss reporting.
- **[Background driver confirmation checks](driver-confirmation-scheduling.md):**
  bounded, fair reassessment of existing collections and manual receipts.
- **[Credit confirmation policy](credit-confirmation-policy.md):** provider
  reassessment separate from wallet receipt acceptance.
- **[Connect BSV Browser](browser-pairing.md):** encrypted mobile pairing,
  separate session spending approval and the wallet network-capability gate.
- **[Driver collection recovery](collection-recovery.md):** persistent failure
  stages and explicitly reviewed, pre-signing recovery without automatic retry.

- **[Acceptance evidence](acceptance-evidence.md):** all ten roadmap outcomes,
  test references, evidence classes and remaining gates.
- **[Recovery drill](operator-recovery-drill.md):** isolated unfunded restore
  procedure; not a claim that a protected restore has been completed.
- **[Record versioning and audit log](record-versioning.md):** registry of every
  persisted store, fail-closed version/migration policy and the hash-chained
  transition log. Does not detect a coherent full-backup rollback.
- **[Release, upgrade and rollback checklist](release-checklist.md):** owner
  roles, supported HA versions, backup and HACS exact-commit pre-flight,
  verification, rollback and downgrade hazards. Automated evidence is separate
  from the protected restore drill.
- **[Security review](security-review-checklist.md):** current trust boundaries
  and the independent-review work still required.
- **[Ongoing driver credits](ongoing-driver-credits.md):** separately authorised
  last-registered-driver routing, fixed session recipients and receipt metadata.
- **[Automatic operator credits](automatic-operator-credits.md):** negative
  balances paid without per-payment approval under a bounded operator policy.
- **[Driver spending approval](driver-session-budget.md):** private invitation,
  dynamic pricing terms, BSV Browser connection and session binding.
- **[Sensor session proxy](sensor-session-proxy.md):** provisional import/export
  metering, interval pricing and proxy transaction IDs.
- **[Manual exception review](session-payment-review.md):** separate reviewed
  requests and credits, mutually exclusive with automatic session settlement.
- **[Tariff provenance](tariff-provenance.md):** append-only, digest-linked
  record of the published price behind every priced interval of a frozen
  account; evidence only, no pricing change.
- **[Golden accounts](golden-accounts.md):** fictional DST, negative-price,
  V2G and rounding sessions checked against an independent reference
  calculator, and what remains unverified.
- **[ADR: estimated-tariff finalisation](adr/estimated-tariff-finalisation.md):**
  *Proposed, needs owner approval under #2.* Reconciles #68 with the #11/#12
  wording.

## Original mock baseline

- **[Settlement interface](settlement-interface.md):** proposed responsibility split, HA actions and entities, pricing rules, API contract, approval and recovery safeguards.
- **[Settlement sequence](settlement-sequence.md):** no-budget flow and explicit mock implementation boundary.
- **[Setup guide](../README.md):** runnable mock service and HA scaffold.
- **[API schema](../openapi.json):** generated from the implemented service.
- **[Verification results](../TEST_RESULTS.md):** 27 passing tests for v0.1.2 and an HTTP demonstration, with untested boundaries identified.

The design document describes the target interface. The implementation deliberately uses `mock_received` and `mock_confirmed`, adds the HA `add_interval` action and provides synthetic identities and manual mock-approval endpoints. No live-wallet capability should be inferred from the design.

## Research and history

- **[Comparable wallet systems](research/wallet-micropayments-comparison.md):** source-cited research, originally conducted against the earlier budget-first brief.
- **[Archived concepts and visuals](archive/README.md):** historical feasibility, budget-first design, sequence and infographic.

## Next implementation milestone

The opt-in [OCPP import shadow adapter](ocpp-import-shadow.md) records partial
entity-observed import spans without payment ownership or charger control.
With the purcell-lab OCPP fork it also records context and protocol provenance,
and an optional [OCPP export shadow](ocpp-export-shadow.md) grades derived export
spans by bound spread, diagnostic only.
[Recorder readiness](recorder-readiness.md) places both recorders on a readiness
ladder that never reaches validated automatically, and reconciles closed OCPP
spans against the legacy Sigenergy counters over aligned windows.
[OCPP lifecycle replay](ocpp-lifecycle-replay.md) replays sanitised live
sessions through that shadow code and fixes the attribution contract.
It does not implement the native settlement recorder or source selector.

Prioritise recovery, confirmation reassessment, real-wallet compatibility and
criterion-level evidence before expanding scope. Sensor-proxy accounting,
browser-open collection and capped operator credits are implemented; native
OCPP, independent meter/account validation and charger-enforced budgets remain
separate work. The acceptance matrix records the remaining gates.

Repository publication does not install this software in Home Assistant or deploy a running wallet service.
