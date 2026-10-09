// Create a signed request: an @bsv/auth proof bound to a route (action) + body.
import type { WalletInterface } from '@bsv/sdk'
import { createAuthProof, type AuthProof, type RequestBody } from './auth.js'

export async function createSignedRequest (
  wallet: WalletInterface,
  opts: { serverIdentityKey: string, action: string, body?: RequestBody }
): Promise<AuthProof> {
  return await createAuthProof(wallet, { counterparty: opts.serverIdentityKey, action: opts.action, body: opts.body })
}
