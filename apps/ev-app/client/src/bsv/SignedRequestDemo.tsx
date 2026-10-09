import { useState } from 'react'
import { Link } from 'react-router-dom'
import { ConnectWallet } from './ConnectWallet.js'
import { useSignedRequest } from './useSignedRequest.js'
import { readApiJson } from './apiClient.js'

export function SignedRequestDemo () {
  const { signedFetch, connected } = useSignedRequest()
  const [result, setResult] = useState<unknown>(null)
  const [error, setError] = useState<string | null>(null)
  // --- demo activity log (safe to delete) ---
  const [log, setLog] = useState<string[]>([])
  const step = (m: string): void => setLog(l => [...l, m])
  // --- end demo activity log ---
  const send = async () => {
    setError(null); setResult(null); setLog([])
    try {
      step('Signing a request proof (action: echo) bound to the body…')
      step('POST /api/echo — sending { proof, body }')
      const res = await signedFetch('/api/echo', { action: 'echo', body: { hello: 'world' } })
      if (!res.ok) { step('✗ Server rejected the request (' + String(res.status) + ')'); setError('request failed: ' + String(res.status)); return }
      step('✓ Signature valid — server processed the request')
      setResult(await readApiJson(res))
    } catch (e) { step('✗ ' + String(e)); setError(String(e)) }
  }
  return (
    <main className="bsv-page">
      <Link className="bsv-back" to="/">← Back to home</Link>
      <h1>Signed Request Demo</h1>
      <p>Authenticate a single API call: sign the request with your wallet, verify it server-side.</p>
      <ConnectWallet />
      {connected && <button className="bsv-btn" onClick={() => { void send() }}>Send signed echo</button>}
      {result != null && <pre className="bsv-result">{JSON.stringify(result, null, 2)}</pre>}
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
