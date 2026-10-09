// Wallet acquisition (scaffold wallet-connect capability, reduced to the same
// substrate choice as the driver page in frontend/driver/portal.js): use the
// in-app wallet when BSV Browser injects window.CWI, otherwise let
// @bsv/sdk WalletClient('auto') find Metanet Desktop. No relay, no server.
import { WalletClient } from '@bsv/sdk'

export type Substrate = 'window.CWI' | 'auto'

export function walletSubstrate (win: unknown = globalThis): Substrate {
  const cwi = win !== null && typeof win === 'object' ? (win as { CWI?: unknown }).CWI : undefined
  return cwi ? 'window.CWI' : 'auto'
}

export function acquireWallet (): WalletClient {
  return new WalletClient(walletSubstrate())
}
