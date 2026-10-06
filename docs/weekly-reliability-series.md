# Weekly-first settlement reliability

Approved series: [#91](https://github.com/purcell-lab/ha-bsv-settlement/issues/91).
All changes remain staged until separate merge/install/restart approval.

## Operating contract

Default: signed 1,000 sat aggregate approval including driver-paid fees, up to
seven days, with one settlement per completed session. Credits do not refill
the allowance. Re-registration, revocation, expiry and exhaustion retain their
existing guards. A signed budget is neither reserved funds nor an offline signer.

Monthly execution and the grouped-authorisation experiment remain disabled.
Existing financial-safety and station-first interface foundations stay installed.
Disabling the request does not revoke an already granted wallet-native permission.

## Sequence and acceptance

| Stage | Deliverable | Required boundary |
|---|---|---|
| R1 | Administrator-only retained-attempt inspection | No network, ledger mutation, raw bytes, scripts, keys, release or retry |
| R2 | Read-only exact outgoing wallet action evidence | No `sendWith`, signing, abort, arbitrary change import or claimed wallet repair |
| R3 | Coherent history and weekly failure matrix | Preserve distinct attempts, stale/conflicting evidence and historical ownership |

R2's native status-repair method remains an explicit research gate. A read-only
wallet query is not synchronisation, and provider confirmation is not wallet
receipt acceptance. Do not present the series as complete native acceptance.

## R1 service

`bsv_settlement.inspect_driver_collection` accepts `config_entry_id` and
`budget_id` through an authenticated HA administrator service context. It
inspects only the exact saved attempt under the coordinator lock.

The response returns bounded input outpoints and output amounts, computed versus
recorded transaction ID, draft match and recipient match. Missing/corrupt data
is explicit. It does not include fee verification because raw transaction bytes
do not establish funding input values, nor does it verify signatures or spend
status. Matching data does not authorise a recovery.

There is no public driver API action, automatic polling or persistent diagnostic
record. Never paste the diagnostic into public GitHub issues: public transaction
references can still link private financial activity.

## Release and next gates

R2 includes a fixture-tested `inspectOutgoingAction` adapter and a documented
[native status-repair gate](outgoing-wallet-evidence.md). It is not wired into
the production entry point. It can report wallet-local evidence but cannot fix
a `nosend` record. That work remains blocked on a verified native method.

Use the full Python, driver and operator suites, reproducible build checks and
HACS validation. Keep native-device, independent-accounting, protected isolated
restore, security/commercial review and physical charger-control acceptance
separate. Sigenergy remains authoritative; OCPP remains shadow-only.

No live action is part of this series' automated tests. Historical uncertain
attempts remain held; original-identity receipt import must not become a resend.
