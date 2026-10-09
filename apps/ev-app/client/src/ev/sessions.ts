// Read-only projection of the portal's `sessions` result (portal.history()).
// Status wording and the provisional rules are ported from
// frontend/driver/portal-model.js and frontend/ui.js (provisionalSats) so the
// two pages never disagree. Nothing here creates, claims or retries a payment.
import { formatAud, formatCount, formatKwh, formatSats, formatTime, isTxid, shortTxid, toFinite, UNAVAILABLE } from './format.ts'

type Row = Record<string, unknown>

export interface PaymentView {
  id: string
  direction: string
  amount: string
  fee: string
  status: string
  txid: string | null
  txidShort: string
  confirmations: string
  createdAt: string
}

export interface ProvisionalView { amount: string, note: string }

export interface SessionView {
  key: string
  title: string
  opened: string
  ended: string
  importKwh: string
  exportKwh: string
  netAud: string
  status: string
  summary: string
  warning: boolean
  provisional: ProvisionalView | null
  payments: PaymentView[]
}

const obj = (v: unknown): Row => (v !== null && typeof v === 'object' && !Array.isArray(v) ? v as Row : {})
const rows = (v: unknown): Row[] => (Array.isArray(v) ? v.map(obj) : [])
const text = (v: unknown): string => (typeof v === 'string' && v !== '' ? v : '')

export const DEMO_RATE_LABEL = 'demonstration rate, not market FX'

/** Decimal AUD x sat/AUD, positive ROUND_HALF_UP, as frontend/ui.js provisionalSats. Null if anything is invalid. */
export function provisionalSats (netAud: unknown, satsPerAud: unknown): number | null {
  const parts = (v: unknown): { n: bigint, d: bigint } | null => {
    if (typeof v !== 'string' && typeof v !== 'number') return null
    const m = String(v).match(/^-?(\d{1,16})(?:\.(\d{1,16}))?$/)
    return m ? { n: BigInt(m[1] + (m[2] ?? '')), d: 10n ** BigInt((m[2] ?? '').length) } : null
  }
  const amount = parts(netAud)
  const rate = parts(satsPerAud)
  if (rate === null || !(Number(satsPerAud) > 0) || amount === null) return null
  const numerator = amount.n * rate.n
  const denominator = amount.d * rate.d
  const rounded = (numerator * 2n + denominator) / (denominator * 2n)
  return rounded <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(rounded) : null
}

const STATES: Record<string, string> = {
  provider_unconfirmed: 'Awaiting block confirmation',
  submitted: 'Submitted · awaiting confirmation',
  awaiting_driver_payment: 'Awaiting driver payment',
  ready: 'Awaiting wallet approval',
  submission_authorised: 'Wallet signing in progress',
  broadcast_unknown: 'Submission uncertain · do not retry payment',
  waived: 'Waived',
  wallet_attempt_reserved: 'Held for review',
  waiting_for_session_end: 'Session in progress',
  no_operator_credit: 'No operator credit due',
  monthly_reserved: 'Session amount reserved within allowance. No payment confirmed.',
  reservation_released: 'Unused reservation released. Not a payment.',
  wallet_spend_recorded: 'Wallet spending recorded. Provider confirmation is not recorded.'
}

export function transactionStatus (t: Row): string {
  const credit = t.direction === 'operator_to_driver'
  const accepted = t.wallet_receipt_status === 'wallet_reported_accepted'
  if (t.state === 'provider_unconfirmed' && credit && accepted) return 'Wallet received credit · awaiting block confirmation'
  if (t.state === 'provider_confirmed') {
    return credit ? (accepted ? 'Confirmed · wallet acceptance recorded' : 'Confirmed · receipt sync needed') : 'Payment confirmed'
  }
  const state = text(t.state)
  return STATES[state] ?? (state === '' ? 'Review required' : state.replaceAll('_', ' '))
}

export function provisionalSession (s: Row, now = Date.now()): ProvisionalView | null {
  // A provisional account is not a payment. Never replace a submitted/final
  // transaction, or manufacture a number from missing pricing or a stale meter.
  if (s.ended_at !== null || Boolean(s.closure) || rows(s.transactions).some(t =>
    t.txid || Number.isSafeInteger(t.amount_sats) ||
    !['waiting_for_session_end', 'automatic_credit_pending'].includes(String(t.state)))) return null
  const checked = Date.parse(String(s.meter_updated_at))
  const fresh = Number.isFinite(checked) && now - checked >= -5000 && now - checked <= 120000
  const net = toFinite(s.net_amount_aud)
  const sats = fresh && net !== null ? provisionalSats(s.net_amount_aud, s.satoshis_per_aud) : null
  if (sats === null || net === null) {
    return {
      amount: 'Provisional amount unavailable',
      note: !fresh ? 'Waiting for a fresh meter update. No payment requested.'
        : 'Waiting for valid session pricing and its conversion rate. No payment requested.'
    }
  }
  const direction = net < 0 ? 'credit to you' : net > 0 ? 'charge' : 'balance'
  return {
    amount: `Provisional ${direction}: ${sats.toLocaleString('en-AU')} sat`,
    note: `AUD ${Math.abs(net).toFixed(2)} at ${String(s.satoshis_per_aud)} sat/AUD (session conversion rate; ${DEMO_RATE_LABEL}). Network fees excluded. The amount can change until the session closes. Not a payment request.`
  }
}

function summary (s: Row, provisional: ProvisionalView | null): { summary: string, status: string } {
  if (provisional) return { summary: provisional.amount, status: 'Active' }
  const list = rows(s.transactions)
  let payment = 'No payment'
  let status = s.ended_at ? 'Recorded' : 'Active'
  if (text(obj(s.closure).state).includes('waiv')) status = 'Waived'
  if (list.length > 1) { payment = `${list.length} payments`; status = 'See details' }
  if (list.length === 1) {
    const t = list[0]
    const amount = typeof t.amount_sats === 'number' && Number.isSafeInteger(t.amount_sats)
      ? `${t.amount_sats.toLocaleString('en-AU')} sat` : 'amount unavailable'
    const credit = t.direction === 'operator_to_driver'
    const accepted = t.wallet_receipt_status === 'wallet_reported_accepted'
    payment = `${credit ? 'Credit' : 'Pay'} ${amount}`
    status = t.state === 'provider_confirmed'
      ? (credit ? (accepted && Number.isFinite(Date.parse(String(t.wallet_imported_at))) ? 'Received' : 'Receipt due') : 'Confirmed')
      : ({
          provider_unconfirmed: credit && accepted ? 'Received · unconfirmed' : 'Confirming',
          submitted: 'Submitted', broadcast_unknown: 'Review', awaiting_driver_payment: 'Awaiting payment',
          ready: 'Wallet approval', submission_authorised: 'Signing', wallet_attempt_reserved: 'Held',
          waived: 'Waived', waiting_for_session_end: 'Active', no_operator_credit: 'No credit'
        } as Record<string, string>)[text(t.state)] ?? 'Review'
  }
  return { summary: payment, status }
}

export function projectPayment (t: Row): PaymentView {
  return {
    id: text(t.id),
    direction: t.direction === 'operator_to_driver' ? 'Credit to you'
      : t.direction === 'driver_to_operator' ? 'Payment to operator' : UNAVAILABLE,
    amount: formatSats(t.amount_sats),
    fee: formatSats(t.fee_sats),
    status: transactionStatus(t),
    txid: isTxid(t.txid) ? t.txid : null,
    txidShort: isTxid(t.txid) ? shortTxid(t.txid) : 'Not submitted',
    confirmations: formatCount(t.confirmations),
    createdAt: formatTime(t.created_at)
  }
}

export function projectSession (value: unknown, index: number, now = Date.now()): SessionView {
  const s = obj(value)
  const provisional = provisionalSession(s, now)
  const { summary: line, status } = summary(s, provisional)
  const tid = text(s.transaction_id) || (typeof s.transaction_id === 'number' ? String(s.transaction_id) : '')
  return {
    key: text(s.session_key) || `session-${index}`,
    title: tid ? `Charging session ${tid}` : 'Charging session',
    opened: formatTime(s.opened_at),
    ended: s.ended_at === null || s.ended_at === undefined ? 'Not ended' : formatTime(s.ended_at),
    importKwh: formatKwh(s.import_kwh),
    exportKwh: formatKwh(s.export_kwh),
    netAud: formatAud(s.net_amount_aud),
    status,
    summary: line,
    warning: Array.isArray(s.quality_flags) && s.quality_flags.some(f => f !== 'manual_energy_adjustment'),
    provisional,
    payments: rows(s.transactions).map(projectPayment)
  }
}

export function projectSessions (sessions: unknown[], now = Date.now()): SessionView[] {
  return sessions.map((s, i) => projectSession(s, i, now))
}
