// Centralized server configuration, read from the environment.
import { PrivateKey } from '@bsv/sdk'

// Server wallet key. Set SERVER_PRIVATE_KEY for a stable identity; a random key is
// used as a dev fallback (the server's identity then changes on every restart).
const configuredServerKey = process.env.SERVER_PRIVATE_KEY
if (process.env.NODE_ENV === 'production' && configuredServerKey == null) {
  throw new Error('SERVER_PRIVATE_KEY is required in production')
}
const parsedServerKey = configuredServerKey == null
  ? PrivateKey.fromRandom()
  : PrivateKey.fromString(configuredServerKey)
if (configuredServerKey != null && parsedServerKey.toString() !== configuredServerKey) {
  throw new Error('SERVER_PRIVATE_KEY must use its canonical encoding')
}
export const SERVER_PRIVATE_KEY = parsedServerKey.toString()

const portText = process.env.PORT ?? '3000'
if (!/^(?:[1-9]\d{0,4})$/.test(portText)) throw new Error('PORT must be an integer from 1 to 65535')
export const PORT = Number(portText)
if (PORT > 65535) throw new Error('PORT must be an integer from 1 to 65535')

// Browser origin allowed by CORS — your client's dev URL by default. CORS is not auth.
const configuredClientOrigin = process.env.CLIENT_ORIGIN
if (process.env.NODE_ENV === 'production' && configuredClientOrigin == null) {
  throw new Error('CLIENT_ORIGIN is required in production')
}
const parsedClientOrigin = new URL(configuredClientOrigin ?? 'http://localhost:5173')
const localClient = parsedClientOrigin.protocol === 'http:' &&
  (parsedClientOrigin.hostname === 'localhost' || parsedClientOrigin.hostname === '127.0.0.1' || parsedClientOrigin.hostname === '[::1]')
if ((parsedClientOrigin.protocol !== 'https:' && !localClient) || parsedClientOrigin.username !== '' ||
    parsedClientOrigin.password !== '' || parsedClientOrigin.pathname !== '/' ||
    parsedClientOrigin.search !== '' || parsedClientOrigin.hash !== '') {
  throw new Error('CLIENT_ORIGIN must be credential-free HTTPS (or exact HTTP localhost development) origin')
}
export const CLIENT_ORIGIN = parsedClientOrigin.origin

const configuredNetwork = process.env.BSV_NETWORK ?? 'test'
if (configuredNetwork !== 'main' && configuredNetwork !== 'test' && configuredNetwork !== 'ttn') {
  throw new Error('BSV_NETWORK must be main, test, or ttn')
}
export const BSV_NETWORK = configuredNetwork
