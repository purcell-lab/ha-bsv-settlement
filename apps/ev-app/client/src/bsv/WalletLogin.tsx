import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ConnectWallet } from './ConnectWallet.js'
import { useWallet } from './WalletContext.js'
import { createAuthProof } from './auth.js'
import { getServerIdentity, readIdentityKeyResponse, requireIdentityKey } from './serverIdentity.js'
import { apiFetch } from './apiClient.js'

export function WalletLogin () {
  const { wallet, connected, identityKey } = useWallet()
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // --- demo activity log (safe to delete) ---
  const [log, setLog] = useState<string[]>([])
  const step = (m: string): void => setLog(l => [...l, m])
  // --- end demo activity log ---
  const login = async () => {
    setError(null); setResult(null); setLog([])
    if (wallet == null) return
    try {
      step('Fetching the server identity (GET /api/identity)…')
      const counterparty = await getServerIdentity()
      step('Server identity: ' + counterparty.slice(0, 16) + '…')
      step('Signing a login proof with your wallet (action: login)…')
      const proof = await createAuthProof(wallet, { counterparty, action: 'login' })
      step('POST /api/login — sending the proof to the server')
      const res = await apiFetch('/api/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(proof) })
      if (!res.ok) { step('✗ Server rejected the proof (' + String(res.status) + ')'); setError('login failed: ' + String(res.status)); return }
      const loggedInIdentity = await readIdentityKeyResponse(res)
      step('✓ Proof valid — the server trusts this identity')
      if (identityKey !== null && loggedInIdentity !== identityKey) throw new Error('server returned another wallet identity')
      setResult(loggedInIdentity)
    } catch (e) { step('✗ ' + String(e)); setError(String(e)) }
  }
  return (
    <main className="bsv-page">
      <Link className="bsv-back" to="/">← Back to home</Link>
      <h1>Login</h1>
      <p>Prove your identity to the server with your wallet — no password.</p>
      <ConnectWallet />
      {connected && <button className="bsv-btn" onClick={() => { void login() }}>Login with wallet</button>}
      {result != null && <p>✓ Logged in as <code>{result.slice(0, 16)}…</code></p>}
      {error != null && <p className="bsv-err">{error}</p>}
      {/* --- demo activity log (safe to delete) --- */}
      {log.length > 0 && (
        <ol className="bsv-log">
          {log.map((m, i) => <li key={i}>{m}</li>)}
        </ol>
      )}
      {/* --- end demo activity log --- */}
    </main>
  )
}
