# Payment event timestamps

New operator credits (including the 5 kWh button) and driver collections record
the following server-clock UTC observations. These fields are additive and do
not change payment authorisation, fees, recipients, holds or retry policy.

| Field | Meaning |
|---|---|
| `broadcast_attempted_at` | First durable submission-intent marker, saved with signed bytes before the provider call. A crash can occur before any network bytes leave. |
| `broadcast_acknowledged_at` | First matching transaction-ID response to that submission. A timeout leaves this missing even if the provider actually received the transaction. |
| `provider_first_confirmed_at` | First recorded successful provider check observing more than zero confirmations. Not block mining time, independent SPV verification or a promise of finality. |
| `wallet_accepted_reported_at` | Server receipt time of the validated, signed wallet acceptance report. Projected from the existing durable `wallet_receipt_ack.reported_at`, not the wallet's internal import time. |

The public driver history shows these in expanded transaction details in UTC.
Operator payment, automatic-credit and collection summaries expose the same
fields for read-only tracing. Incoming credit receipt reporting is distinct
from an outgoing driver payment; debit rows do not display wallet receipt timing.

## Preservation and historical records

- First observations survive repeated checks, retries of acknowledgement,
  restarts and later loss of confirmation. Current state and confirmation count
  still determine eligibility; historical timestamps never grant authority.
- Existing signed receipt reports retain their original recorded time.
- Old broadcast or already-confirmed records with missing event timestamps are
  not backfilled from approval time, `checked_at`, block time or deployment time.
  Pending legacy records can record a new confirmation observation.
- “Not recorded” means no timestamp is available, not proof the event did not
  happen. A late or retried wallet report can be later than actual import.
- No new broadcast, receipt import, history migration or live payment is
  performed to populate timestamps.
