// Relay-session context: wraps @bsv/wallet-relay's hook so a single relay client
// (mobile QR / remote wallet) lives above the router. Port/extend from your app as needed.
import { createContext, useContext, type ReactNode } from 'react'
import { useWalletRelayClient } from '@bsv/wallet-relay/react'
import { API_BASE_URL } from './config.js'

type RelayValue = ReturnType<typeof useWalletRelayClient>
const Ctx = createContext<RelayValue | null>(null)

export function WalletConnectionProvider ({ children, apiUrl = API_BASE_URL }: { children: ReactNode, apiUrl?: string }) {
  // apiUrl points at the server running the WalletRelayService (REST /api/session + WS /ws).
  const relay = useWalletRelayClient({ apiUrl, autoCreate: false })
  return <Ctx.Provider value={relay}>{children}</Ctx.Provider>
}

export function useWalletConnection (): RelayValue {
  const v = useContext(Ctx)
  if (v === null) throw new Error('useWalletConnection must be used within WalletConnectionProvider')
  return v
}
