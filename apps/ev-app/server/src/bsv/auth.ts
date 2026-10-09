// Shared, framework-agnostic auth-proof helpers built on @bsv/auth (BRC-103).
// One primitive: sign a proof bound to { action, body? }, verify it on the server.
import { AuthProofClient, AuthProofServer, type AuthProof, type ProofSignerWallet, type RequestBody } from '@bsv/auth'

export type { AuthProof, RequestBody }

export async function createAuthProof (
  wallet: ProofSignerWallet,
  opts: { counterparty: string, action: string, body?: RequestBody }
): Promise<AuthProof> {
  const client = new AuthProofClient()
  return await client.createAuthProof({ wallet, counterparty: opts.counterparty, action: opts.action, body: opts.body })
}

export async function verifyAuthProof (
  serverWallet: { verifySignature: (args: any) => Promise<{ valid: boolean }> },
  proof: AuthProof,
  opts: { action: string, body?: RequestBody },
  consumeNonce: (nonce: string, expiresAt: Date) => boolean | Promise<boolean>
): Promise<{ valid: boolean, identityKey?: string, error?: string }> {
  const server = new AuthProofServer()
  return await server.verifyAuthProof({ wallet: serverWallet, proof, action: opts.action, body: opts.body, consumeNonce })
}
