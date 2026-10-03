# Connect BSV Browser

This change adds a short-lived **Connect BSV Browser** QR to the private driver
page. It pairs a phone wallet with a desktop driver page. It does not approve a
session, reserve money, initiate a payment or change the operator-credit policy.
The existing private link inside BSV Browser remains the fallback.

## Operator and driver flow

1. Open the existing private driver invitation on the desktop.
2. Select **Connect BSV Browser**. The QR is available for two minutes.
3. On the phone, select **Connect to app**, scan, check the displayed app domain
   and review the wallet's permissions before approving the connection.
4. The page checks the wallet's advertised methods. A compatible wallet must
   expose `getNetwork`; the mainnet result and wallet identity are checked before
   the existing session-approval operation.
5. Select **Approve spending up to … sat** separately. The existing signed
   session terms, registration, fee limits, one-use collection claim, draft
   inspection and receipt verification still apply.
6. Keep both devices and the driver page open for a debit. Pairing does not
   guarantee collection when the phone is closed, suspended or offline.
7. **Disconnect / use driver link** ends the relay and restores the local-wallet
   path. It does not revoke saved spending consent. Reconnect requires a new QR.

## Compatibility gate

The inspected BSV Browser dependency `@bsv/expo-wallet-toolbox` 0.11.0 exposes a
QR connection handler but its `IMPLEMENTED_METHODS` list omits `getNetwork`.
That build can pair but cannot safely complete this application's settlement
flow over the relay. The page displays this limitation and blocks session
signing through that transport; it does not fabricate a mainnet result or infer
network from a public identity. Use the local driver link until a wallet build
exposes the required method ([package](https://www.npmjs.com/package/@bsv/expo-wallet-toolbox/v/0.11.0),
[BSV Browser dependency](https://github.com/bsv-blockchain/bsv-browser/blob/master/package.json)).

The integration implements the published wallet-relay 0.5.x QR transcript and
encrypted wallet RPC framing. Mobile currently discovers `GET /api/session/:id`
and connects to `/ws?topic=…&role=mobile` at the returned relay origin, so these
root routes are intentional, not arbitrary application API aliases
([upstream integration and wire protocol](https://github.com/bsv-blockchain/ts-stack/tree/main/packages/wallet/ts-wallet-relay),
[mobile pairing route](https://github.com/bsv-blockchain/bsv-browser/blob/master/app/pair.tsx)).

## Standalone HA architecture

- **No new service:** HACS contains the Python/aiohttp relay and bundled browser
  JavaScript. No Node runtime, external wallet relay, reverse proxy or relay
  credentials are required.
- **Reachability:** the configured HA external URL must be public HTTPS, at its
  root, with a certificate the phone trusts. Open the driver page at that same
  origin. The phone must reach `/api/session/:id` and `/ws` without an
  interactive HA login or an intermediary login challenge. Existing gateway
  policy and root-route conflicts must be checked before activation.
- **Blind relay:** HA forwards bounded ciphertext. A fresh, unfunded
  `ProtoWallet` in the desktop page signs the pairing QR and encrypts/decrypts
  RPC. It is not the operator's funded wallet. Driver private keys remain on the
  phone.
- **Memory-only:** pairing keys, desktop tokens, mobile connection and RPC
  requests are never put in storage, sensor attributes, URLs, QR tokens,
  repository files or logs. The QR contains only the signed public pairing
  parameters, not the desktop token or driver invitation capability.
- **Authentication:** creating/cancelling a relay requires the existing driver
  capability. Desktop WebSocket auth uses a token-bearing subprotocol plus the
  exact configured origin, never a query-string secret. Mobile access uses
  possession of the short-lived QR; the first socket is exclusive. The desktop
  verifies its encrypted handshake and pins its wallet identity.
- **Not a new identity guarantee:** anyone who copies a live QR can attempt to
  pair first. Keep it private and check the app on the phone. An already
  registered session additionally requires that registered wallet's identity.
- **Bounds:** eight relay sessions, twelve creations/minute, 120 messages/minute
  per session, one socket per role, one in-flight frontend RPC and 1 MB wire
  frames. A too-large transaction fails closed; use the original browser flow.
- **Lifetime:** two-minute QR; at most 30 minutes after handshake. Disconnect,
  reload, HA restart/unload or a rejected frame ends the connection. Revocation
  and invitation expiry are checked on connection and every relayed message.
  Idle sockets may be closed earlier by transport timeouts.
- **No implicit retries:** RPC timeout or loss of connection rejects pending
  calls. A reserved or uncertain payment must use existing reconciliation,
  not a new collection attempt. Closing a socket cannot undo a wallet action
  already received by the phone.

The phone's app-level permission policy is separate from our signed spending
mandate. Drivers must review the wallet's connection permissions and limits;
pairing is not a claim that the wallet enforces our session-specific budget.

## Verification and activation gates

Automated tests cover the real aiohttp routes, origin/token checks, discovery
redaction, duplicate peers, revocation, QR expiry, bounded frames, teardown and
creation limits. JavaScript tests use the bundled SDK 2.0.13 against mobile SDK
2.8.2 to verify the signed QR, two-way encryption, acknowledgement, RPC replay
checks, rejection, timeout, identity, network and missing-method behaviour.
Synthetic keys are unfunded. None of these tests transfers real funds.

Before production approval:

- Inspect desktop/mobile layouts, light/dark states, QR waiting, disconnect and
  incompatible-wallet fallback. Confirm scanning does not sign session consent.
- Check route availability and the public HTTPS origin on the actual HA host.
- Scan with the installed BSV Browser version and record its capabilities.
- With a compatible version, perform a separately authorised, bounded mainnet
  collection/credit test. Test wallet rejection, suspension and reconnect.
- Keep independent security review and browser-independent payment assurance
  open. A passing crypto fixture is not a successful native-device trial.

Merging, installation and HA restart are separate operator-authorised actions.
This PR does not deploy itself.
