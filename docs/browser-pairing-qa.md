# Pairing QA inventory

All automated and preview checks use fictional, unfunded identities. Native
BSV Browser, public HA routing and real settlement remain separate live gates.

| User-visible claim or control | Check |
| --- | --- |
| Connect BSV Browser shows a pairing QR | Click; inspect QR and instructions, verify signed URI in SDK tests |
| Connecting does not approve spending | Existing signed-consent state remains unchanged; relay tests assert no consent mutation |
| Separate spending approval | Existing Approve button remains a separate action after pairing; preview refuses signing |
| Incompatible wallet | Simulate missing getNetwork; show reason, disable approval, offer driver-link fallback |
| Disconnect / use driver link | End socket, clear QR, retain consent state, restore local-wallet path |
| Copy private driver link | Copy complete URL; clipboard-denied fallback gives safe instructions |
| Wallet unavailable or timeout | Stop pending RPC; no automatic transaction retry |
| Old/invalid QR | Reject expired/topic-mismatched/replayed handshake |
| Existing driver binding | Reject a different wallet identity; require mainnet |
| Desktop and mobile layouts | Inspect 1280px and 375px widths, light/dark, waiting and incompatible states |
| Page reload / teardown | Fresh ephemeral pairing needed; no persisted wallet transport secret |
| Public discovery | Relay origin only, no token, budget, driver or account data |

The preview uses the actual production UI and pairing client with simulated
phone messages. Its banner makes clear that the displayed QR is not a live
invitation and no real signature/payment is sent. The production build does
not include the preview harness or its simulated transport.

## Local evidence

- Python/aiohttp: capability checks, authentication, discovery redaction,
  duplicate sockets, invalid frames, revocation, expiry and connection cleanup.
- Node 22: driver SDK 2.0.13 interoperates with mobile SDK 2.8.2 for the pairing
  signature, handshake encryption, RPC response and replay checks.
- Playwright: real clicks through QR waiting, incompatible wallet, disconnect
  to local fallback, compatible pairing and simulated rejection of spending
  approval. No live wallet connection or transaction.
- Visual inspection: desktop QR card and 375px light/dark incompatible states;
  no horizontal overflow or page script errors observed.
- Rendered 375px QR decoded with ZXing to `bsv-wallet://pair` with exactly the
  six pairing fields and no desktop token. Fixed the mobile sticky approval
  bar so it cannot cover the code while waiting for a scan.

Reproduce the fictional preview with `node build-pairing-preview.mjs` from
`frontend/driver`, then serve `preview/` and open `pairing/`.
