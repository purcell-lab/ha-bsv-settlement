// Compose the wallet providers in the required order (relay above wallet).
import './bsv.css'
import type { ReactNode } from 'react'
import { WalletConnectionProvider } from './WalletConnectionContext.js'
import { WalletProvider } from './WalletContext.js'

export function WalletProviders ({ children }: { children: ReactNode }) {
  return (
    <WalletConnectionProvider>
      <WalletProvider>{children}</WalletProvider>
    </WalletConnectionProvider>
  )
}
