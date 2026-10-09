// Short-lived opaque login sessions minted after a verified BRC-103 login proof.
// In-memory and single-process, like the generated nonce store: restarting the
// server signs everyone out, and it must be replaced by a shared store before
// running more than one instance. A session grants read access to the
// identity's own credit rows only; it carries no payment or signing authority.
import { randomBytes } from 'node:crypto'

export interface Session { identityKey: string, expiresAt: number }

export class SessionStore {
  private readonly sessions = new Map<string, Session>()
  private readonly ttlMs: number
  private readonly max: number
  private readonly now: () => number

  constructor (opts: { ttlMs?: number, max?: number, now?: () => number } = {}) {
    this.ttlMs = opts.ttlMs ?? 15 * 60_000
    this.max = opts.max ?? 10_000
    this.now = opts.now ?? Date.now
  }

  create (identityKey: string): { token: string, expiresAt: number } | null {
    const t = this.now()
    for (const [k, s] of this.sessions) if (s.expiresAt <= t) this.sessions.delete(k)
    if (this.sessions.size >= this.max) return null
    const token = randomBytes(32).toString('base64url')
    const expiresAt = t + this.ttlMs
    this.sessions.set(token, { identityKey, expiresAt })
    return { token, expiresAt }
  }

  get (token: string): Session | null {
    const session = this.sessions.get(token)
    if (session === undefined) return null
    if (session.expiresAt <= this.now()) { this.sessions.delete(token); return null }
    return session
  }
}
