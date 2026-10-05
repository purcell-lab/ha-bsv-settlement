# Native wallet evidence: compatibility before activation

## Decision

Do not enable monthly authority from a browser capability check. This slice adds an explicit, bounded, read-only environment probe; it does not add native permission acquisition, grant verification or a payment executor. Actual wallet-version/device acceptance remains outstanding.

The probe is not imported by the shipped entry point and adds no button or automatic wallet call. A later reviewed diagnostic flow can invoke it with the already selected wallet transport. Its output is labelled `untrusted_browser_observation` and must never be accepted as server-side grant evidence.

## Pinned upstream findings

These are source-code observations, not proof about the wallet version installed on a user's device.

- **Permission query boundary:** `listSpendingAuthorizations` accesses the spending basket using the wallet manager's administrator origin and decrypts permission-token fields. It is a manager operation, not something a generic BRC-100 WalletInterface can safely be assumed to expose. See [WalletPermissionsManager at commit 320a82b](https://github.com/bsv-blockchain/wallet-toolbox/blob/320a82b59e7cf0207698cb96b5bfe260a2f9e45a/src/WalletPermissionsManager.ts).
- **Period:** The inspected manager builds calendar-month labels using `getUTCFullYear` and `getUTCMonth`; `querySpentSince` selects the current UTC month. This is a candidate policy for this exact implementation, not permission to assume all wallets use UTC. See [the pinned manager implementation](https://github.com/bsv-blockchain/wallet-toolbox/blob/320a82b59e7cf0207698cb96b5bfe260a2f9e45a/src/WalletPermissionsManager.ts).
- **Accounting mismatch:** `querySpentSince` reduces action satoshis as `a - e.satoshis`, a net aggregate. Desktop display code explicitly allows a negative total when more came in than went out, displaying it as zero. That is not our app's gross driver-debits-plus-fees ledger. Credits must still never refill the app allowance. See [Desktop spending display at commit 379851d](https://github.com/bsv-blockchain/bsv-desktop/blob/379851d63dd4b051133013f80cc975acf85aae6a/src/lib/utils/spendingProgress.ts).
- **Grouped permission side effect:** The inspected `waitForAuthentication` can fetch manifest permissions and trigger a grouped permission request. It is deliberately excluded from a read-only environment probe. See [the pinned manager implementation](https://github.com/bsv-blockchain/wallet-toolbox/blob/320a82b59e7cf0207698cb96b5bfe260a2f9e45a/src/WalletPermissionsManager.ts).

Source inspection does not establish token renewal/revocation behaviour on a native device, fee inclusion for all action states, cross-origin isolation, pagination completeness, caching behaviour or a secure server-observable grant channel. Those remain evidence requirements.

## Probe contract

`probeWalletEnvironment(wallet, {origin, timeoutMs})` calls only:

1. `isAuthenticated`. If false, missing, malformed or unavailable, stop without unlocking.
2. `getVersion`, only for an already authenticated wallet.
3. `getNetwork`, only for an already authenticated wallet.

Results include only allowlisted values, a timestamp and the exact HTTPS origin. No public key, address, tokens, raw errors, signatures or transactions are collected. Missing or hanging RPCs return bounded unavailable states, not fabricated support; timeout does not cancel an underlying RPC.

The probe always reports monthly grant and receiving permission as `not_verified`, and automatic collection readiness as false. A callable method on a generic RPC proxy is not evidence that its remote wallet implements that method.

## Next implementation decision

Keep the gross app ledger as an independent conservative spending gate. A native grant adapter must be backed by a reviewed wallet-specific observation/bridge that binds identity, origin, network, exact wallet/version policy, freshness and remaining native allowance. A signed browser claim or a screenshot alone is not such an adapter.

Before A3 implementation can claim native compatibility, obtain the actual wallet/version and demonstrate the supported permission request/query/revoke transport, period boundary, fee booking and receiving behaviour. Do not expose administrator-origin calls to the charging page, add a manifest that requests permissions silently, or reinterpret existing session signatures as monthly grants.
