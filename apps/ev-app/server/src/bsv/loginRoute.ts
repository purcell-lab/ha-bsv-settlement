// Express login route. Mount: app.post('/api/login', loginRoute(serverWallet))
import type { Request, Response } from 'express'
import { verifyAuthProof } from './auth.js'
import { consumeNonce } from './nonceStore.js'

export function loginRoute (serverWallet: { verifySignature: (args: any) => Promise<{ valid: boolean }> }) {
  return async (req: Request, res: Response): Promise<void> => {
    const result = await verifyAuthProof(serverWallet, req.body, { action: 'login' }, consumeNonce)
    if (!result.valid) { res.status(401).json({ error: 'invalid proof' }); return }
    res.json({ identityKey: result.identityKey })
  }
}
