// Driver page (read-only milestone): public rates, wallet sign-in, the
// driver's own sessions and payments, sign out. Everything goes through the
// integration's existing same-origin portal API. There are deliberately no
// payment, approval, collection, credit, registration or charger controls.
import { useCallback, useEffect, useRef, useState } from 'react'
import { acquireWallet } from '../bsv/walletAcquisition.ts'
import { compactIdentity, UNAVAILABLE } from './format.ts'
import { signPortalLogin } from './login.ts'
import { createPortal, PortalError } from './portal.ts'
import { priceCard } from './prices.ts'
import { projectSessions, type SessionView } from './sessions.ts'

const portal = createPortal((input, init) => fetch(input, init))
const framed = (() => { try { return window.top !== window.self } catch { return true } })()

interface Account { identity: string, expiresAt: number }

function errorText (e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

function Value ({ children }: { children: string }) {
  return <dd className={children === UNAVAILABLE ? 'ev-unavailable' : undefined}>{children}</dd>
}

function PricesCard ({ now }: { now: number }) {
  const [prices, setPrices] = useState<unknown>(null)
  const [checking, setChecking] = useState(true)
  useEffect(() => {
    let alive = true
    // Public rates only: no wallet prompt, cookie or private history involved.
    const refresh = () => {
      portal.prices()
        .then(value => { if (alive) setPrices(value) }, () => { if (alive) setPrices(null) })
        .finally(() => { if (alive) setChecking(false) })
    }
    refresh()
    const timer = setInterval(() => { if (!document.hidden) refresh() }, 60000)
    return () => { alive = false; clearInterval(timer) }
  }, [])
  const buy = priceCard(prices, 'import', now)
  const sell = priceCard(prices, 'export', now)
  const checked = Date.parse(String((prices as { checked_at?: unknown } | null)?.checked_at))
  return (
    <section className="ev-card" aria-labelledby="prices-heading">
      <h2 id="prices-heading">Current rates</h2>
      <dl className="ev-prices">
        <div><dt>Buy / EV charging</dt><Value>{buy.value}</Value></div>
        <div><dt>Sell / V2G export</dt><Value>{sell.value}</Value></div>
      </dl>
      <p className="ev-meta" aria-live="polite">
        {checking && !buy.available && !sell.available ? 'Checking current rates. ' : ''}
        {(buy.available || sell.available) && Number.isFinite(checked)
          ? `Checked ${new Date(checked).toLocaleTimeString('en-AU', { hour: '2-digit', minute: '2-digit' })}. ` : ''}
        Indicative only, not a fixed session quote.
      </p>
    </section>
  )
}

function SessionCard ({ s }: { s: SessionView }) {
  return (
    <li className="ev-session">
      <details>
        <summary>
          <span className="ev-session-title">{s.title}</span>
          <span className="ev-session-line">{s.summary}<span className="ev-badge">{s.status}</span></span>
        </summary>
        <dl className="ev-list">
          <div className="ev-row"><dt>Started</dt><Value>{s.opened}</Value></div>
          <div className="ev-row"><dt>Ended</dt><Value>{s.ended}</Value></div>
          <div className="ev-row"><dt>Energy imported</dt><Value>{s.importKwh}</Value></div>
          <div className="ev-row"><dt>Energy exported</dt><Value>{s.exportKwh}</Value></div>
          <div className="ev-row"><dt>Net session amount</dt><Value>{s.netAud}</Value></div>
        </dl>
        {s.warning && <p className="ev-caveat">This session has a data quality flag. The operator reviews it before settlement.</p>}
        {s.provisional !== null && (
          <div className="ev-provisional">
            <p><strong>{s.provisional.amount}</strong></p>
            <p className="ev-meta">{s.provisional.note}</p>
          </div>
        )}
        {s.payments.length === 0
          ? <p className="ev-meta">No payment recorded for this session.</p>
          : (
            <ul className="ev-payments" aria-label="Payments for this session">
              {s.payments.map((p, i) => (
                <li key={p.id || i}>
                  <dl className="ev-list">
                    <div className="ev-row"><dt>Direction</dt><Value>{p.direction}</Value></div>
                    <div className="ev-row"><dt>Amount</dt><Value>{p.amount}</Value></div>
                    <div className="ev-row"><dt>Network fee</dt><Value>{p.fee}</Value></div>
                    <div className="ev-row"><dt>Status</dt><dd>{p.status}</dd></div>
                    <div className="ev-row"><dt>Confirmations</dt><Value>{p.confirmations}</Value></div>
                    <div className="ev-row"><dt>Recorded</dt><Value>{p.createdAt}</Value></div>
                    <div className="ev-row">
                      <dt>Transaction</dt>
                      <dd>
                        {p.txid !== null
                          ? <a href={`https://api.whatsonchain.com/v1/bsv/main/tx/hash/${p.txid}`} target="_blank" rel="noopener noreferrer"><code title={p.txid}>{p.txidShort}</code></a>
                          : <code>{p.txidShort}</code>}
                      </dd>
                    </div>
                  </dl>
                </li>
              ))}
            </ul>
            )}
      </details>
    </li>
  )
}

function AccountCard ({ now }: { now: number }) {
  const [account, setAccount] = useState<Account | null>(null) // memory only; the cookie is HttpOnly
  const [sessions, setSessions] = useState<unknown[]>([])
  const [total, setTotal] = useState(0)
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const generation = useRef(0)
  const expires = useRef(0) // mirrors account.expiresAt for the expiry watchdog

  const clear = useCallback((note: string) => {
    generation.current++
    expires.current = 0
    setAccount(null); setSessions([]); setTotal(0); setMessage(note)
  }, [])

  const signedIn = useCallback((identity: string, seconds: number) => {
    expires.current = Date.now() + seconds * 1000
    setAccount({ identity, expiresAt: expires.current })
  }, [])

  useEffect(() => {
    // Clear private data when the server-side sign-in lifetime ends, even if a request stalls.
    const timer = setInterval(() => {
      if (expires.current !== 0 && Date.now() >= expires.current) clear('Private access ended. Sign in again to see your sessions.')
    }, 1000)
    return () => clearInterval(timer)
  }, [clear])

  const fail = useCallback((e: unknown) => {
    if (e instanceof PortalError && e.status === 401) clear('Private access ended or the request could not be verified. Sign in again.')
    else setMessage('Action paused: ' + errorText(e))
  }, [clear])

  const load = useCallback(async (identity: string, offset: number) => {
    const before = generation.current
    const result = await portal.sessions(offset)
    if (before !== generation.current) return
    if (result.identity !== identity) {
      clear('Wallet sign-in changed in another tab. Sign in again.')
      return
    }
    signedIn(identity, result.expires_in)
    setSessions(old => (offset === 0 ? result.sessions : [...old, ...result.sessions]))
    setTotal(result.total)
  }, [clear, signedIn])

  const run = async (fn: () => Promise<void>) => {
    if (busy) return
    setBusy(true)
    try { await fn() } catch (e) { fail(e) } finally { setBusy(false) }
  }

  const signIn = () => run(async () => {
    if (framed) { setMessage('Open this page directly in your browser to sign in.'); return }
    const before = generation.current
    setMessage('Approve the sign-in request in your wallet.')
    const proof = await signPortalLogin(acquireWallet(), await portal.challenge(), location.origin)
    const result = await portal.login(proof)
    if (before !== generation.current) throw new Error('Sign-in was interrupted. Please try again.')
    signedIn(result.identity, result.expires_in)
    setMessage('Signed in.')
    await load(result.identity, 0)
  })

  const signOut = () => run(async () => {
    try {
      await portal.logout()
      clear('Signed out. Spending approvals and payments are unchanged.')
    } catch (e) {
      clear('Signed out on this page. The server sign-out could not be confirmed: ' + errorText(e))
    }
  })

  const views = projectSessions(sessions, now)
  const remaining = account === null ? 0 : Math.max(0, Math.ceil((account.expiresAt - now) / 60000))
  return (
    <section className="ev-card" aria-labelledby="account-heading" aria-busy={busy}>
      <h2 id="account-heading">Your sessions and payments</h2>
      {account === null
        ? (
          <>
            <p>Sign in with your BSV wallet to see your own sessions, payments and credits. Your history stays private until you sign in.</p>
            <p className="ev-caveat">Signing in proves which wallet you control. This page cannot approve spending, pay, claim a session or move funds.</p>
            <button type="button" className="ev-btn" onClick={() => { void signIn() }} disabled={busy}>
              {busy ? 'Waiting for wallet…' : 'Sign in with wallet'}
            </button>
            <p className="ev-meta">Works with Metanet Desktop on this computer, or inside the BSV Browser app.</p>
          </>
          )
        : (
          <>
            <p className="ev-meta">
              Signed in as <code title={account.identity}>{compactIdentity(account.identity)}</code>
              {` · private access ends in about ${remaining} min`}
            </p>
            <div className="ev-actions">
              <button type="button" className="ev-btn-ghost" onClick={() => { void run(() => load(account.identity, 0)) }} disabled={busy}>Refresh</button>
              <button type="button" className="ev-btn-ghost" onClick={() => { void signOut() }} disabled={busy}>Sign out</button>
            </div>
            <p className="ev-meta">{`${views.length} of ${total} sessions`}</p>
            {views.length === 0
              ? <p>No sessions are linked to this wallet yet.</p>
              : <ul className="ev-sessions">{views.map(s => <SessionCard key={s.key} s={s} />)}</ul>}
            {views.length < total && (
              <button type="button" className="ev-btn-ghost ev-wide" onClick={() => { void run(() => load(account.identity, sessions.length)) }} disabled={busy}>
                Load more sessions
              </button>
            )}
          </>
          )}
      <p className="ev-status" role="status" aria-live="polite">{message}</p>
    </section>
  )
}

export function DriverPage () {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000) // Expire displayed data even if a fetch stalls.
    return () => clearInterval(timer)
  }, [])
  return (
    <>
      <a className="ev-skip" href="#main">Skip to content</a>
      <header className="ev-header">
        <span className="ev-brand">BSV Settlement</span>
        <span className="ev-pill">Driver · read-only</span>
      </header>
      <main id="main" className="ev-page">
        <h1>EV charging</h1>
        <p className="ev-lead">Rates and your own charging history. Home Assistant remains the source of truth for every amount.</p>
        <PricesCard now={now} />
        <AccountCard now={now} />
        <p className="ev-meta">To approve a budget or settle a session, use the <a href="/bsv_settlement/driver/index.html">driver portal</a>.</p>
      </main>
    </>
  )
}
