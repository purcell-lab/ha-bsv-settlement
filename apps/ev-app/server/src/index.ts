import http from 'node:http'
import { ProtoWallet, PrivateKey } from '@bsv/sdk'
import { SERVER_PRIVATE_KEY, PORT, CLIENT_ORIGIN } from './bsv/config.js'
import { WalletRelayService } from '@bsv/wallet-relay'
import { createApp } from './app.js'
import { loadHaConfig } from './ha/config.js'
import { HaStateReader } from './ha/client.js'

// Fail fast on invalid HA configuration. Errors never include the token.
const haConfig = loadHaConfig(process.env)
const ha = new HaStateReader(haConfig)

// Verify-only server wallet. All config (incl. SERVER_PRIVATE_KEY) lives in bsv/config.ts.
const serverWallet = new ProtoWallet(PrivateKey.fromString(SERVER_PRIVATE_KEY))

const trustProxyText = process.env.TRUST_PROXY
if (trustProxyText != null && trustProxyText !== '' && !/^[0-9]$/.test(trustProxyText)) {
  throw new Error('TRUST_PROXY must be a hop count from 0 to 9')
}

// Raw HTTP server so capabilities can attach WebSocket upgrades (e.g. the wallet relay).
const server = http.createServer()
const app = createApp({
  serverWallet,
  clientOrigin: CLIENT_ORIGIN,
  ha,
  entities: haConfig.entities,
  trustProxy: trustProxyText == null || trustProxyText === '' ? false : Number(trustProxyText),
  // mobile-wallet pairing relay (QR) from the generated wallet-connect base
  extend: (a) => { new WalletRelayService({ app: a, server, wallet: serverWallet, origin: CLIENT_ORIGIN }) }
})
server.on('request', app)
server.listen(PORT, () => { console.log(`ev-app server on http://localhost:${PORT} (read-only HA adapter)`) })
