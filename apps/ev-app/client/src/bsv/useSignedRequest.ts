// Hook: signedFetch attaches a proof bound to the route + JSON body.
import { useCallback } from 'react'
import { useWallet } from './WalletContext.js'
import { createSignedRequest } from './signedRequest.js'
import { getServerIdentity, requireIdentityKey } from './serverIdentity.js'
import { apiFetch } from './apiClient.js'
import type { RequestBody } from './auth.js'

// serverIdentityKey is optional: when omitted it's fetched from GET /api/identity.
export function useSignedRequest (serverIdentityKey?: string) {
  const { wallet } = useWallet()
  const signedFetch = useCallback(async (url: string, opts: { action: string, body?: RequestBody }): Promise<Response> => {
    if (wallet === null) throw new Error('connect a wallet first')
    const counterparty = requireIdentityKey(serverIdentityKey ?? await getServerIdentity())
    const proof = await createSignedRequest(wallet, { serverIdentityKey: counterparty, action: opts.action, body: opts.body })
    return await apiFetch(url, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ proof, body: opts.body })
    })
  }, [wallet, serverIdentityKey])
  return { signedFetch, connected: wallet !== null }
}
