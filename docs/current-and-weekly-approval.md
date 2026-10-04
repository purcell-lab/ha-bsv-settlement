# Current session plus weekly automatic settlement

A fresh driver signature can cover one explicitly named current recorder session
and future sessions. The current session may still be open or may have just ended.
One shared total, normally 1,000 sat including all driver-paid network fees, applies
to the entire approval. The approval lasts up to seven days and ends earlier when
a newer driver registers.

## Operator workflow

- **Default:** Create a multi-session invitation. The operator form defaults to
  seven days; the single-session alternatives remain explicit options.
- **Current session:** Select “Include the current recorder session”. The backend
  verifies its ID against the recorder's latest session. A different historical
  account cannot be substituted.
- **Completed account:** The invitation freezes its energy, rounded AUD account,
  converted amount and warnings. The driver sees these before signing. Subsequent
  account changes block collection rather than silently changing the bill.
- **Open account:** The invitation names the exact session and its opening time.
  It includes energy already recorded; the final priced account is checked at close.
- **Private invitation:** Current-account invitations are never offered through
  public registration. Share the private link only with the intended driver.
- **Driver action:** Sign once and register the receiving wallet. The driver page
  shows the approval ID, session ID and proxy transaction ID outside collapsed
  details. An unbound one-session approval says “Not assigned”, not a fabricated ID.

API example, using fictional identifiers:

```yaml
action: bsv_settlement.create_session_budget
data:
  config_entry_id: fictional-operator-entry
  proxy_config_entry_id: fictional-recorder-entry
  conversion_rate_entity: sensor.example_satoshis_per_aud
  multi_session: true
  initial_session_id: fictional-current-session
  valid_minutes: 10080
  max_total_sats: 1000
  max_fee_sats: 1000
```

Omit `initial_session_id` for future-only approval. Creating an invitation does not
authorise a payment. Existing signatures cannot acquire the new scope.

## Automatic does not mean unrestricted

- **Driver charges:** The approved driver page and wallet must be available.
  Wallet-native permission prompts can still occur. No offline collection guarantee
  or funds reservation is introduced.
- **Operator credits:** Existing automatic-credit authority, funding checks and
  recipient routing remain unchanged. Including an existing session does not
  redirect an already assigned credit to a newly registered wallet.
- **Total allowance:** Current and future charges draw from the same allowance.
  Credits do not refill it. Fees are calculated through the existing payment path,
  not set to the maximum shown in the invitation.
- **Safety holds:** Missing metering baselines, unpriced energy, conflicting
  settlement ownership, uncertain wallet actions and changed closed accounts still
  block settlement. Routine provisional allocation warnings remain non-blocking.
- **Previous approval:** A weekly approval superseded by a later verified driver
  registration no longer blocks creation of a fresh invitation. Its original
  signature, spending reservations, receipts and historical records are preserved.
  An active weekly approval cannot be silently replaced or reset.

No change to charger control, OCPP recording, private keys or mainnet broadcast
policy is part of this update.
