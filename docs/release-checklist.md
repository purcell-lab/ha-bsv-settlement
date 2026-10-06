# Release, upgrade and rollback checklist

This checklist covers releasing the HACS integration (`custom_components/bsv_settlement`)
and installing it on a Home Assistant instance. It does **not** authorise any
action by itself:

- No version bump, tag or GitHub release without an explicit instruction from
  the release owner.
- A GitHub merge is not a deployment. Installing or restarting a production
  Home Assistant instance needs separate, explicit authority.
- No real-funds transfer, live charger-control change or mainnet broadcast is
  part of a release check.

## Owner roles

Record the role holder for each release in private release notes or the
deployment PR, not personal contact data in this file. One person may hold
several roles, but validation and deployment sign-off are recorded separately.

| Role | Owns | Signs off |
|---|---|---|
| **Release owner** (repository owner) | Scope, version/tag decision, final go/no-go | Authorisation to tag, release or deploy |
| **HA developer** | Change, tests, changelog, storage-version review | CI green on the exact commit |
| **Validation owner** | Reviews CI evidence, test counts, bundle reproducibility and this checklist | Pre-flight and post-install verification |
| **Deployment operator** | Backup, HACS exact-commit install, authorised restart | Install record and pre-upgrade inventory |
| **Recovery owner** | Rollback decision, backup restore, [protected restore drill](operator-recovery-drill.md) (#4) | Rollback readiness; drill evidence (separate) |
| **Support owner** | Post-release monitoring, issue triage, user communication | Post-release observation window closed |

## Supported versions

| Component | Version | Evidence |
|---|---|---|
| Home Assistant (declared minimum, `hacs.json`) | **2026.9.4** | Required CI: `Python and Home Assistant tests`, `Clean install smoke test (HA 2026.9.4)` |
| Home Assistant (next release) | **2026.10.0b2** pre-release | CI: `Python and Home Assistant tests (HA 2026.10.0b2)`, `Clean install smoke test (HA 2026.10.0b2)` |
| Python | 3.14 | All CI jobs |
| Node (build only) | 22 | Driver and operator bundle jobs |

The pre-release row is forward-compatibility evidence. Replace it with the
stable 2026.10.x release when one is published, in both CI jobs at once. Do not
widen the declared minimum to an untested version. HA 2026.8.3 passed the tests
locally on 6 October 2026, but it is below the declared minimum and is not
supported. The clean-install smoke test refuses to run there.

To add or move a version, edit the `tests` and `clean-install` matrices in
`.github/workflows/validate.yaml`. The `tests` entry for the declared minimum
must keep the check name `Python and Home Assistant tests`, because branch
protection uses that name.

## Pre-flight (before any install)

- [ ] **Exact commit recorded.** Note the full SHA you intend to install and the
      SHA that is currently installed, for rollback.
- [ ] **CI green on that SHA.** All checks pass: Python/HA tests (each matrix
      version), clean-install smoke tests, HACS validation, driver bundle and
      operator cards. Queued, cancelled or runner-infrastructure failures do
      not count as passing evidence ([testing](testing.md)).
- [ ] **Storage review.** If the change alters a `Store` version or
      `test_store_versions_are_pinned_for_rollback_review`, update
      [Downgrade hazards](#downgrade-hazards) before release.
- [ ] **Changelog.** `CHANGELOG.md` has an entry. Change the manifest version only
      when the release owner tells you to.
- [ ] **Backup.** Take a full Home Assistant backup. It includes `.storage`,
      which holds the operator key stores, so keep it protected and off the
      device. If the mock wallet service is in use, also back up its database.
      Record the backup's identifier and time, never its contents.
- [ ] **Pre-upgrade inventory.** For each `bsv_settlement` entry, record its
      backend, its `operator_public_key` (public), any unresolved payments
      (`broadcast_unknown`, `submitted`, `provider_unconfirmed`), active monthly
      bindings and pending collections. Keep the inventory in private evidence.
- [ ] **No payment in flight.** Pause automatic credits if a payment is
      mid-flight. Do not upgrade during an uncertain broadcast unless the
      recovery owner accepts the risk.

## Install (deployment operator, with explicit authority)

1. HACS → BSV Settlement (Mock PoC) → **Redownload**, then select the exact
   recorded commit or release. Do not take the moving default branch.
2. Restart Home Assistant (needs restart authority).
3. Change dashboard resources only when the release notes require it. Update
   module URLs in place and never replace dashboard layouts.

## Post-install verification (validation owner)

- [ ] Every `bsv_settlement` entry is **Loaded**. There are no new
      `bsv_settlement` errors in the log.
- [ ] Each wallet entry still shows the `operator_public_key` from the
      inventory. The key was never regenerated.
- [ ] Every unresolved payment, monthly binding and pending collection from the
      inventory is still present, in the same state.
- [ ] `/bsv_settlement/operator-card.js`, `/bsv_settlement/session-review-card.js`,
      `/bsv_settlement/budget-card.js` and `/bsv_settlement/driver/` return
      200. Installed file hashes match the commit's source.
- [ ] Record the evidence (redacted) on the deployment PR or tracking issue.
      Include the SHA, HA version, backup identifier and checks performed.

## Rollback procedure

1. **Prefer disabling a feature to downgrading code.** For example, remove the
   monthly activation record ([monthly release](monthly-release.md#rollback)).
   State, holds and bindings are kept.
2. **Code rollback (no storage damage).** Check every
   [downgrade hazard](#downgrade-hazards). Then reinstall the previously
   recorded commit via HACS and restart (with authority). Repeat post-install
   verification against the pre-upgrade inventory.
3. **Unreadable store or checkpoint mismatch.** Do not edit `.storage` and do
   not delete key or checkpoint files. Stop settlement. Restore the full HA
   backup from pre-flight (a coherent restore). Before re-enabling,
   reconcile every wallet operation recorded after the backup time
   ([retained ledger checkpoint](retained-ledger-checkpoint.md)).
4. **Never rotate an operator key to recover.** A missing key store means you
   restore the matching backup. Removing a config entry deliberately leaves its
   key store on disk.

## Downgrade hazards

The integration's stores keep their HA storage version across releases, so an
older release can usually read a newer file. The exception is the OCPP shadow
store. An older release reads the file, but it does not enforce rules that were
added later:

| Store / feature | Hazard on downgrade | Action |
|---|---|---|
| Monthly authority (`monthly_authorities` in the mainnet ledger) | Releases before S2 do not enforce monthly exclusion and could collect a monthly-owned account twice | Do not downgrade below S2 while any monthly binding exists. Close and reconcile first |
| `ledger_checkpoint` witness (mainnet) | A release without the checkpoint can write the ledger without the witness. The next upgrade then fails closed | Reconcile after any write made by the older code. Do not assume a blind downgrade/upgrade cycle works |
| `ocpp_shadow` store (version 3) | Older releases that know only versions 1 or 2 refuse the file, so the shadow entry does not load. The data is kept. Observation only, with no payment effect | Accept, or restore the matching backup |
| Unresolved payments and reservations | Older code may not know newer states (e.g. automatic or ongoing credits) and could reuse a reserved input | Resolve or record every unresolved payment first. Recovery owner approval needed |
| `provenance_archive` store and `tariff_provenance_ref` fields (#12) | Older releases do not read the archive and ignore the ledger references. Their new reviews and automatic records carry no frozen provenance. A pre-archive release shows a manual review's reference in its status output (digests and counts only). The archive file is left in place | Accept. Evidence only, with no payment effect. Records made while downgraded show `not_recorded` after upgrade |
| New top-level keys | Settlement and wallet ledgers are re-saved whole, so newer keys survive but are not enforced. The sensor-proxy store rewrites only the keys it knows | Treat newer-feature state as inactive while downgraded |

## Automated evidence and its limits

| Evidence | What it shows |
|---|---|
| `scripts/clean_install_smoke.py`, `tests/test_clean_install.py` and the `Clean install smoke test` CI job | Only the HACS payload is copied into an empty config. An isolated interpreter sets it up via the config flows (embedded testnet, sensor proxy, mainnet refusal, mock form). Entities, 42 actions and frontend paths load, and everything unloads. Runs with no network, using only HA and the manifest requirements |
| `tests/test_upgrade_rollback.py` with `tests/fixtures/upgrade` | Stores written by v0.1.2 and by the earlier main layout load in full HA. Identity, history, proxy observations and an uncertain-broadcast reservation are kept. Store versions and keys stay readable for rollback. A split restore of an older ledger fails closed |
| Python/HA CI matrix | The full regression suite on each supported HA version |

These are offline, fictional-data tests. They are **not**:

- the **protected restore drill (#4)** in [operator-recovery-drill.md](operator-recovery-drill.md),
  which needs a separate recorded execution with an independent witness;
- a HACS download from GitHub, a production install or a restart record;
- proof that a downgrade is safe after newer features have written state.

## Support ownership

- The **Support owner** watches the issue tracker and the deployment PR for one
  observation window after each install, at least one full charging session.
  They triage reports to the HA developer.
- Report security-sensitive findings privately to the repository owner, never
  in a public issue. Never post keys, receiving addresses, HA credentials or
  site identifiers.
- The **Recovery owner** decides on any rollback and on stopping settlement.
  The release owner approves any later re-release.
