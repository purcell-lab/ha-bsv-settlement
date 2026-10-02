# Session-linked payment requests and operator-credit review

This development flow links an ended sensor-proxy session to an immutable
account review. It supports a manually fulfilled driver payment request and
a separately approved operator credit. It is not an integrated driver wallet,
automatic debit authority, a budget gate or an audited payment product.

## Preconditions

- Activate the updated integration and a `sensor_proxy` recorder entry.
- Select the existing embedded mainnet operator entry; do not create a
  replacement wallet or rotate its key.
- Configure a positive numeric rate sensor with unit `sat/AUD`. A manual
  demonstration rate is allowed but must not be described as market FX.
- Enter the driver's public identity and receiving address in the existing
  native input dialogs. Never provide a private key, WIF or seed.
- Use a closed session with complete non-estimated prices and no unresolved
  counter, availability or missing-data flags.
- Back up and test recovery of the operator wallet before funding it.

The remaining provisional time allocation and DC-meter boundary are explicit
limitations. An administrator must review the account and independently
confirm driver details; that attestation is not cryptographic proof of driver
identity or receiving-address ownership. A session with a counter anomaly is
blocked, even if its rounded estimate looks plausible.

## Freeze and review

`prepare_session_review` is administrator-only and takes:

```yaml
config_entry_id: MAINNET_WALLET_ENTRY
proxy_config_entry_id: SENSOR_RECORDER_ENTRY
session_id: SESSION_ID_FROM_RECORDER
conversion_rate_entity: sensor.bsv_satoshis_per_aud
```

The action stores the closed account, source fingerprint, proxy transaction
ID, public driver binding, exact recipient, conversion-rate value and source
timestamp, direction, rounded satoshi amount, ten-minute expiry and canonical
terms hash. The signed AUD account is rounded to cents first; its magnitude
times sat/AUD is rounded to whole satoshis using half-up rounding.

For example, AUD1.89 at 100 sat/AUD requests 189 satoshis. A negative AUD1.89
account creates a 189-satoshi operator credit instead. A nonzero account
converting below one satoshi is blocked; a genuinely zero rounded account can
be closed explicitly without a transaction. The demonstration amount cap is
100,000 satoshis in either direction.

Preparing is idempotent for the session: subsequent rate-sensor or driver-field
changes do not silently reprice or redirect that review. `frozen_terms` remains
unchanged and hashes to `terms_hash`; later workflow status and attestations
are outside that immutable object.

`approve_session_review` requires the exact review ID, terms hash, recipient
and amount, plus explicit account and driver-detail attestations. It rechecks
the source account and current driver binding. No signing or broadcasting
happens during this action.

## Driver-to-operator request

Approval creates a plain `manual_bsv_payment_request_v1` record containing the
BSV mainnet address, amount, session reference, expiry and terms hash. The
driver must use their own BSV wallet, enter the exact recipient amount and
approve its network fee.

The card provides a locally generated **address-only QR** and a readable JSON
record. The QR does not encode the amount, session reference or spending
authority. No BRC-100/BRC-29 wallet connection, signed payment request, deep-link
interoperability or driver signature exchange is claimed. Never send BTC or
testnet coins.

The request uses the operator's existing address, not a unique per-session
deposit address. Therefore, matching an output does not establish which driver
paid it. The administrator must independently confirm the driver-supplied
transaction reference. A future signed wallet handshake or per-request
receiving scheme is needed to strengthen attribution.

### Verify the reported payment

`verify_session_driver_payment` accepts review ID, transaction ID, output index
and `confirm_driver_payment_reference: true`. It only reads the chain provider:

- Parse raw transaction bytes and check their transaction ID.
- Check the chosen output's exact P2PKH script and satoshi amount.
- Reject an output already allocated to another session.
- Reject known transaction timestamps earlier than the request.
- Record provider-reported confirmations, verification time and manual
  attribution, without claiming independent SPV or payer-identity proof.

Overpayments, underpayments and split outputs are not automatically accepted.
Known older transactions are rejected; absent provider timestamps do not prove
freshness. Only one output can be bound to a review. Changed confirmation
evidence can return it to an unconfirmed state, and a failed recheck marks
previous evidence unavailable rather than leaving an apparently fresh success.

An expired issued request cannot be silently replaced or cancelled because a
driver could still send to its address. Its QR is withheld; late payment evidence
can still be checked and is marked as checked after request expiry. This flag
records the verification time, not a proven blockchain payment-arrival time.
Resolve disputed, partial, duplicate or otherwise unmatched payments manually.

## Operator-to-driver credit

Account approval only enables unsigned preparation. The sequence remains:

1. `prepare_session_credit`: repeat review ID/hash and supply an exact network
   fee of 1 to 1,000 satoshis. The fee range is a demonstration cap, not an
   estimate. The existing wallet checks confirmed funding and its conservative
   change requirement.
2. Review the frozen recipient, recipient amount, network fee, total spend,
   source-backed draft and expiry.
3. `broadcast_session_credit`: explicitly approve real mainnet payment and
   repeat review ID/hash, draft ID, recipient, amount and fee.
4. Use the existing wallet chain-refresh action to reconcile the submitted
   transaction. The review card displays the linked draft state.

The session-linked draft cannot be sent through the generic operator broadcast
action to bypass the review checks. Both account and draft expiry apply, and
the account approval is checked again before signing.

The existing signer preserves exact signed bytes and the input reservation
before sending. Uncertain submission, repeated clicks or a restart do not
create another signature or automatic broadcast. Provider acknowledgement is
not a claim of final payment.

`cancel_session_review` can cancel an unissued review or an unsigned credit;
it cancels a linked unsigned draft first. It cannot cancel an issued driver
request or a signed/submitted credit. A cancelled safe-to-replace review remains
in history, and a subsequent preparation gets a new review ID.

## HA review card

After restart, register `/bsv_settlement/session-review-card.js` as a module
dashboard resource. The bundle is shipped inside the HACS integration and
served locally; no third-party QR service is used.

```yaml
type: custom:bsv-session-review-card
config_entry_id: MAINNET_WALLET_ENTRY
proxy_config_entry_id: SENSOR_RECORDER_ENTRY
proxy_entity: sensor.YOUR_RECORDER_STATUS
wallet_entity: sensor.YOUR_MAINNET_OPERATOR_WALLET_STATUS
rate_entity: sensor.bsv_satoshis_per_aud
grid_options:
  columns: 12
  rows: auto
```

Choose a closed session, freeze it, then review the terms. Account approval
requires two checkboxes; unsigned credit preparation requires an explicitly
entered fee. Mainnet broadcast is a separate control showing exact terms and
requiring a further explicit checkbox. Non-administrators cannot use the
actions; backend checks enforce this independently of the card.

The QR disappears for an expired request or once payment evidence is recorded.
The broadcast control disappears once the draft is no longer unsigned and
valid. `Refresh review` only reads local status; it does not fetch new chain
evidence. Recheck incoming evidence or use mainnet wallet chain refresh when
current provider state is needed.

## Validation and deployment boundary

Backend tests use ephemeral SDK keys and fictional chain responses. They
exercise frozen rates and accounts, changed drivers, account quality gates,
expiry, zero/sub-satoshi accounts, exact output matching, output deduplication,
reorganisation-like confirmation changes, admin-only service access,
restart persistence and uncertain-broadcast idempotency.

Local Chromium tests exercise actual controls with a mocked HA WebSocket
transport. No network requests occur during QR rendering; independent QR
decoding confirms the payload is only the example address. Tests verify that
account approval does not invoke broadcasting, separate consent is required,
and controls are removed after submission or expiry. These are not live wallet
interoperability or mainnet-transfer results.

Code installation does not approve any particular payment or an HA restart.
Leave staged when restart permission has not been granted. Activation must be
followed by configuration and live read-only verification before any separately
approved payment demonstration.
