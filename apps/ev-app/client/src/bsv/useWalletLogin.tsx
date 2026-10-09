// Wallet login: prove identity with the connected wallet, then POST the proof.
import { useCallback } from 'react'
import { useWallet } from './WalletContext.js'
import { createAuthProof } from './auth.js'
import { getServerIdentity, readIdentityKeyResponse } from './serverIdentity.js'
import { apiFetch } from './apiClient.js'

// serverIdentityKey is optional: when omitted it's fetched from GET /api/identity.
export interface UseWalletLoginOptions { serverIdentityKey?: string, loginEndpoint?: string }

export function useWalletLogin (opts: UseWalletLoginOptions = {}) {
  const { wallet, identityKey } = useWallet()
  const login = useCallback(async (): Promise<{ identityKey: string }> => {
    if (wallet === null) throw new Error('connect a wallet first (initializeWallet / relay)')
    const counterparty = requireIdentityKey(opts.serverIdentityKey ?? await getServerIdentity())
    const proof = await createAuthProof(wallet, { counterparty, action: 'login' })
    const res = await apiFetch(opts.loginEndpoint ?? '/api/login', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(proof)
    })
    if (!res.ok) throw new Error('login failed: ' + String(res.status))
    const loggedInIdentity = await readIdentityKeyResponse(res)
    if (identityKey !== null && loggedInIdentity !== identityKey) throw new Error('server returned another wallet identity')
    return { identityKey: loggedInIdentity }
  }, [wallet, opts.serverIdentityKey, opts.loginEndpoint])
  return { login, identityKey, connected: wallet !== null }
}
