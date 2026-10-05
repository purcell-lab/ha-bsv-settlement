# BSV Browser grouped-authorisation test

This is an opt-in diagnostic, separate from the charging portal. It requests
a native 30,000 sat monthly allowance but does not enable monthly settlement.
Collection remains per completed charging session, not monthly billing.

## Operator setup

An authenticated HA administrator can call
`bsv_settlement.configure_grouped_wallet_test` with:

```yaml
enabled: true
origin: https://charging.example
confirm_shared_origin: true
```

Replace the example with the exact configured external HTTPS origin, without
a trailing slash. Approval applies to the entire host, not a charging path.
Do not enable on a shared origin without accepting that scope.

Settings persist through HA's storage helper, separately from payment records.
The default is disabled. A conflicting modern or legacy wallet manifest
declaration is never overwritten. Invalid saved settings fail closed without
blocking the existing integration. HA's name, icons and other PWA keys remain.

The root `/manifest.json` gains the `metanet` namespace specified in
[BRC-73](https://bsv.brc.dev/wallet/0073) and
[BRC-116](https://bsv.brc.dev/wallet/0116). The request is 30,000 sat for
monthly charging payments and network fees. No unrelated protocol permissions
are requested by this diagnostic.

## Driver test

1. Open `/bsv_settlement/driver/grouped-test.html` directly in BSV Browser
   in wallet-enabled mode, on the approved external origin.
2. Check the host and amount. Press **Ask BSV Browser** once.
3. Review the native wallet prompt yourself. Check the actual origin,
   spending amount, period and any permission-token cost. Cancel unexpected
   terms. The test never approves a prompt on your behalf.
4. Report the app version and whether the native permission screen shows
   30,000 sat. Redact identity keys, balances and unrelated wallet details.

The page calls only `getNetwork` and, after a deliberate click,
`waitForAuthentication` through injected CWI. It does not fetch identity,
sign a consent statement, create/sign an action, import receipts, enter the
normal portal or invoke settlement APIs. The native wallet may independently
charge a fee to create a permission token; the user must review that prompt.

Returning authenticated does NOT verify the amount. The status deliberately
remains `spending_permission: not_verified`. An offline test of mobile wallet
toolbox 2.14.3 found that an existing smaller spending token can suppress the
new grouped spending request. Do not infer an upgrade from no prompt.

There is one attempt per page load. Timeouts cannot cancel native prompts.
Do not reload/retry until the wallet's outstanding prompt has been inspected.
Cloud browser mocks are not native-device acceptance.

## Disable and safety boundaries

Call the same administrator service with `enabled: false` to withdraw the
owned manifest request. This does NOT revoke an existing wallet allowance.
Use the wallet's own permission controls to review/revoke grants.

The public diagnostic endpoint `/api/bsv_settlement/grouped-test` exposes
only enabled state, approved origin, requested limit and unverified status.
It contains no wallet identity, transaction, token or private link.
The page fails closed for an unavailable/disabled manifest, wrong origin,
embedded frame, missing CWI or non-mainnet wallet.

Monthly authority, ownership binding, per-session execution and native
grant evidence remain separate readiness gates. Do not activate them based
on this test. Existing uncertain payments must not be retried or replaced.
