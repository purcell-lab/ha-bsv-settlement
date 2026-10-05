# Station-first review corrections

These changes address the holistic S1-S5 review in the existing staged stack.
No production activation, native-wallet grant or payment is performed.

## Regression inventory

| Finding | Correction | Evidence |
|---|---|---|
| F1 false readiness | Required session adapters, fresh runtime health, nonzero app/native capacity and unchanged ledger revision; UI additionally requires a recently checked connected signer | `test_monthly_readiness_review.py`; `station.test.js`; restored-login browser case |
| F2 missing monthly history | Validated, identity-filtered retained monthly bindings project through the existing history endpoint, including frozen accounts and accounting attempts | `test_monthly_history_review.py` uses real portal HTTP and a fictional monthly binding |
| F3 hidden terms | Approval moves to Station before displaying and focusing terms | Browser regression from each of the three tabs |
| F4 partial setup dead end | Resume/reconnect actions remain available for incomplete setup; an existing mandate is not re-signed or reset; reviewed optional setup hooks re-read server evidence | `monthly-wallet.test.js`, `station.test.js`, partial-setup browser case |
| F5 false OCPP provenance | History uses the existing linked, freshness-checked OCPP observer; browser does not substitute the recorder running state | Backend discharging/Charging divergence test and frontend stale/missing OCPP cases |
| F6 stale numeric account | Live card and expanded details share the freshness-aware account projection; invalid live net values show Unavailable | Projection tests and stale-meter browser case |

## Evidence boundaries

- The browser QA uses a fictional wallet and fabricated station responses. It is
  a UI regression suite, not native-wallet or end-to-end settlement evidence.
- The backend history test uses the real HTTP endpoint, signed fictional
  authority and internal binding path. No public binding endpoint is added.
- A committed monthly ledger entry means verified wallet spending was recorded;
  it is not rendered as provider-confirmed or wallet receipt acceptance.
- Existing receiving registration and native grant acquisition are not invented.
  Missing production setup adapters explicitly require operator work. The
  resumable interface does not enable monthly operation on its own.
- Server readiness is necessary but not sufficient. The specific closed session
  still needs exact ownership, final accounting and fee-inclusive capacity.

## Browser checks

The extended `qa-station-preview.mjs` checks confirmation visibility and focus
from Station, My charging and History; declining without consent; partial setup;
restored login without a connected wallet; stale amounts in live and expanded
views; mobile/desktop light/dark layout; keyboard tabs; QR text/copy; and no
wallet spend calls. The correction run passed 70 of 70 assertions.
