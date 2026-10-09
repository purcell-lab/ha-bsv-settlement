// Connect button + desktop-fail modal (mobile QR / install link). Built on useWallet + the relay session.
import { useWallet } from './WalletContext.js'
import { useWalletConnection } from './WalletConnectionContext.js'

const INSTALL_URL = 'https://desktop.bsvb.tech'

export function ConnectWallet () {
  const { status, identityKey, connect, connectMobile, cancel } = useWallet()
  const relay = useWalletConnection()
  if (status === 'connected') {
    return <div className="bsv-connected">Connected: <code>{identityKey?.slice(0, 16)}…</code></div>
  }
  return (
    <div className="bsv-connect">
      <button onClick={() => { void connect() }} disabled={status !== 'disconnected'}>
        {status === 'connecting' ? 'Connecting…' : 'Connect wallet'}
      </button>
      {(status === 'choosing' || status === 'pairing') && (
        <div className="bsv-modal" role="dialog">
          <div className="bsv-modal-card">
            {status === 'choosing' && (
              <>
                <h3>No desktop wallet found</h3>
                <button className="bsv-btn" onClick={() => { void connectMobile() }}>Connect with a mobile wallet</button>
                <a href={INSTALL_URL} target="_blank" rel="noreferrer">Install a desktop wallet</a>
              </>
            )}
            {status === 'pairing' && (
              <>
                <h3>Scan with your mobile wallet</h3>
                {relay.session?.qrDataUrl != null ? <img src={relay.session.qrDataUrl} alt="Pairing QR" /> : <p>Generating code…</p>}
              </>
            )}
            <button className="bsv-btn-ghost" onClick={cancel}>Cancel</button>
          </div>
        </div>
      )}
    </div>
  )
}
