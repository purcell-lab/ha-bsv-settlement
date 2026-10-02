# BSV Browser pre-session approval

The driver can approve a budget **before plugging in**. The operator shares a
private link from HA. That link loads the signed terms and current Amber prices.
One page action connects BSV Browser, signs consent and returns the receipt to
HA. The wallet may still display its own identity or signature permission prompts.

This is consent capture. It does **not** reserve funds, grant spending access,
make a payment, start charging or enforce a budget on the charger.

## Default agreement

The operator can edit these defaults before creating the invitation:

| Field | Default |
|---|---|
| Total budget, including network fees | 1,000 sat |
| Maximum network fee | 10 sat, included in the total |
| Validity | 720 minutes (12 hours) |
| Session scope | One future session on the configured recorder |
| Conversion | Current positive `sat/AUD` sensor value, frozen in the agreement |
| Operator | Configurable name and contact, with a generic repository default |

At 100 sat/AUD, 1,000 sat equals AUD10. This is a demonstration conversion,
not market FX. Contact details belong in the private HA dashboard configuration,
not hardcoded in the public repository.

Dynamic settlement uses each interval's import and export prices, including
negative prices. The driver page shows current rates as indicative information,
not fixed session tariffs. Actual automatic settlement remains a later task.

## Driver experience

1. Open the operator's private link inside BSV Browser.
2. Review the operator contact, current Amber prices, maximum spend, fee ceiling,
   conversion rate and expiry.
3. Select **Approve session budget**. No separate Connect button, checkboxes,
   JSON copying or manual receipt return is required for the link flow.
4. Allow any prompts shown by the wallet. HA independently checks the returned
   signature and saves the consent.

If receipt submission fails, the page retains the existing signature and offers
**Retry saving receipt**. It does not sign a second consent. A reload can confirm
that HA accepted it, but the downloaded receipt is only available while held by
the page or from the administrator's saved record.

The page has an advanced manual JSON fallback for earlier invitations. This
fallback requires manual receipt return and has no live-price API access.

## Operator workflow

After installing the update and an authorised HA restart, register
`/bsv_settlement/budget-card.js` as a module resource and configure:

```yaml
type: custom:bsv-budget-card
config_entry_id: MAINNET_OPERATOR_ENTRY
proxy_config_entry_id: SENSOR_RECORDER_ENTRY
proxy_entity: sensor.YOUR_RECORDER_STATUS
rate_entity: sensor.bsv_satoshis_per_aud
operator_name: Charging operator
operator_contact: YOUR_CONTACT_DETAILS
grid_options:
  columns: 12
  rows: auto
```

Create a pre-session approval link and send it only to the intended driver.
It contains a random capability in the URL fragment. The full capability is
returned only once and is not stored in plain text by HA. The saved record
contains only its SHA-256 hash. If the link is lost, revoke the invitation and
create a replacement. Do not publish the link or put it in public issues.

Repeated creation preserves the existing unexpired, unrevoked, unbound
pre-session invitation. Form edits do not silently replace its signed terms.
The card reloads the latest saved budget status when opened, but cannot recover
a lost capability. Refresh budget status after the driver approves.

When the vehicle session opens, select **Bind consent to latest session** and
confirm that this session belongs to the consenting driver. HA requires:

- Verified, unexpired and unrevoked driver consent.
- The original recorder.
- A session that opened after consent was accepted and before expiry.
- No existing binding to another session, and no other budget already bound
  to this session on that recorder.

The signed reservation terms remain unchanged. HA stores the selected session
and proxy transaction IDs in a separate binding record. Automatic assignment of
the next observed session is deliberately disabled: an unrelated driver might
plug in first. Named existing open sessions remain supported through the HA
service, but closed sessions cannot receive retrospective invitations.

## Live Amber prices

The private link reads the configured import and feed-in sensors when opened,
every 30 seconds while the page is open, and immediately before wallet approval.
HA reads them again before accepting a first receipt. The page displays AUD
cents/kWh, including negative feed-in values and an estimated-rate label.

Only numeric `$/kWh` or `AUD/kWh` readings with a valid effective time interval
are accepted. Missing readings, wrong units and expired intervals beyond a
90-second update grace period pause approval. An already saved receipt can be
reconciled idempotently during a later price outage.

Prices are not frozen into a payment quote. The signed agreement fixes the
dynamic calculation rule and conversion rate; its current-price display changes.

## Wallet and signature contract

The [demonstration](https://todriguez.com/cfb/) specifies BSV Browser on phones
and BSV Desktop on computers. Its [application](https://todriguez.com/cfb/app.js)
uses `WalletClient("auto")` and `getPublicKey({identityKey:true})`. This page
uses the same interface, pinned to `@bsv/sdk` 2.0.13.

HA signs canonical UTF-8 invitation terms with ECDSA and a single SHA-256.
The page verifies the signature and that the public key matches the operator
address. This proves key control, not a real-world business identity.

Driver consent signs the exact invitation hash, budget UUID and driver identity
with `no_spending_authority: true`, using:

```json
{
  "protocolID": [2, "ha ev session budget"],
  "keyID": "<budget UUID>",
  "counterparty": "anyone",
  "data": "<UTF-8 approval payload bytes>"
}
```

HA derives the BRC-42 public child using counterparty scalar 1 and invoice
`2-ha ev session budget-<budget UUID>`. Python/TypeScript interoperability is
tested. No private driver key, seed phrase or HA administrator token is requested.

The [wallet permission specification](https://hub.bsvblockchain.org/brc/wallet/0116)
describes application-scoped monthly spending permissions. No standing spending
permission or `createAction` / `signAction` call is made here.

## Narrow driver endpoint

`POST /api/bsv_settlement/driver` accepts JSON with `budget_id`, `token` and
`action`. Only `read` and `approve` are implemented. Approval additionally
requires the correctly signed receipt. The token is scoped to that invitation,
expires with it and stops working when revoked.

There is no public invitation-creation, session-binding, wallet-status or payment
endpoint. The driver response contains only the invitation, current rates,
consent state, consenting public identity and binding. Responses are `no-store`;
the page uses `no-referrer`, omits credentials, and sends no capability in query
parameters. It has no CDN, analytics or browser local storage.

Requests are limited to 20 KB and 120 requests/minute across this endpoint.
Cross-site browser requests are rejected. This is a small supervised PoC,
and capability-link approval is disabled inside embedded frames.
It is not production-grade abuse protection or a guaranteed available public service.
Signed consent can only fill an unclaimed invitation; another driver cannot
overwrite it. HA serialises operations under the existing coordinator lock.

## What remains separate

The existing manual payment-review flow is unchanged. It does not consume or
enforce these budgets. Signed consent does not verify the existing manually
entered driver credit address, nor does it authorise any transfer.

Before automatic collection, connect settlement to the exact bound consent,
check rate, cumulative spend, fees, expiry and revocation, implement driver-side
payment policy and wallet transport, and reconcile chain outcomes without
duplicate payment. Charger admission/stopping requires a separate authorised
control path. Actual BSV Browser prompts and background availability still need
device testing; simulated browser transport tests are not that validation.

## Validation and build

Tests cover signatures, cross-SDK compatibility, pre-session defaults, immutable
terms, capability authentication and redaction, request limits, live-price
freshness, negative prices, expiry, revocation, replay and one-session binding.
Tests use fictional keys and do not contact a broadcaster.

Build with `npm ci && npm test && npm run build` in `frontend/driver`. Copy
`index.html`, `style.css` and the SDK licence alongside the HACS-shipped bundle.
The static driver page and capability endpoint run within the HA integration.
