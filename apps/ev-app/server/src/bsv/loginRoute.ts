// Express login route. Mount: app.post('/api/login', loginRoute(serverWallet, { sessions }))
// ev-app change from the generated route: after the BRC-103 proof verifies, mint a
// short-lived opaque read-only session token (see http/sessions.ts) so the driver
// page can read its own credits without signing every request.
import type { Request, Response } from 'express'
import { verifyAuthProof } from './auth.js'
import { consumeNonce as defaultConsumeNonce } from './nonceStore.js'
import type { SessionStore } from '../http/sessions.js'

export function loginRoute (
  serverWallet: { verifySignature: (args: any) => Promise<{ valid: boolean }> },
  opts: {
    sessions?: SessionStore
    consumeNonce?: (nonce: string, expiresAt: Date) => boolean | Promise<boolean>
  } = {}
) {
  return async (req: Request, res: Response): Promise<void> => {
    let result: { valid: boolean, identityKey?: string }
    try {
      result = await verifyAuthProof(serverWallet, req.body, { action: 'login' }, opts.consumeNonce ?? defaultConsumeNonce)
    } catch {
      result = { valid: false }
    }
    if (!result.valid || result.identityKey == null) { res.status(401).json({ error: 'invalid proof' }); return }
    if (opts.sessions == null) { res.json({ identityKey: result.identityKey }); return }
    const session = opts.sessions.create(result.identityKey)
    if (session === null) { res.status(503).json({ error: 'too many sessions' }); return }
    res.json({ identityKey: result.identityKey, sessionToken: session.token, expiresAt: session.expiresAt })
  }
}
