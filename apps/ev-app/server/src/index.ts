import http from 'node:http'
import express from 'express'
import cors from 'cors'
import { ProtoWallet, PrivateKey } from '@bsv/sdk'
import { SERVER_PRIVATE_KEY, PORT, CLIENT_ORIGIN } from './bsv/config.js'
import { WalletRelayService } from '@bsv/wallet-relay'
import { loginRoute } from './bsv/loginRoute.js'
import { verifySignedRequest } from './bsv/verifySignedRequest.js'
import { consumeNonce } from './bsv/nonceStore.js'

const app = express()
// CORS controls browser sharing only; it is not authentication or authorization.
app.use(cors({ origin: CLIENT_ORIGIN }))
app.use(express.json({ limit: '64kb', strict: true }))

// Verify-only server wallet. All config (incl. SERVER_PRIVATE_KEY) lives in bsv/config.ts.
const serverWallet = new ProtoWallet(PrivateKey.fromString(SERVER_PRIVATE_KEY))

app.get('/health', (_req, res) => { res.json({ status: 'ok' }) })

// The server's identity public key. Auto-discovery trusts the configured endpoint/TLS
// authority. Pin an independently validated key when deployment continuity matters.
app.get('/api/identity', async (_req, res) => {
  const { publicKey } = await serverWallet.getPublicKey({ identityKey: true })
  res.json({ identityKey: publicKey })
})
app.post('/api/login', loginRoute(serverWallet))
app.post('/api/echo', async (req, res) => { const { proof, body } = req.body; const r = await verifySignedRequest(serverWallet, proof, { action: 'echo', body }, consumeNonce); if (!r.valid) { res.status(401).json({ error: 'invalid proof' }); return }; res.json({ valid: true, identityKey: r.identityKey }) })

// Raw HTTP server so capabilities can attach WebSocket upgrades (e.g. the wallet relay).
const server = http.createServer(app)
new WalletRelayService({ app, server, wallet: serverWallet, origin: CLIENT_ORIGIN }) // mobile-wallet pairing relay (QR)
server.listen(PORT, () => { console.log(`server on http://localhost:${PORT}`) })
