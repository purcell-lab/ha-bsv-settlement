// App-wide wallet state + connect state machine (desktop-first, relay fallback).
import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from 'react'
import type { WalletInterface } from '@bsv/sdk'
import { connectDesktopWallet } from './walletAcquisition.js'
import { useWalletConnection } from './WalletConnectionContext.js'

export type ConnectStatus = 'disconnected' | 'connecting' | 'choosing' | 'pairing' | 'connected'
interface WalletState {
  wallet: WalletInterface | null
  identityKey: string | null
  connected: boolean
  status: ConnectStatus
  connect: () => Promise<void>          // desktop-first; on failure -> 'choosing'
  connectMobile: () => Promise<void>    // relay QR -> 'pairing'
  cancel: () => void
}
const Ctx = createContext<WalletState | null>(null)

export function WalletProvider ({ children }: { children: ReactNode }) {
  const relay = useWalletConnection()
  const [wallet, setWallet] = useState<WalletInterface | null>(null)
  const [identityKey, setIdentityKey] = useState<string | null>(null)
  const [status, setStatus] = useState<ConnectStatus>('disconnected')

  const connect = useCallback(async () => {
    setStatus('connecting')
    try {
      const { wallet, identityKey } = await connectDesktopWallet()
      setWallet(wallet); setIdentityKey(identityKey); setStatus('connected')
    } catch {
      setStatus('choosing')   // no desktop wallet -> show modal
    }
  }, [])

  const connectMobile = useCallback(async () => {
    setStatus('pairing')
    try {
      await relay.createSession()   // shows QR via relay.session.qrDataUrl
    } catch {
      setStatus('choosing')         // relay unavailable -> back to the choice modal
    }
  }, [relay])

  const cancel = useCallback(() => { relay.cancelSession?.(); setStatus('disconnected') }, [relay])

  // bridge: when the relay session connects, adopt its wallet
  useEffect(() => {
    if (relay.session?.status === 'connected' && relay.wallet != null && wallet == null) {
      const w = relay.wallet as unknown as WalletInterface
      w.getPublicKey({ identityKey: true }).then(({ publicKey }) => {
        setWallet(w); setIdentityKey(publicKey); setStatus('connected')
      }).catch(() => {})
    }
  }, [relay.session?.status, relay.wallet, wallet])

  return <Ctx.Provider value={{ wallet, identityKey, connected: wallet !== null, status, connect, connectMobile, cancel }}>{children}</Ctx.Provider>
}
export function useWallet (): WalletState {
  const v = useContext(Ctx)
  if (v === null) throw new Error('useWallet must be used within WalletProvider')
  return v
}
