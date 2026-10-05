# BSV Browser: implementation decision and native acceptance

The operator selected BSV Browser as the primary wallet on 6 October 2026.
This document records source evidence, not acceptance on the operator's phone.
Existing per-session collection and credit receipt flows remain in use.

## Pinned source observations

Reviewed [BSV Browser commit baf14a0](https://github.com/bsv-blockchain/bsv-browser/tree/baf14a0c7c783f23eab85b5d25cad7bbe2135e06).
Its [package metadata](https://github.com/bsv-blockchain/bsv-browser/blob/baf14a0c7c783f23eab85b5d25cad7bbe2135e06/package.json)
declares application version 1.6.3. This does not identify the installed phone app.

- The [injected CWI provider](https://github.com/bsv-blockchain/bsv-browser/blob/baf14a0c7c783f23eab85b5d25cad7bbe2135e06/utils/webview/cwiProvider.ts)
  exposes authentication, public keys, signatures, actions, action listing and
  receipt internalisation. It does not expose native monthly spending
  permission request/query/revoke methods.
- The [native method dispatcher](https://github.com/bsv-blockchain/bsv-browser/blob/baf14a0c7c783f23eab85b5d25cad7bbe2135e06/app/index.tsx)
  uses an explicit switch and rejects unsupported methods. Adding a method to
  our JavaScript adapter cannot add a native wallet capability.
- The same dispatcher derives the origin from the verified frame and passes
  it to the wallet permissions manager. Do not supply a privileged origin from
  the charging page or bypass that manager.
- The [document-start script](https://github.com/bsv-blockchain/bsv-browser/blob/baf14a0c7c783f23eab85b5d25cad7bbe2135e06/utils/webview/documentStartScript.ts)
  can answer `getVersion` using wallet version information. Record application
  version from the app's About/settings separately; do not equate an SDK wallet
  version with application version.

## Native grouped request and separate evidence gap

[BRC-73](https://bsv.brc.dev/wallet/0073) and
[BRC-116](https://bsv.brc.dev/wallet/0116) define root-manifest grouped
permissions, including spending authorisation. The absence of a direct CWI
permission-management method does NOT prevent requesting a native budget.
An explicit `waitForAuthentication` call can trigger the wallet permission
manager's manifest flow, including when the wallet is already authenticated.

The opt-in [grouped-authorisation test](grouped-wallet-test.md) publishes
the 30,000 sat request through HA's public manifest extension API. It changes
neither monthly settlement readiness nor the existing payment routes.

The current monthly design requires trusted server-observable native grant
evidence. The reviewed BSV Browser page interface cannot provide that evidence.
No browser capability result, screenshot, login signature or app-signed mandate
is substituted for it. Monthly runtime stays disabled.

Two explicit options exist:

1. **Retain the current design:** use the standard grouped request, then
   implement and review a wallet-side grant evidence
   bridge, including origin/identity binding, freshness, remaining limit,
   native period, fee treatment and revocation. Obtain the wallet maintainer's
   agreement before claiming this will be supported.
2. **Revise the design:** enforce the 30,000 sat gross debit-plus-fee monthly cap
   in HA, with separately signed app consent. Let BSV Browser approve each
   session transaction through its existing `createAction`/`signAction`
   permissions. Native permission state is explicitly unknown and prompts may
   occur. This is not verified native monthly authority and not disconnected
   collection; it needs an approved design and revised readiness semantics.

Neither option is silently enabled by deployment. The ownership adapter,
exactly-once executor and monthly credit/zero routes must be reviewed against
the chosen model before activation. Do not build those against a fake native
grant or assume that the latest signed-in driver owns a physical session.

## Native acceptance checklist

| Check | Required evidence | Status |
|---|---|---|
| App and device | Installed app version, OS, wallet-enabled mode | Pending |
| Read-only API | `isAuthenticated`, `getVersion`, `getNetwork`; no prompts or payments | Probe tested with fixtures; phone pending |
| Origin and identity | Correct original wallet, verified site origin, rejected wrong wallet | Phone pending |
| Existing receipts | Import only existing provider-confirmed credits; preserve original txids | Phone pending for this release |
| Per-session debit | Exact reviewed amount/recipient/fee, no duplicate on reload | Separate real-value approval required |
| Native monthly request | Root manifest and explicit authentication call | Isolated diagnostic implemented; native phone acceptance pending |
| Native monthly evidence | Trusted grant/query/revoke transport | Still unresolved; authentication is not grant evidence |
| Ownership | Exact physical station/connector/session evidence; wrong-driver refusal | Not implemented |
| Restart/uncertainty | Durable operation ID, no replacement signing after timeout | Existing regression coverage; new executor pending |

Do not publish identity keys, addresses, private links, tokens or wallet exports
in a public acceptance record. Record boolean outcomes and redacted evidence.
Read-only diagnostics must never invoke `waitForAuthentication`,
`createSignature`, `createAction`, `signAction` or `internalizeAction`.
