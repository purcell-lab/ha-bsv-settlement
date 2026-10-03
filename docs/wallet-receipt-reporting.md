# Wallet receipt acceptance reporting

Chain confirmation and wallet receipt acceptance are separate facts. A confirmed
operator payment does not establish that the receiving wallet imported its BRC-29
receipt. Missing acknowledgement is **not** rejection.

## Flow

1. The existing private driver page retrieves the confirmed credit receipt.
2. It verifies the receipt and calls BRC-100 `internalizeAction`.
3. Only after `accepted: true`, it requests a signature under protocol
   `[2, "ev credit receipt"]`, key ID equal to the receiving budget ID, counterparty
   `anyone`. This signature reports an existing receipt, not spending authority.
4. The capability-scoped `acknowledge_credit_receipt` endpoint verifies the
   registered driver's signature and exact payment binding.
5. Home Assistant stores the acknowledgement and its server receipt time.

The signed canonical JSON binds the action and version, network, original
receiving budget, internal credit ID, session and proxy transaction ID, chain TXID,
output index, amount, destination, driver and operator identities, invitation hash
and `accepted: true`. Ongoing credits must resolve through a route belonging to
the original receiving registration, not the latest driver.

## Display and evidence

- `wallet_receipt_status: not_recorded`: no verified acknowledgement saved.
- `wallet_receipt_status: wallet_reported_accepted`: registered wallet signed the
  report and Home Assistant persisted it.
- `wallet_imported_at`: server acknowledgement time, not a claimed wallet-local
  import timestamp. Null for historical records without the new report.

Public status does not disclose the signature or capability token. An authenticated
wallet report is not independent proof of the wallet's database, spendability,
current balance or chain finality. The dashboard retains chain status separately.
Reorgs and provider uncertainty must not be masked by a previous acknowledgement.

## Retry and historical records

Lost responses can retry the same signed acknowledgement idempotently. In the same
open page, an accepted import is cached so reporting retries do not call
`internalizeAction` again. A reload reads persisted acceptance from the server.
If no report was saved before a reload, the same existing receipt may need to be
imported again; this does not create or broadcast a payment.

Signature refusal or reporting failure says wallet import succeeded but reporting
is pending. It never marks a payment failed or schedules a replacement payment.
Storage failure rolls back in-memory acknowledgement so a retry really persists it.
Expired or revoked spending consent may report an already confirmed historical
credit using its original private capability and registered wallet. It does not
renew approval, change a recipient or permit spending.

Older payments are not backfilled as accepted. Reopen their original private page
with the receiving wallet to import and report the existing receipt. Retrieval of
a lost private link is outside this change.

## Deployment and validation

This change requires the matching integration and rebuilt driver/operator bundles.
No version bump, wallet migration, transaction mutation or new on-chain transaction
is required. Tests use fictional keys, synthetic receipts and an isolated provider;
live wallet acceptance must be checked after a separately approved deployment.
