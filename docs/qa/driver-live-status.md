# Driver live status and approval matching

## Scope

Keep the existing settlement method and signed budgets. Match a legacy next-session reservation only after explicit operator confirmation. A receiving registration permits owner-scoped display, not driver spending.

The normal multi-session path retains its existing automatic session handling. This revision neither converts old single-session approvals into weekly authority nor changes payment policies.

## QA inventory

- Current buy and sell prices remain visible before approval, during charging, while operator matching is pending, and during settlement.
- Session account mirrors the operator summary: provisional sat amount, AUD and signed conversion, import/export kWh, energy-weighted average rates, start time and proxy reference.
- OCPP connector status is separate from settlement status and energy direction. It comes from one validated observer linked to the session recorder.
- Bound historical links never switch to a newer driver's live session.
- Unbound/aggregate links display current session energy only with a fixed receiving route belonging to that approval. This does not bind spending authority.
- Refresh is every 15 seconds; stale or failed reads withhold old totals. Missing and zero values remain distinct.
- Driver approval saved but unmatched is explicit. The operator overview offers a guarded confirmation only for one server-reported current-session candidate.
- Confirmation cancellation, non-admin access, stale approval, session rotation and closure must not submit a binding.
- Mobile and desktop: no horizontal overflow, rates and provisional summary visible without opening disclosures; full references and meter notes expand separately.
- Mock scenarios: active, unmatched reservation, expired, unavailable prices, lost connection, unconfirmed and held payment.
- No live wallet signing, spending, receipt import, retry or policy change is part of UI validation.

## Validation results

- 118 driver tests passed.
- 82 operator tests passed.
- 30 focused Python tests passed, including capability-scoped telemetry, explicit matching, no backdating, no scope expansion and no payment calls.
- Mock mobile browser: current rates, OCPP state, provisional sat/AUD, both energy totals, weighted averages, start time and reference are visible outside collapsed terms. No horizontal overflow.
- Observed the 15-second poll update import energy from 3.800 to 4.000 kWh, provisional charge from 89 to 94 sat and OCPP state from Charging to SuspendedEV, without interaction.
- Unmatched reservation keeps current rates and owner-scoped energy visible while explaining that session confirmation is still pending.
- Interrupted connection withholds prior totals and OCPP state as Unavailable. Mock claim/draft/sign/report/approval counters remained zero.
- Full local Python regression passed: 1,032 tests, three dependency warnings. GitHub CI is recorded in the PR checks.

## Release boundary

This revision is a PR, not a live installation. Existing terms, financial caps, payment providers and recorder authority are unchanged. OCPP status is read-only and does not replace Sigenergy energy accounting.

The authorised operational match of the current live session was performed separately using the existing binding service. It does not install this UI revision or broaden the driver's signed budget.

## Main Status payment buttons and wallet connection recovery

The later user request adds two immediate-action 5 kWh buttons to Status, with AUD and sat amounts directly on each button. The click authorises one operator-funded payout within the existing 1,000 sat total, with a fresh fee quote; amounts owed by the driver create a separate manual request. No charging consent is expanded. Existing preparation controls remain on the two credit tabs.

- 1,040 Python tests passed, including 27 energy-adjustment cases covering both tariff signs, restart replay, no adoption of manual reviews, failed-attempt safety, total limits and administrator context.
- 88 operator tests and 120 driver tests passed.
- Mobile mock browser at 390 px: both buttons show AUD and sat without horizontal overflow. Export returned “Driver credit submitted: 41 sat. Awaiting block confirmation.” Import returned “Driver payment requested: 62 sat. Driver wallet approval and payment are still required.”
- Mock wallet-unavailable scenario reproduces the reported SDK error and promotes “Copy link for BSV Browser”. No claims, drafts, signatures, approval submissions or payment reports occurred.
- The deployed preview uses in-memory mock request storage because its sandbox forbids localStorage. Production retains nonsecret UUIDs in browser storage and blocks sending if storage is unavailable.
- These checks do not establish that the user's actual BSV Browser is connected. No live HA services, funds or registration changes were used for this addition.

Installation must update both frontend and backend and refresh the operator-card resource cache marker. The recorder sensor now supplies its exact nonsecret config entry ID, so the existing overview configuration does not need a guessed recorder identifier.
