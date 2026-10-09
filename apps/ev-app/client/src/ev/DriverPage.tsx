// Driver page (milestone 1, read-only): station status, wallet sign-in, my credits.
// There are deliberately no payment, approval, collection or charger controls.
import { useCallback, useEffect, useState } from 'react'
import { ConnectWallet } from '../bsv/ConnectWallet.js'
import { useWallet } from '../bsv/WalletContext.js'
import { fetchMyCredits, fetchStation, signIn, type LoginSession, type MyCredits, type StationStatus } from './api.js'
import { describeReason, formatAud, formatCount, formatReading, formatSats, shortTxid, UNAVAILABLE, type Reading } from './format.js'

function Row ({ label, reading, decimals, note }: { label: string, reading: Reading<number | string> | undefined, decimals?: number, note?: string }) {
  const text = formatReading(reading, decimals)
  const reason = reading?.value === null ? describeReason(reading.reason) : ''
  return (
    <div className="ev-row">
      <dt>{label}{note != null && <span className="ev-note"> {note}</span>}</dt>
      <dd className={text === UNAVAILABLE ? 'ev-unavailable' : undefined}>
        {text}
        {reason !== '' && <span className="ev-reason"> ({reason})</span>}
      </dd>
    </div>
  )
}

function StationCard () {
  const [station, setStation] = useState<StationStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try { setStation(await fetchStation()) } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setLoading(false) }
  }, [])
  useEffect(() => {
    let alive = true
    fetchStation()
      .then(value => { if (alive) setStation(value) })
      .catch((e: unknown) => { if (alive) setError(e instanceof Error ? e.message : String(e)) })
    return () => { alive = false }
  }, [])
  const s = station
  return (
    <section className="ev-card" aria-labelledby="station-heading" aria-busy={loading}>
      <div className="ev-card-head">
        <h2 id="station-heading">Station status</h2>
        <button type="button" className="bsv-btn-ghost" onClick={() => { void load() }} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>
      {error !== null && <p className="bsv-err" role="alert">Station status unavailable: {error}</p>}
      {s === null && error === null && <p>Loading…</p>}
      {s !== null && (
        <>
          <dl className="ev-list">
            <Row label="Recorder" reading={s.recorder.status} />
            <Row label="Current session" reading={s.session.transaction_id} />
            <Row label="Energy imported" reading={s.session.import_energy} />
            <Row label="Energy exported" reading={s.session.export_energy} />
            <Row label="Provisional cost" note="(provisional)" reading={s.session.provisional_cost} decimals={2} />
          </dl>
          <p className="ev-caveat">{s.session.provisional_cost.label}</p>
          <h3>Prices</h3>
          <dl className="ev-list">
            <Row label="Import price" reading={s.prices.import} />
            <Row label="Export (feed-in) price" reading={s.prices.export} />
            <Row label="Satoshis per AUD" note="(demonstration rate, not market FX)" reading={s.conversion.satoshis_per_aud} decimals={0} />
          </dl>
          <h3>OCPP shadow</h3>
          <dl className="ev-list">
            <Row label="Shadow lifecycle" note="(shadow only)" reading={s.ocpp_shadow.lifecycle} />
          </dl>
          <p className="ev-caveat">{s.ocpp_shadow.label}</p>
          <p className="ev-meta">Updated {new Date(s.generated_at).toLocaleString('en-AU')}</p>
        </>
      )}
    </section>
  )
}

function CreditsList ({ data }: { data: MyCredits }) {
  if (data.credits === null) {
    return <p className="ev-unavailable" role="status">Credits {UNAVAILABLE} ({describeReason(data.reason)}).</p>
  }
  if (data.credits.length === 0) return <p role="status">No credits for this wallet in the current Home Assistant view.</p>
  return (
    <ul className="ev-credits">
      {data.credits.map((c, i) => (
        <li key={`${c.source}-${c.session_id ?? ''}-${c.txid ?? i}`} className="ev-credit">
          <dl className="ev-list">
            <div className="ev-row"><dt>Session</dt><dd>{c.session_id ?? UNAVAILABLE}</dd></div>
            <div className="ev-row"><dt>State</dt><dd>{c.state ?? UNAVAILABLE}</dd></div>
            <div className="ev-row"><dt>Amount</dt><dd>{formatSats(c.amount_sats)}</dd></div>
            <div className="ev-row"><dt>Network fee</dt><dd>{formatSats(c.fee_sats)}</dd></div>
            <div className="ev-row"><dt>Net amount</dt><dd>{formatAud(c.net_amount_aud)}</dd></div>
            <div className="ev-row"><dt>Transaction</dt><dd><code title={c.txid ?? undefined}>{shortTxid(c.txid)}</code></dd></div>
            <div className="ev-row"><dt>Confirmations</dt><dd>{formatCount(c.confirmations)}</dd></div>
            <div className="ev-row"><dt>Wallet receipt</dt><dd>{c.wallet_receipt_status ?? UNAVAILABLE}</dd></div>
            <div className="ev-row"><dt>Created</dt><dd>{c.created_at ?? UNAVAILABLE}</dd></div>
          </dl>
        </li>
      ))}
    </ul>
  )
}

function MyCreditsCard () {
  const { wallet, connected, identityKey } = useWallet()
  const [session, setSession] = useState<LoginSession | null>(null) // memory only; never persisted
  const [credits, setCredits] = useState<MyCredits | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async (s: LoginSession) => {
    setBusy(true); setError(null)
    try { setCredits(await fetchMyCredits(s)) } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
      if (e instanceof Error && e.message.includes('expired')) { setSession(null); setCredits(null) }
    } finally { setBusy(false) }
  }, [])

  const doSignIn = async () => {
    if (wallet === null) return
    setBusy(true); setError(null)
    try {
      const s = await signIn(wallet, identityKey)
      setSession(s)
      await load(s)
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <section className="ev-card" aria-labelledby="credits-heading" aria-busy={busy}>
      <h2 id="credits-heading">My credits</h2>
      <p className="ev-caveat">
        Signing in proves which wallet you control. It is read-only: it cannot approve, pay or move funds.
      </p>
      {!connected && <ConnectWallet />}
      {connected && session === null && (
        <button type="button" className="bsv-btn" onClick={() => { void doSignIn() }} disabled={busy}>
          {busy ? 'Signing in…' : 'Sign in with wallet'}
        </button>
      )}
      {session !== null && (
        <>
          <p className="ev-meta">Signed in as <code>{session.identityKey.slice(0, 16)}…</code></p>
          <button type="button" className="bsv-btn-ghost" onClick={() => { void load(session) }} disabled={busy}>
            {busy ? 'Loading…' : 'Refresh credits'}
          </button>
        </>
      )}
      {error !== null && <p className="bsv-err" role="alert">{error}</p>}
      {credits !== null && (
        <>
          <CreditsList data={credits} />
          <p className="ev-meta">{credits.note}</p>
        </>
      )}
    </section>
  )
}

export function DriverPage () {
  return (
    <main className="bsv-page ev-page">
      <h1>EV charging</h1>
      <p>Read-only view of the charging station. Home Assistant remains the source of truth for all amounts.</p>
      <StationCard />
      <MyCreditsCard />
    </main>
  )
}
