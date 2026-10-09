// Bounded in-memory replay protection for the generated development server.
// Replace this with an atomic Redis/DB implementation before horizontally scaling.
const usedNonces = new Map<string, number>()
const MAX_NONCES = 10_000

function pruneExpired (now: number): void {
  for (const [nonce, expiresAt] of usedNonces) {
    if (expiresAt <= now) usedNonces.delete(nonce)
  }
}

export function consumeNonce (nonce: string, expiresAt: Date): boolean {
  const now = Date.now()
  pruneExpired(now)
  if (expiresAt.getTime() <= now || usedNonces.has(nonce) || usedNonces.size >= MAX_NONCES) return false
  usedNonces.set(nonce, expiresAt.getTime())
  return true
}
