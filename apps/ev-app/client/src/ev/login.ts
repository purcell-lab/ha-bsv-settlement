// Portal wallet sign-in, ported unchanged from signPortalLogin() in
// frontend/driver/portal-model.js. The integration verifies this exact
// signature in portal.py (BRC-42 child key "2-ev portal login-<nonce>" of the
// identity key, counterparty "anyone"). It is the portal's own challenge, not
// the scaffold's BRC-103 login, because Home Assistant has no BRC-103 verifier.
//
// The challenge is checked before the wallet is asked to sign anything, the
// signature is verified locally before it is sent, and no spending, payment or
// transaction call is made.
import { KeyDeriver, Signature, type WalletInterface, type WalletProtocol } from '@bsv/sdk'
import type { Challenge, LoginProof } from './portal.ts'

export const loginProtocol: WalletProtocol = [2, 'ev portal login']
export const loginScope = 'read_own_sessions_sync_receipts_and_collect_signed_session_budgets'
// Same wallet prompt text as the driver page: the server-side scope of this
// sign-in is shared with that page, so the prompt must describe all of it.
export const loginDescription = 'Sign in for automatic per-session charging payments under your separately signed budget, private history and existing credit receipts. This signature does not create or increase a spending budget.'

export type LoginWallet = Pick<WalletInterface, 'getPublicKey' | 'createSignature'>

// Same canonical JSON as frontend/driver/model.js: object keys sorted, recursively.
export const canonical = (x: unknown): string => JSON.stringify(x, (_, v: unknown) =>
  v !== null && typeof v === 'object' && !Array.isArray(v)
    ? Object.fromEntries(Object.keys(v).sort().map(k => [k, (v as Record<string, unknown>)[k]]))
    : v)
export const bytes = (s: string): number[] => Array.from(new TextEncoder().encode(s))
export const hex = (a: ArrayLike<number>): string => Array.from(a).map(x => x.toString(16).padStart(2, '0')).join('')

const KEYS = 'action,browser_binding,expires_at,issued_at,nonce,origin,scope,version'

export function checkChallenge (challenge: Challenge, origin: string, now = Date.now()): Record<string, unknown> {
  let p: Record<string, unknown>
  try { p = JSON.parse(challenge.payload) as Record<string, unknown> } catch { throw new Error('Invalid portal sign-in challenge.') }
  const n = (v: unknown): number => (typeof v === 'number' ? v : NaN)
  if (p === null || typeof p !== 'object' || Array.isArray(p) ||
    Object.keys(p).sort().join(',') !== KEYS ||
    p.action !== 'sign_in_driver_portal' || p.version !== 1 || p.origin !== origin || p.scope !== loginScope ||
    typeof p.nonce !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(p.nonce) ||
    typeof p.browser_binding !== 'string' || !/^[0-9a-f]{64}$/.test(p.browser_binding) ||
    !Number.isSafeInteger(p.issued_at) || !Number.isSafeInteger(p.expires_at) ||
    n(p.issued_at) * 1000 > now + 30000 || n(p.expires_at) * 1000 <= now ||
    n(p.expires_at) <= n(p.issued_at) || n(p.expires_at) - n(p.issued_at) > 120 ||
    canonical(challenge.protocolID) !== canonical(loginProtocol) || challenge.keyID !== p.nonce ||
    canonical(p) !== challenge.payload) {
    throw new Error('Invalid portal sign-in challenge.')
  }
  return p
}

export async function signPortalLogin (
  wallet: LoginWallet, challenge: Challenge, origin: string, now = Date.now()
): Promise<LoginProof> {
  const p = checkChallenge(challenge, origin, now)
  const nonce = p.nonce as string
  const identity = (await wallet.getPublicKey({ identityKey: true })).publicKey
  if (typeof identity !== 'string' || !/^(02|03)[0-9a-f]{64}$/.test(identity)) throw new Error('Invalid wallet identity.')
  // `description` is not in the SDK's CreateSignatureArgs type, but the driver
  // page sends it and wallets show it in the prompt, so send the same object.
  const args = { protocolID: loginProtocol, keyID: nonce, counterparty: 'anyone' as const,
    data: bytes(challenge.payload), description: loginDescription }
  const { signature } = await wallet.createSignature(args)
  const key = new KeyDeriver('anyone').derivePublicKey(loginProtocol, nonce, identity)
  if (!key.verify(bytes(challenge.payload), Signature.fromDER(signature))) throw new Error('Wallet login signature did not verify.')
  return { identity, payload: challenge.payload, signature: hex(signature) }
}
