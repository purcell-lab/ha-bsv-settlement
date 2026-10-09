// Framework-agnostic verification of a signed request. Works in Express, Next API
// routes, Fastify — it's a plain function. Pass your own single-use nonce store.
import { verifyAuthProof, type AuthProof, type RequestBody } from './auth.js'

export async function verifySignedRequest (
  serverWallet: { verifySignature: (args: any) => Promise<{ valid: boolean }> },
  proof: AuthProof,
  opts: { action: string, body?: RequestBody },
  consumeNonce: (nonce: string, expiresAt: Date) => boolean | Promise<boolean>
): Promise<{ valid: boolean, identityKey?: string, error?: string }> {
  return await verifyAuthProof(serverWallet, proof, { action: opts.action, body: opts.body }, consumeNonce)
}
