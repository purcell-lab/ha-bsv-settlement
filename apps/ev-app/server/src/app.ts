// Express app factory. HA engine, new front door: Home Assistant remains the
// single source of financial truth; this app only READS allowlisted HA states.
// There are no payment, signing, broadcast, collection, credit, waiver,
// recovery or charger endpoints in milestone 1.
import express, { type Request, type Response } from 'express'
import cors from 'cors'
import { loginRoute } from './bsv/loginRoute.js'
import { verifySignedRequest } from './bsv/verifySignedRequest.js'
import { consumeNonce as defaultConsumeNonce } from './bsv/nonceStore.js'
import type { AuthProof } from './bsv/auth.js'
import { HaError, type HaStateReader } from './ha/client.js'
import type { EntityMap } from './ha/config.js'
import { readStation, type StationStatus } from './ha/station.js'
import { creditsForIdentity, isIdentityKey } from './ha/credits.js'
import { rateLimit, type RateLimitOptions } from './http/rateLimit.js'
import { SessionStore } from './http/sessions.js'

export interface VerifierWallet {
  verifySignature: (args: any) => Promise<{ valid: boolean }>
  getPublicKey: (args: { identityKey: true }) => Promise<{ publicKey: string }>
}

export interface AppDeps {
  serverWallet: VerifierWallet
  clientOrigin: string
  ha: HaStateReader
  entities: EntityMap
  rateLimit?: RateLimitOptions
  sessions?: SessionStore
  consumeNonce?: (nonce: string, expiresAt: Date) => boolean | Promise<boolean>
  trustProxy?: number | false
  stationCacheMs?: number
  extend?: (app: express.Express) => void
}

export const ME_CREDITS_ACTION = 'me.credits'
export const PROOF_HEADER = 'x-bsv-auth-proof'
const MAX_PROOF_HEADER = 8192

export function createApp (deps: AppDeps): express.Express {
  const app = express()
  app.disable('x-powered-by')
  app.set('trust proxy', deps.trustProxy ?? false)
  const sessions = deps.sessions ?? new SessionStore()
  const consumeNonce = deps.consumeNonce ?? defaultConsumeNonce

  app.use((_req, res, next) => {
    res.set('x-content-type-options', 'nosniff')
    res.set('cache-control', 'no-store')
    res.set('referrer-policy', 'no-referrer')
    next()
  })
  app.use(rateLimit(deps.rateLimit ?? { capacity: 30, refillPerSecond: 1 }))
  // CORS controls browser sharing only; it is not authentication or authorization.
  app.use(cors({
    origin: deps.clientOrigin,
    methods: ['GET', 'POST'],
    allowedHeaders: ['content-type', 'authorization', PROOF_HEADER]
  }))
  app.use(express.json({ limit: '64kb', strict: true }))

  app.get('/health', (_req, res) => { res.json({ status: 'ok' }) })

  // The server's identity public key. Auto-discovery trusts the configured endpoint/TLS
  // authority. Pin an independently validated key when deployment continuity matters.
  app.get('/api/identity', async (_req, res) => {
    const { publicKey } = await deps.serverWallet.getPublicKey({ identityKey: true })
    res.json({ identityKey: publicKey })
  })
  app.post('/api/login', loginRoute(deps.serverWallet, { sessions, consumeNonce }))

  let cached: { at: number, value: StationStatus } | null = null
  let inflight: Promise<StationStatus> | null = null
  const cacheMs = deps.stationCacheMs ?? 2000
  app.get('/api/station', async (_req, res) => {
    if (cached === null || Date.now() - cached.at >= cacheMs) {
      inflight ??= readStation(deps.ha, deps.entities).finally(() => { inflight = null })
      const value = await inflight
      cached = { at: Date.now(), value }
    }
    res.json(cached.value)
  })

  async function verifiedIdentity (req: Request): Promise<string | null> {
    const authorization = req.get('authorization')
    if (authorization !== undefined) {
      const match = /^Bearer ([A-Za-z0-9_-]{43})$/.exec(authorization)
      if (match === null) return null
      return sessions.get(match[1])?.identityKey ?? null
    }
    const header = req.get(PROOF_HEADER)
    if (header === undefined || header.length > MAX_PROOF_HEADER || !/^[A-Za-z0-9+/=]+$/.test(header)) return null
    let proof: AuthProof
    try {
      proof = JSON.parse(Buffer.from(header, 'base64').toString('utf8')) as AuthProof
    } catch {
      return null
    }
    const result = await verifySignedRequest(deps.serverWallet, proof, { action: ME_CREDITS_ACTION }, consumeNonce)
    return result.valid && isIdentityKey(result.identityKey) ? result.identityKey : null
  }

  app.get('/api/me/credits', async (req: Request, res: Response) => {
    let identityKey: string | null
    try { identityKey = await verifiedIdentity(req) } catch { identityKey = null }
    if (identityKey === null) { res.status(401).json({ error: 'wallet sign-in required' }); return }
    const base = {
      identity_key: identityKey,
      generated_at: new Date().toISOString(),
      note: 'Read-only view of the rows Home Assistant currently shows on its operator wallet sensor. ' +
        'Older resolved rows may be outside that display window.'
    }
    try {
      const state = await deps.ha.getState(deps.entities.walletStatus)
      const s = state.state.trim().toLowerCase()
      if (s === 'unknown' || s === 'unavailable' || s === '') {
        res.json({ ...base, credits: null, reason: s === '' ? 'empty' : s })
        return
      }
      res.json({ ...base, credits: creditsForIdentity(state.attributes, identityKey), reason: null })
    } catch (error) {
      res.json({ ...base, credits: null, reason: `ha_${error instanceof HaError ? error.code : 'network'}` })
    }
  })

  // Let the caller mount extra routes (the generated wallet relay) before the fallbacks.
  deps.extend?.(app)
  app.use((_req: Request, res: Response) => { res.status(404).json({ error: 'not found' }) })
  // Generic error body: never echo internal error text (it could carry upstream details).
  app.use((_err: unknown, _req: Request, res: Response, _next: express.NextFunction) => {
    if (!res.headersSent) res.status(500).json({ error: 'internal error' })
  })
  return app
}
