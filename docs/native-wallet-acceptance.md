# Native-wallet acceptance: weekly-first test pack

## Decision and current boundary

Prepare native-device evidence before expanding wallet automation. The default
remains a signed 1,000 sat shared allowance, including driver-paid fees, valid
for seven days, with one settlement per completed session. Credits do not refill
that allowance. Application consent is not funded escrow, a wallet-native
permission grant or assurance that a disconnected browser can pay.

R1–R3 were deployed at `fdbcacc78ccc1324f11f852e01c9f947beebeaaf`.
Their [deployment record](https://github.com/purcell-lab/ha-bsv-settlement/issues/91#issuecomment-6009727833)
does not establish native acceptance. This follow-on pack is staged only:
no deployed page, permission request, HA change or real-value test is included.

## Names and separate evidence

| Function | Application terminology | Native interface or evidence |
|---|---|---|
| Prove wallet identity | Wallet sign-in | Challenge signature verified against the exact identity; connection alone is insufficient |
| Approve weekly charging | Weekly spending mandate | Application-signed terms, not a wallet-native seven-day permission |
| Permit wallet operations | Native wallet permissions | Installed wallet's permission system; not inferred from method presence or app approval |
| Pay a completed session | Driver collection | `createAction` / `signAction` with `noSend`, followed by HA validation and exact-byte broadcast |
| Receive an existing operator payment | Credit receipt import | `internalizeAction`, followed by the application's signed receipt acknowledgement |
| Inspect an outgoing payment | Wallet action observation | Exact labelled `listActions`; not repair, receipt acceptance or chain proof |
| Observe block inclusion | Provider confirmation | Provider evidence for exact transaction and output, not independent SPV |

The pinned SDK documents `sendWith` as submitting previous `noSend` actions,
not a passive status notification. `abortAction` changes wallet state.
Neither is an acceptable workaround for the outgoing status mismatch without
a separately reviewed protocol and explicit authority. See the repository's
[pinned-interface findings](outgoing-wallet-evidence.md) and
[native implementation findings](wallet-native-evidence.md).

## Run sequence and authority gates

### N1: Existing-transaction inspection, no mutation

Suggested first native run: inspect one already provider-confirmed outgoing
payment in the wallet that originally created it. Keep the historical uncertain
collection out of this first test. Privately bind the exact original identity,
budget ID, transaction ID, application origin and a public-safe alias.

1. Record installed wallet name/build, device/OS, transport, SDK and app commit.
   Do not assume BSV Desktop, BSV Browser and Metanet Explorer are interchangeable.
2. Check app and wallet operation scopes before opening a page. The normal
   driver portal may resume receipt sync or collection on sign-in, reconnection,
   timer or visibility change. It is NOT the read-only diagnostic harness.
3. A reviewed, isolated harness on the approved application origin must supply
   the already selected wallet transport. This PR supplies the helper, not a
   hosted harness or a connection flow. Approval to deploy/run that harness
   is still required; using another origin changes permission behaviour.
4. Explicitly invoke `captureNativeObservation(wallet, options)` once. Do not
   discover, unlock or reconnect a wallet as a side effect of inspection.
5. Require exact mainnet identity before and after the one bounded, labelled
   action query. Capture wallet status separately from the saved provider check.
6. Denial, unsupported methods, mismatched identity or no bounded query match
   are inconclusive. Do not grant extra permissions just to make a test pass.
7. If the wallet displays a permission/signing prompt despite read-only flags,
   cancel and stop; record the discrepancy. Do not approve it as “read-only”.
8. On timeout, stop; the RPC may still be outstanding. Do not change transport
   wrappers to bypass the guard. The helper blocks the same object until the
   outstanding RPC ends; it cannot enforce a cross-tab or remote-wallet lock.

Allowed helper calls: `isAuthenticated`, `getVersion`, `getNetwork`,
`getPublicKey` with `seekPermission:false`, and `listActions` with
`seekPermission:false`, one exact label, at most 50 rows, no inputs, outputs,
scripts or raw transaction request. All other capabilities are excluded from
the wrapper. No upload or persistence is performed.

All results remain `untrusted_browser_observation`; a fixture can produce the
same structure. A reviewer must corroborate actual device/version execution.
The helper never reports native acceptance, grant verification, automatic
collection readiness or successful status repair.

### N2: Receipt acceptance by the original recipient

This is a wallet mutation, even though it must not create a new payment.
The two existing provider-confirmed credits without recorded acceptance belong
to the original receiving identity. Do not redirect them to a replacement
wallet or newly registered driver.

Before authorisation, privately list the exact existing transaction/output,
amount, receiving identity and proof status for each receipt. Check for an
existing wallet record and application acknowledgement first. Then ask for
authority to import only those receipts and record their acknowledgements.

The normal portal may batch more receipts or resume other work. Inspect the
entire queued scope first. If it exceeds the approved list, prepare an
exact-target flow rather than opening the page and hoping only one runs.
If already accepted but acknowledgement was lost, use the existing guarded
acknowledgement path only after exact wallet evidence. Do not claim a wallet
balance audit from an `accepted` response.

### N3: Weekly debit lifecycle

First use controlled fixtures or an isolated test environment for expiry,
exhaustion, denied signing, concurrent pages, disconnect and reorg/provider
failure. Never manufacture those conditions on the active mainnet session.

A later supervised mainnet run requires an exact scope: two distinct completed
session accounts, original recipient, each amount, expected fee/total bounds,
existing allowance and before/after committed allowance. Do not create a fresh
weekly invitation, reassign sessions or spend the allowance during preparation.
Existing consent does not authorise arbitrary synthetic acceptance payments.

One app approval may still involve multiple native wallet prompts. Record the
number and text of prompts; do not promise one-click completion until that
exact device/transport has demonstrated it.

### N4: Outgoing “nosend” reconciliation

Observation is ready for native testing; repair remains NOT IMPLEMENTED.
Before coding a native repair, identify a documented method in the actual
wallet/version and obtain upstream confirmation of its semantics.

Required guarantees: same existing transaction, no new spend/signature,
no change-output invalidation, exact identity/origin binding, defined idempotency,
and explicit behaviour for unconfirmed, missing, conflicting and reorg evidence.
Record whether any bytes are submitted or any local state changes. Do not use
`sendWith`, abort/recreate, arbitrary receipt import or direct wallet-database
edits as an unreviewed substitute.

## Native evidence matrix

Each case starts `not_run`. An automated fixture pass is regression coverage,
not a native pass. Record `pass`, `fail`, `blocked` or `inconclusive` only against
the exact case, wallet build, transport and evidence type.

| ID | Case and expected result | Authority / evidence gate |
|---|---|---|
| NW01 | Authenticated mainnet wallet/version and exact original identity observed | Approved read-only harness; actual device |
| NW02 | Exact confirmed outgoing payment found with original budget label; local status reported separately | N1; no repair or receipt claim |
| NW03 | Locked, wrong, switched, denied or unsupported wallet stops without permission acquisition | Fixture first; native scoped refusal |
| NW04 | Timeout/interruption stops; no repeated RPC, new payment or optimistic result | Fixture first; native no funds in flight |
| NW05 | Weekly terms display 1,000 sat, seven days, fees included and credits do not replenish | Explicit fresh-consent test only |
| NW06 | Two completed sessions yield two distinct transactions; committed allowance includes both amounts and actual fees | Exact real-value authority required |
| NW07 | Expired/exhausted/revoked mandate blocks new spend; old transaction still reconciles | Isolated environment; no live policy edits |
| NW08 | Background/reconnect returns to the same attempt; uncertainty retains reservation and hold | Failure fixture then supervised native scope |
| NW09 | Original receiving wallet imports one exact existing confirmed receipt and signs acknowledgement | N2 exact receipt mutation approval |
| NW10 | Wrong receiving identity refused; accepted receipt/history reload creates no replacement transfer | Fixture first; exact native scope |
| NW11 | Lost acknowledgement and repeated receipt sync remain idempotent | Isolated failure test; do not blindly reimport mainnet |
| NW12 | Confirmed, unconfirmed, missing and conflicting provider evidence remain distinct from wallet local status/acceptance | Existing regressions plus observed native display |
| NW13 | Native outgoing repair satisfies N4 guarantees | Blocked pending supported protocol; cannot pass from `listActions` |
| NW14 | Mobile history shows direction, amount, energy/average price and Received only with receipt evidence | Same device/build; no receipt fabrication |

## Test ownership and evidence

Keep existing tests as the owners of financial and consent logic. The
[weekly matrix](weekly-acceptance-matrix.md) maps these to their regression
owners. This PR adds only orchestration tests in
`frontend/driver/native-acceptance.test.js`, reusing the environment and outgoing
inspection modules. It does not duplicate the collection or receipt engine.

Run from the repository root:

```sh
node --test frontend/driver/native-acceptance.test.js
python scripts/validate.py all
```

Use the pinned development environment for the full command. All four CI jobs,
including HACS and reproducible builds, remain deployment gates. Tests in this
pack use fictional identifiers and no live services.

Record each native run with [the evidence template](native-wallet-run-template.md).
Keep exact identity, budget/transaction/output references and original screenshots
in a protected private record; publish only reviewed aliases and non-sensitive
conclusions. Do not commit filled private manifests, links, keys, signatures,
raw transactions, seed phrases, pairing URIs or session capabilities.

## Completion and next implementation order

1. Review this pack and tests; keep staged.
2. Approve and implement the narrowly scoped same-origin diagnostic harness,
   then run N1 on an already-confirmed outgoing action.
3. Prepare the original-wallet receipt import scope; request mutation approval
   before N2. Obtain the original wallet if it is unavailable.
4. Confirm the actual native wallet's supported same-transaction reconciliation
   path. If unsupported, document the limitation and prepare an upstream request.
5. Execute independently approved N3 lifecycle tests; preserve failure evidence.

Do not close [#91](https://github.com/purcell-lab/ha-bsv-settlement/issues/91)
or [#22](https://github.com/purcell-lab/ha-bsv-settlement/issues/22) on this pack,
unit tests or a single successful mainnet payment. Protected isolated restore,
independent reference accounts and security/commercial gates remain separate.
