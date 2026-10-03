# Seven-day aggregate driver approval

This is an opt-in extension, not a migration of existing consent. A fresh driver
signature authorises multiple future sessions on the configured charger. The
default is 1,000 sat total, including every payment and network fee, for seven
days. It ends sooner when a newer receiving wallet is registered. It does not
reset per session and operator credits do not refill the debit allowance.

## Operator workflow

Choose **Multiple future sessions, up to seven days** in Driver setup. Review the
total, fee ceiling and duration, then create the private invitation. The driver
opens that link, signs the explicit aggregate terms and registers their receiving
wallet. Only sessions opened after registration qualify. Single-session options
retain their current limits and matching rules.

The automatic-credit master policy must already be enabled before this combined
receiving/spending invitation is created. The separate ongoing-credit policy
must be enabled to pay multiple credits. Creating an invitation does not enable
either policy, start the charger, move funds or reserve funds on chain.

## Driver workflow

The private page shows the total, committed and remaining allowance, expiry,
eligible closed sessions and each transaction's settlement status. Keep the
wallet connected and the page open for collection. An operator-signed session
ticket binds each quote to the root approval; it is not a new standalone consent.
The browser verifies that ticket's operator, recipient, tariff, conversion,
session, expiry and limits before asking its wallet for a payment.

Wallet permission prompts remain dependent on wallet implementation and existing
permissions. This is not browser-independent collection and no unattended real
wallet compatibility claim is made.

## Accounting and recovery rules

- A quote reserves the account plus its maximum permitted fee.
- A validated draft fixes the actual fee and releases only unused fee headroom.
- Reserved, signed, uncertain, waived and reorged attempts keep their obligation.
- Restart loads the same ledger; no new allowance or replacement transaction.
- Parent consent is rechecked before quote, claim, signing permit and first
  submission. Expiry, revocation, newer registration or changed terms blocks new
  spending. Submitted transactions remain readable and reconcilable.
- Each session shares the existing driver/operator/manual settlement ownership
  indexes. Meter warnings, historical accounts and conflicting records fail
  closed rather than silently receiving new authority.
- Ongoing credits use separate operator funding, caps and policies. New unsigned
  credits under a version-3 registration stop at expiry or replacement, including
  a session opened earlier but not yet paid. Signed credits still reconcile.

The aggregate ledger is enforced by the trusted operator service under its
coordinator lock. A signature over this policy is not a cryptographic escrow,
UTXO lock, wallet-level spending channel or guarantee of payment. A compromised
operator or modified driver client is outside this PoC's assurance boundary.

## Validation inventory

Automated checks cover fresh version-3 consent, cross-SDK signatures, seven-day
maximum, retained version-2 behavior, post-registration timing, metering gates,
two-session aggregate accounting, actual fees, concurrent requests, lost response,
restart, expiry and replacement before first broadcast, read-only reconciliation,
ticket tampering, standalone-ticket rejection and credit/debit separation.

Interface checks cover single/multi scope selection, duration limits, aggregate
wording, remaining allowance, driver session selection, exact buy/sell labels,
sat display, stale data and no real wallet/payment calls.

Deployment is not authorised by this PR. Before live activation, review the PR,
run CI, back up persisted state, install through the approved HACS workflow and
obtain explicit restart approval. Existing signed invitations stay unchanged.
Use a fresh invitation and supervised wallet test after activation.
