// Same-origin wire client for the integration's existing driver portal API
// (custom_components/bsv_settlement/portal.py, DriverPortalView). It mirrors
// the api() helper in frontend/driver/portal.js exactly: POST JSON to
// /api/bsv_settlement/portal with credentials 'same-origin'. The browser adds
// the Origin header; the server checks it against Home Assistant's external
// URL and keeps the sign-in in a Secure, HttpOnly, SameSite=Strict cookie that
// this script can never read.
//
// Read-only milestone: only the five actions below exist here. Registration,
// pairing, approval, collection, debit, credit and waiver actions are refused
// before any request is made.

export const PORTAL_URL = '/api/bsv_settlement/portal'
export const PORTAL_ACTIONS = ['prices', 'challenge', 'login', 'sessions', 'logout'] as const
export type PortalAction = typeof PORTAL_ACTIONS[number]
export const REQUEST_TIMEOUT_MS = 20000

export class PortalError extends Error {
  status: number
  code: string | null
  constructor (message: string, status: number, code: string | null = null) {
    super(message)
    this.name = 'PortalError'
    this.status = status
    this.code = code
  }
}

export type FetchLike = (input: string, init: RequestInit) => Promise<Response>

export interface Challenge { payload: string, protocolID: unknown, keyID: string }
export interface LoginProof { identity: string, payload: string, signature: string }
export interface LoginResult { identity: string, expires_in: number, scope: unknown }
export interface SessionsResult {
  identity: string
  sessions: unknown[]
  total: number
  offset: number
  has_more: boolean
  expires_in: number
}

const IDENTITY = /^(02|03)[0-9a-f]{64}$/

function isObject (v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

function isAllowed (action: unknown): action is PortalAction {
  return typeof action === 'string' && (PORTAL_ACTIONS as readonly string[]).includes(action)
}

export async function portalRequest (
  fetchImpl: FetchLike, action: PortalAction, extra: Record<string, unknown> = {}
): Promise<unknown> {
  if (!isAllowed(action)) throw new Error('This app does not use that portal action.')
  if (Object.prototype.hasOwnProperty.call(extra, 'action')) throw new Error('Invalid portal request.')
  const response = await fetchImpl(PORTAL_URL, {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    referrerPolicy: 'no-referrer',
    redirect: 'error',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, ...extra }),
    signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS)
  })
  let data: unknown
  try { data = await response.json() } catch { data = null }
  if (!response.ok) {
    const body = isObject(data) ? data : {}
    throw new PortalError(typeof body.error === 'string' && body.error !== '' ? body.error : 'Portal request failed',
      response.status, typeof body.code === 'string' ? body.code : null)
  }
  if (!isObject(data)) throw new PortalError('Unexpected portal response', response.status)
  return data
}

export function validLifetime (seconds: unknown): seconds is number {
  return typeof seconds === 'number' && Number.isSafeInteger(seconds) && seconds > 0 && seconds <= 900
}

export function createPortal (fetchImpl: FetchLike) {
  return {
    /** Public indicative tariffs. No cookie, wallet or identity involved. */
    prices: () => portalRequest(fetchImpl, 'prices'),

    async challenge (): Promise<Challenge> {
      const r = await portalRequest(fetchImpl, 'challenge') as Record<string, unknown>
      if (typeof r.payload !== 'string' || typeof r.keyID !== 'string') throw new Error('Invalid portal sign-in challenge.')
      return { payload: r.payload, protocolID: r.protocolID, keyID: r.keyID }
    },

    async login (proof: LoginProof): Promise<LoginResult> {
      const r = await portalRequest(fetchImpl, 'login',
        { identity: proof.identity, payload: proof.payload, signature: proof.signature }) as Record<string, unknown>
      if (r.identity !== proof.identity) throw new Error('Sign-in identity mismatch.')
      if (!validLifetime(r.expires_in)) throw new Error('The sign-in lifetime could not be verified.')
      return { identity: r.identity, expires_in: r.expires_in, scope: r.scope }
    },

    async sessions (offset = 0): Promise<SessionsResult> {
      if (!Number.isSafeInteger(offset) || offset < 0 || offset > 100000) throw new Error('Invalid page offset.')
      const r = await portalRequest(fetchImpl, 'sessions', { offset }) as Record<string, unknown>
      if (typeof r.identity !== 'string' || !IDENTITY.test(r.identity) || !Array.isArray(r.sessions) ||
          typeof r.total !== 'number' || !Number.isSafeInteger(r.total) || r.total < 0 ||
          typeof r.expires_in !== 'number' || !Number.isSafeInteger(r.expires_in) || r.expires_in < 0) {
        throw new Error('Unexpected session history response.')
      }
      return {
        identity: r.identity,
        sessions: r.sessions,
        total: r.total,
        offset: typeof r.offset === 'number' ? r.offset : offset,
        has_more: r.has_more === true,
        expires_in: r.expires_in
      }
    },

    logout: () => portalRequest(fetchImpl, 'logout')
  }
}

export type Portal = ReturnType<typeof createPortal>
