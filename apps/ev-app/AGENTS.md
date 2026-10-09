# ev-app — agent guide

Scaffolded by `create-bsv-app` (layout: **monorepo**, network: **test**). BSV capabilities live under `src/bsv`. Re-run `npx create-bsv-app` inside this folder to add more capabilities.

## Install dependencies

### client/

Dependencies are already in `package.json` — just install:

```
cd client && npm i
```

Included:
  @bsv/auth@^0.1.0
  @bsv/sdk@^2.1.0
  @bsv/wallet-relay@^0.2.0
  react@>=18
  react-router-dom@^7.0.0

### server/

Dependencies are already in `package.json` — just install:

```
cd server && npm i
```

Included:
  @bsv/auth@^0.1.0
  @bsv/sdk@^2.1.0
  @bsv/wallet-relay@^0.2.0
  qrcode@^1.5.0
  ws@^8.0.0
  express@^5.0.0

## Wiring

Base files (`main.tsx`, `App.tsx`, `server`) were wired automatically.

## wallet-connect (base)

Connect any BRC-100 wallet — desktop (`@bsv/sdk` `WalletClient('auto')`) or mobile/relay (`@bsv/wallet-relay`) — use it app-wide, and sign/verify the `@bsv/auth` proofs that `wallet-login` and `signed-requests` build on.

### How it works
- Connecting is a small state machine: it tries the desktop/extension wallet first; if none is found it opens a modal to pair a mobile wallet over a relay (QR) or install a desktop one. The connected wallet lives in React context, reachable anywhere via `useWallet()`.
- The **mobile/relay path needs a server**: the base server entry runs `new WalletRelayService({ app, server, wallet: serverWallet, origin })` from `@bsv/wallet-relay`, which registers `GET /api/session` (+ `/:id`, `POST /api/request/:id`) and a `/ws` WebSocket upgrade on the raw HTTP server. The client (`useWalletRelayClient`, pointed at `API_BASE_URL`) creates a session, shows its QR, and pairs over `/ws`. Frontend-only projects (no server) get desktop connect only.
- The proof primitive (`auth.ts`) uses the wallet to sign a message bound to `{ counterparty, action, body? }` and verifies it server-side (BRC-103). That's identity (and request auth) without passwords or shared secrets.
- The server publishes its own identity key at `GET /api/identity`; `getServerIdentity()` accepts that key under the configured API origin's HTTPS/local-development authority. This is convenient endpoint trust, not an independent identity proof. Pin the expected key through the login/signed-request hooks when continuity must survive DNS, certificate, proxy, or deployment changes.
- Every generated API call uses `apiClient.ts`: safe fixed-origin paths, no redirects or credentials, a 10-second whole-response deadline, 1 MiB request/response ceilings, validated unencoded lengths, and strict UTF-8 JSON. Production requires an explicit HTTPS `VITE_API_URL`.

### How it's used
- `auth.ts` (shared) — `createAuthProof(wallet, { counterparty, action, body? })` and `verifyAuthProof(serverWallet, proof, { action, body? }, consumeNonce)`.
- `config.ts` (client) — `API_BASE_URL` (from `VITE_API_URL`, default `http://localhost:3000`); the server base every fetch helper targets.
- `apiClient.ts` (client) — the shared bounded, redirect-free client used by generated identity, login, and signed-request calls.
- `serverIdentity.ts` (client) — `getServerIdentity()` fetches + caches the server's identity key from `GET /api/identity`.
- `walletAcquisition.ts` (client) — `connectDesktopWallet()`.
- `WalletConnectionContext.tsx` / `WalletContext.tsx` / `WalletProviders.tsx` (client) — relay session + wallet state; consume via `useWallet()`.
- `ConnectWallet.tsx` (client) — the connect button + desktop-fail modal.
- New projects (glue on): `src/main.tsx` wraps `<App/>` in `<WalletProviders>`, and a generated `Home.tsx` hub links to each installed capability's page once a wallet connects. With `--no-glue` / add mode: wrap your root in `<WalletProviders>` and build your own home.

### Future integrations
- Persist the connection across reloads (re-probe the desktop wallet / restore the relay session on load).
- Reuse the proof primitive for any action beyond login — bind a proof to any `{ action, body }` (that's exactly what `signed-requests` does).
- Layer identity certificates (BRC-52/103) on top of the raw identity key when you need verified attributes, not just a public key.

## wallet-login

Passwordless login: the connected wallet signs a proof with `action: 'login'`, the server verifies it, and you get a trusted `identityKey` — no password, no shared secret.

### How it works
- The client fetches the server's identity key (`GET /api/identity`) under the configured API origin's HTTPS/local-development authority, uses it as the proof `counterparty`, signs a login proof with the wallet, and POSTs it through the same redirect-free bounded client. Pass `serverIdentityKey` to pin an independently validated key when endpoint trust alone is insufficient.
- The server verifies the signature with its `serverWallet` and consumes a single-use nonce (replay protection), then trusts the `identityKey` the proof was signed by.
- That verified `identityKey` is the whole BSV-specific step. What you do next — issue a session, create a user — is your app's call (see *Future integrations*). The demo page renders each step so you can watch the exchange.

### How it's used
- `WalletLogin.tsx` (client page) — login UI at `/login`; resolves the counterparty via `getServerIdentity()` and shows a step-by-step activity log of the exchange.
- `useWalletLogin.tsx` (client hook) — `const { login } = useWalletLogin()` for a custom UI; pass `{ serverIdentityKey }` to pin a key instead of auto-fetching.
- `loginRoute.ts` (server) — `app.post('/api/login', loginRoute(serverWallet))`; verifies the proof and returns `{ identityKey }`.

### Environment (in `bsv/config.ts`)
- Client: `API_BASE_URL` (default `http://localhost:3000`, override with `VITE_API_URL`).
- Server: `SERVER_PRIVATE_KEY` (the `serverWallet` key; random dev fallback), `PORT`, `CLIENT_ORIGIN` (browser CORS sharing only, not authorization). Production requires explicit stable key and HTTPS origin values.

### Future integrations — turn login into a session
After `/api/login` verifies the proof you hold a trusted `identityKey`; mint a session from it however your app prefers. A minimal JWT example with `jose` (read a secret from env, like `serverWallet` does its key):
```ts
import { SignJWT, jwtVerify } from 'jose'
const secretText = process.env.JWT_SECRET
if (secretText == null || new TextEncoder().encode(secretText).byteLength < 32) throw new Error('JWT_SECRET must contain at least 32 bytes')
const secret = new TextEncoder().encode(secretText)
// in loginRoute, once the proof verifies:
const token = await new SignJWT({ sub: result.identityKey }).setProtectedHeader({ alg: 'HS256' }).setExpirationTime('7d').sign(secret)
res.cookie('session', token, { httpOnly: true, secure: true, sameSite: 'lax' }) // or return it for a bearer header
// guard a route: const { payload } = await jwtVerify(token, secret) // payload.sub === identityKey
```
- Swap the in-memory nonce store in `loginRoute.ts` for Redis/DB in production.
- Persist a user record keyed by `identityKey` on first login.

## signed-requests

Authenticate individual API calls: sign a proof bound to a route (`action`) + request `body`, send it with the request, verify it server-side. Same proof primitive as login, plus a body — one round-trip, no handshake, framework-agnostic.

### How it works
- For each call the client signs a proof over `{ counterparty: serverIdentity, action, body }` and sends `{ proof, body }` to the route.
- The server re-derives the same binding and verifies the signature (and a single-use nonce) before trusting the caller's `identityKey`. Because the proof is bound to the exact action + body, it can't be replayed against another route or with a tampered payload.
- It's stateless — there's no session; every request carries its own authentication. The demo page narrates the steps and shows the server's JSON reply.

### How it's used
- `signedRequest.ts` / `useSignedRequest.ts` (client) — `const { signedFetch } = useSignedRequest()`; `signedFetch('/api/thing', { action: 'thing', body })`. Counterparty auto-discovery trusts the configured API origin; pass `useSignedRequest(serverIdentityKey)` to pin an independently validated key.
- `SignedRequestDemo.tsx` (client page) — interactive demo at `/signed-demo`: connect, send a signed echo to `/api/echo`, watch the steps + JSON result.
- `verifySignedRequest.ts` (server) — `verifySignedRequest(serverWallet, proof, { action, body }, consumeNonce)`; call it from any backend (Express/Next/Fastify) before trusting `identityKey`.

### Future integrations
- Back the `consumeNonce` callback with Redis/DB so replay protection holds across processes and restarts.
- Gate real endpoints: verify, then authorize the `identityKey` (allow-list, roles, ownership checks).
- Bind extra context into the `body` (timestamps, resource ids) for tighter, per-resource authentication.
