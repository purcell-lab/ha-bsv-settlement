# Driver collection failures and guarded recovery

This change fixes a diagnostic and recovery gap, not the underlying cause of
every network failure. A browser can lose connectivity while an operation
continues on the wallet or server. `Failed to fetch` alone does not establish
which operation succeeded, whether a draft exists, or whether money moved.

## Persistent failure reporting

Each collection operation has a named stage: quote, wallet identity/network,
claim signature, reservation request, draft creation, draft validation, signing
permit, wallet recheck, signing, signed-transaction validation and submission.
The first failure remains visible on the driver page while connection-refresh
errors use a separate banner.

After a claim exists, the browser submits a bounded diagnostic with an event
ID, enumerated stage and error class. HA timestamps and saves the first report.
It never accepts arbitrary error text, URLs, signatures, raw transactions or
wallet secrets in this report. The report requires both the driver capability
and the current collection-attempt token. It cannot change payment state or
authorise recovery.

If reporting also fails, the page retains the report in memory and retries
only that metadata upload after connectivity returns. Do not confuse this with
retrying a payment. Until HA acknowledges the diagnostic, closing the page can
still lose it. Old attempts are shown as “failed step not recorded”; the system
does not invent a retrospective cause.

The operator card shows held collections and driver-reported diagnostic text.
These reports are explicitly unverified observations, not proof of payment or
proof that no payment exists.

## Recovery policy

Only an authenticated HA administrator can invoke either recovery service.
Neither service signs or broadcasts a payment. No timeout, page reload,
diagnostic report or missing transaction ID automatically releases a claim.

### Review first

Call `bsv_settlement.prepare_collection_recovery` with `config_entry_id` and
`budget_id`. This read-only response identifies the exact session, quote hash,
claim time, amount, recipient, fee ceiling, expiry and available diagnostic.

Recovery is ineligible unless the state is `wallet_attempt_reserved` and
there is no signing-permit timestamp, draft hash, signed transaction or txid.
Attempts at or beyond signing must reconcile the existing action instead.

### Reconcile external evidence

Before release, the operator must:

- Check the driver's wallet for completed, signed, pending and unsigned actions.
- Check both confirmed and unconfirmed history at the operator's receiving
  address, and reconcile any candidate payment to this account.
- Cancel the old unsigned wallet draft, or obtain evidence that none exists.
- Close all old driver pages and retain a nonsecret reconciliation reference.

A screenshot with no recent activity is insufficient evidence by itself.
Provider history and operator assertions are not independent SPV proof. If
the evidence remains ambiguous, leave the attempt held. This PR does not
automatically perform or attest those external checks.

### Release for driver review

Call `bsv_settlement.recover_driver_collection` with:

```yaml
config_entry_id: "<operator wallet entry>"
budget_id: "<the reviewed spending approval>"
expected_quote_hash: "<exact hash from the read-only review>"
expected_claimed_at: "<exact claim timestamp from the review>"
evidence_reference: "<nonsecret review reference>"
confirm_driver_wallet_checked: true
confirm_recipient_history_checked: true
confirm_unsigned_draft_cancelled_or_absent: true
confirm_old_driver_pages_closed: true
```

Do not set these confirmations merely to bypass the hold. They record an
administrator's completed review, not a claim that software verified it.

The service rechecks current consent, original expiry, frozen account and
manual-review exclusions under the coordinator lock. It compares the exact
quote and claim time, archives the reviewed attempt, invalidates its token,
and signs a new quote generation for the same financial terms. It preserves
the amount, recipient, fee ceiling, conversion and original expiry.

The resulting state is `recovery_ready`, not `ready`. It is excluded from
automatic collection. The driver must open the current private link, select
**Review and resume collection**, connect the approved mainnet wallet and
confirm the amount and fee ceiling. The new claim needs a fresh token and a
fresh wallet signature over the new quote hash. Old tokens and old claim
signatures cannot continue the retired attempt.

No new spending authority, expiry extension or replacement account is created.
The existing draft checks, one-use signing permit, `noSend` signing and
exact-byte reporting/reconciliation remain in force.

## Deployment and compatibility

This PR is independent of the QR-pairing work. It does not require a relay,
new wallet permissions or a different network. It changes the driver page and
operator card, so installation requires backend activation and updated frontend
resources together.

The payment-status and pairing PRs touch some of the same files. Reconcile and
rebuild the combined driver/card bundles before deploying them together; do not
copy one PR's generated bundle over another's source changes.

Recovery history is covered by the existing local ledger checkpoint and retained
up to 100 reviews. At that limit, recovery stops rather than dropping evidence.
This is local consistency protection, not proof against coherent backup rollback.

The live failed attempt was not reset, retried or paid while developing this PR.
Native wallet validation and a separately authorised live recovery remain gates.
