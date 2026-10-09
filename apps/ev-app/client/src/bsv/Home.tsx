import { ConnectWallet } from './ConnectWallet.js'
import { useWallet } from './WalletContext.js'
import { Link } from 'react-router-dom'

export function Home () {
  const { connected } = useWallet()
  return (
    <main className="bsv-page">
      <h1>BSV app</h1>
      <p>Connect a wallet to get started, then try the installed demos.</p>
      <ConnectWallet />
      {connected && (
        <nav className="bsv-nav">
          <h2 className="bsv-label">Demos</h2>
          <Link to="/login">Wallet login →</Link>
          <Link to="/signed-demo">Signed request demo →</Link>
        </nav>
      )}
    </main>
  )
}
