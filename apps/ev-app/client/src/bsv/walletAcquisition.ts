// Desktop/extension wallet acquisition via @bsv/sdk WalletClient('auto').
import { WalletClient, type WalletInterface } from '@bsv/sdk'

export async function connectDesktopWallet (): Promise<{ wallet: WalletInterface, identityKey: string }> {
  const wallet = new WalletClient('auto')
  const { authenticated } = await wallet.isAuthenticated()
  if (!authenticated) throw new Error('No authenticated desktop wallet found')
  const { publicKey } = await wallet.getPublicKey({ identityKey: true })
  return { wallet, identityKey: publicKey }
}
