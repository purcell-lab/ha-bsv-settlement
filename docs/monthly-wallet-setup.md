# Monthly wallet setup (S3)

Staged S3 of the [approved workflow](https://github.com/purcell-lab/ha-bsv-settlement/issues/79),
stacked on [S2 / PR #81](https://github.com/purcell-lab/ha-bsv-settlement/pull/81).
Nothing here enables monthly charging, grants a wallet permission or creates a
transaction. Integration startup never configures the monthly portal.

## Transport (`monthly_portal.py`)

Monthly actions are part of the existing driver portal endpoint and inherit its
same-origin JSON, size, rate and cookie checks. Every action needs a wallet-signed
portal login; the identity always comes from that login, never from the request.

| Action | Effect | Persists |
|---|---|---|
| `monthly_status` | Own authority, allowance (Limit/Spent/Reserved/Remaining), native grant observation, receiving registration and readiness | No |
| `monthly_challenge` | Issue (or reuse an unexpired) version 4 terms for this identity and the configured station set | Yes, once |
| `monthly_accept` | Verify the wallet signature over exactly those terms | Yes |
| `monthly_cancel_challenge` | Return the cancellation payload | No |
| `monthly_cancel` | Verify the cancellation signature; native revocation stays `not_verified` | Yes |

Not exposed: `bind`, `reserve`, `wallet_pending`, `uncertain`, `commit`,
`release_uninvoked`. A browser can never assert that a wallet spent, so these
stay internal to the future spending reconciler.

Without a reviewed activation record (`hass.data["bsv_settlement_monthly_portal"]`
holding a `MonthlyPortal` for this wallet and exact origin hostname), status
returns `enabled: false` and every other action is refused with code `disabled`.
Monthly refusals are HTTP 409 with a fixed code and text
(`disabled`, `revision_conflict`, `exists`, `cancelled`, `expired`,
`invalid_proof`, `none`, `unavailable`). They never end the portal sign-in.
Every mutation carries the revision the page last read, so a stale second tab
gets `revision_conflict` instead of overwriting newer state.

### Readiness is the server's answer

`readiness.automatic_collection` is true only when all hold:

- an active, uncancelled monthly authority for this identity;
- the allowance for the current wallet month is not blocked or exhausted;
- the native grant adapter returned fresh, matching evidence (any adapter
  failure reads `unverified`);
- the native grant has positive remaining allowance;
- verified session ownership and closed-account adapters are installed;
- a trusted read-only `MonthlyPortal.collection_health(identity)` adapter observes
  the actual executor/reconciler and returns `ready: true` with a `checked_at`
  timestamp no older than 30 seconds (not a static configuration flag);
- a verified receiving registration exists for this identity;
- the ledger revision is unchanged after asynchronous observations.

This server result is necessary, not sufficient: S4 must also require a currently
verified wallet connection. A specific session still needs enough remaining
allowance for its amount plus fees, exact ownership and final accounting.

Otherwise `readiness.missing` names each gap: `monthly_authority`,
`allowance_review`, `wallet_monthly_permission`, `receiving_registration`.

## Orchestrator (`frontend/driver/monthly-wallet.js`)

`MonthlySetup.run` is the single **Authorise monthly charging** action. Each step
runs only if still missing, so a returning driver sees no new signature prompt:

1. **Wallet:** unlock (`waitForAuthentication`), identity, mainnet. A missing API,
   wrong network or timeout stops before any prompt.
2. **Sign-in:** the existing proof-of-control login, skipped when already signed in
   with the same identity. A different identity stops the flow.
3. **Authority:** fetch terms, check them field by field against what the page
   showed (30,000 sat, fees included, one payment per session, no refill or
   carry-forward, exact origin and stations, no implicitly included session,
   10-minute window, canonical payload), ask the driver, then sign under
   `[2, "ev monthly spending"]` and verify the signature locally before sending.
4. **Receipts:** sync all eligible existing credits for this identity with the
   existing owner-history scan (all pages, original recipient, never a newer driver).
   A connection without `internalizeAction` reports `wallet_receiving` missing.
5. **Wallet permission:** reported from server evidence only. The page never infers
   a native monthly grant.

`cancelMonthly` signs the cancellation payload after checking it names this
authority and identity, and reports native revocation exactly as the server does.

### Resuming partial setup

`MonthlySetup` accepts optional reviewed `setupAdapters` for
`receiving_registration` and `wallet_monthly_permission`. It calls only missing
steps, then re-reads server status; adapter return values are never proof of
registration or spending permission. Re-running skips an existing mandate and
never resets its allowance. Missing adapters are reported explicitly as operator
setup work, not silently treated as completed.

No production setup adapter is shipped in this slice. S4 exposes a resume/check
action for partial setup and an honest explanation when station support is not
installed. Native acquisition and receiving registration remain activation gates.

The orchestrator is not imported by the shipped page in S3; the bundle is
unchanged. S4 wires it into the Station · My charging · History interface.

## Fixtures, not evidence

`frontend/driver/wallet-fixtures.js` simulates capability gaps (locked, missing
API, testnet, sign-only pairing, declined prompt) with a real `ProtoWallet` signer.
These are offline fixtures labelled `offline_fixture`. They do not describe any
real Metanet or BSV Browser version. Native acceptance remains an S5 gate.

## Tests

- `tests/test_monthly_portal.py`: disabled-by-default, sign-in required, one
  authority per identity, reused challenges, honest readiness, other-driver
  isolation, stale-tab conflicts, absent accounting transitions, wallet-signed
  cancellation, origin-scoped activation, failed durable write, TypeScript SDK
  signature over HTTP, and the shipped browser validator against Python-issued terms.
- `frontend/driver/monthly-wallet.test.js`: fresh and returning drivers,
  missing-only prompts, fail-closed capabilities, tampered terms, declined terms,
  disabled station, cancelled authority, stale tab, visibility changes and
  cancellation payload checks.

## Remaining for S4/S5

- UI wiring, terms display and wallet drawer (S4).
- Monthly-bound credit/zero routes and per-session wallet invocation through the
  spending reconciler (exactly once per session; repeated `wallet_pending` reads
  never re-invoke the wallet).
- Production adapters, origin isolation decision, challenge pruning and the
  reviewed activation record (S5).
