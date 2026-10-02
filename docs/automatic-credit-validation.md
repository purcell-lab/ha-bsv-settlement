# Automatic-credit validation

Validation uses fictional identities, temporary stores and a recording fake
chain provider. No real payment was made. Native BSV Browser and mainnet miner
acceptance remain untested.

## Coverage

Local validation passed 139 Python/Home Assistant tests and 17 JavaScript SDK
tests. The HACS bundle is built from the checked-in driver source.

- **Server payout with the browser closed:** a fictional negative AUD1.89 session
  at 100 sat/AUD produced one 189 sat driver output and a 10 sat operator-paid fee.
  Reopening the page imported that same transaction with BRC-29 remittance.
- **Duplicate protection:** unknown submission, repeated polling and a reloaded
  wallet store did not create another broadcast. Manual and automatic workflows
  could not own the same session or reuse a signed funding input.
- **Fail-closed cases:** oversized credit, stale or changed account, missing
  destination, late registration, disabled policy, revocation, expiry, unpriced
  energy, quality flags, changed transaction ID and failed durable storage.
- **Wallet import:** SDK tests covered exact output matching, transaction ID,
  remittance, Atomic BEEF and TSC-to-BUMP proof conversion. The fake wallet's
  acceptance is not evidence of acceptance by a production wallet.
- **Interface:** driver page tested at 1280 px and 375 px without horizontal
  overflow. The Payments card shows automatic credits separately from its
  manual exception flow. A stop control disables new automatic signatures.

The one-time operator policy is disabled by default. Publication of this code
does not enable it on a live installation. Installation, restart and policy
activation must be reported separately from these offline checks.
