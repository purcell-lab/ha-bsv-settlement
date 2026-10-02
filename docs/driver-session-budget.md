# BSV Browser connection and session-budget consent

This page connects to BSV Browser using BRC-100 and signs a bounded consent
record for one existing, open sensor-proxy session. HA can verify and save the
receipt. It does **not** request wallet spending permission, reserve coins, make
a transaction, start charging or enforce a budget on the charger.

## Wallet target

The [demonstration instructions](https://todriguez.com/cfb/) name BSV Browser for
phones and BSV Desktop for computers. Its
[application](https://todriguez.com/cfb/app.js) uses `WalletClient("auto")` and
`getPublicKey({identityKey:true})`. This page uses the same interface with the
pinned `@bsv/sdk` 2.0.13.

The [BSV permission specification](https://hub.bsvblockchain.org/brc/wallet/0116)
describes application-scoped monthly spending permissions, not an EV
session-specific funds reservation. No spending manifest or standing wallet
permission is requested by this implementation.

## What the driver approves

An HA administrator creates an invitation for an open session. It fixes:

- The session and proxy transaction IDs.
- The operator's public identity and mainnet P2PKH address.
- A total maximum in satoshis **including fees**, and a separate fee ceiling.
- The conversion-rate sensor value, tariff entity IDs and dynamic net-pricing rule.
- A random budget ID and expiry, no more than 24 hours.
- Consent-only scope and the fact that the account covers the entire named
  session, including energy already recorded before consent.

The operator signs the invitation as an ordinary message. The page checks the
signature and that the public key matches the address. This proves possession
of that key, not the operator's real-world identity. The driver must compare
the address against the trusted HA dashboard or another trusted channel.

The driver connects their wallet, reviews the terms, checks both acknowledgement
boxes and selects **Sign session-budget consent**. The wallet signs the invitation
hash, budget ID and driver identity, with explicit `no_spending_authority: true`.
The page checks the signature before offering a downloadable JSON receipt.

No `createAction`, `signAction`, `internalizeAction`, payment broadcast or
standing-spend request is called. Keys never leave the wallets. Public identities,
session IDs and signed consent are personal transaction data: share them only
with the intended driver/operator.

## Operator workflow

After installing the update and an authorised restart:

1. Register `/bsv_settlement/budget-card.js` as a module resource.
2. Add the card below to the settlement dashboard.
3. Wait for an **open** recorded session. Closed sessions cannot receive
   retrospective budget consent.
4. Enter a total satoshi budget, fee ceiling and validity period. Create the
   invitation and copy its JSON to the driver through a trusted channel.
5. The driver opens `/bsv_settlement/driver/index.html` on your HA origin inside
   BSV Browser, pastes the invitation and signs consent.
6. Paste the returned receipt into the card and select **Verify and save driver
   consent**. HA independently verifies the BRC-43 signature against the claimed
   driver identity and the exact stored invitation.
7. Use **Refresh budget status** or **Revoke this budget consent** as needed.

```yaml
type: custom:bsv-budget-card
config_entry_id: MAINNET_OPERATOR_ENTRY
proxy_config_entry_id: SENSOR_RECORDER_ENTRY
proxy_entity: sensor.YOUR_RECORDER_STATUS
rate_entity: sensor.bsv_satoshis_per_aud
grid_options:
  columns: 12
  rows: auto
```

The static driver page requires no HA token or administrator login. There is
no anonymous write endpoint and no automatic receipt upload. Only authenticated
HA administrators can create, accept, inspect or revoke saved budget records.
The page contains no invitation until the driver pastes one. It uses no CDN,
analytics, local storage or server-side driver session. The SDK may probe local
wallet transports after the user selects Connect.

The card can recover a saved invitation when a receipt is imported again.
Repeated creation for the same open session returns the existing unexpired,
unrevoked invitation; changing form values does not silently change its terms.
Revoke it first to replace it. Signed receipt replays for the same identity
are idempotent; a different identity cannot overwrite an accepted receipt.

## Signature and storage contract

Invitations carry a canonical UTF-8 JSON string signed with the embedded
operator key using ECDSA/SHA-256. Driver receipts use BRC-43:

```json
{
  "protocolID": [2, "ha ev session budget"],
  "keyID": "<budget UUID>",
  "counterparty": "anyone",
  "data": "<UTF-8 approval payload bytes>"
}
```

The public counterparty allows signature verification using the driver's
identity public key without granting access to any private key. The HA verifier
derives the matching BRC-42 child public key with public counterparty private
scalar 1 and invoice `2-ha ev session budget-<budget UUID>`.

Consent lives in the existing private, atomic HA wallet store. Status is
`awaiting_driver_consent`, `consent_verified_not_payment_authority`, `revoked`
or `expired`. Restart preserves it. Revocation acts on this HA record only;
there is no wallet spending permission to revoke. Closing or clearing the
page does not revoke a previously imported receipt.

## Boundaries before automatic collection

This is **budget consent capture**, not the complete charging budget gate.
The existing payment-review workflow remains separate, keeps its manual
approvals and is not constrained or authorised by this record. It must not be
presented as budget-enforced. The receipt does not update the existing manual
driver credit address or prove ownership of that address.

Before collection can be automated, implement an exact session-to-consent check
at payment time, fixed-rate reconciliation, fee and cumulative-spend checks,
driver-side policy enforcement, revocation/expiry checks, chain reconciliation,
duplicate prevention and a wallet payment transport. Charging admission or
stopping also requires a separate, explicitly authorised control path. Wallet
availability and prompt behavior must be tested in the actual BSV Browser;
neither background operation nor automatic payment is claimed.

## Validation

Backend tests cover invitation signatures, frozen terms, limits, open-session
rules, driver identity binding, receipt tampering, expiry, revocation, persistence
and no broadcaster calls. JavaScript tests use the official SDK with fictional
wallet keys. Browser QA uses a mocked wallet transport and real SDK message
signatures, not the user's BSV Browser or funds.

Build with `npm ci && npm test && npm run build` in `frontend/driver`.
The bundled page ships in the HACS custom component. Copy `index.html`,
`style.css` and the SDK licence alongside the bundle after changing them.
The approval page is fully local except for the selected wallet transport.
