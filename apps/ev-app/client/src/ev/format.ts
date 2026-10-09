// Display formatters. Rule: a missing or unknown value is shown as
// "Unavailable", NEVER as 0. Pure module with no imports so it can be
// unit-tested directly with node --test.

export const UNAVAILABLE = 'Unavailable'

const DECIMAL = /^-?\d{1,16}(?:\.\d{1,16})?$/

/** A finite number, or a plain decimal string as the portal stores amounts. Anything else is null. */
export function toFinite (value: unknown): number | null {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (typeof value === 'string' && DECIMAL.test(value)) {
    const n = Number(value)
    return Number.isFinite(n) ? n : null
  }
  return null
}

export function formatAud (value: unknown): string {
  const n = toFinite(value)
  if (n === null) return UNAVAILABLE
  const sign = n < 0 ? '-' : ''
  return `${sign}A$${Math.abs(n).toFixed(2)}`
}

export function formatKwh (value: unknown): string {
  const n = toFinite(value)
  return n === null ? UNAVAILABLE : `${n.toFixed(3)} kWh`
}

/** Satoshis are integers from the ledger; a string, fraction or unsafe value is not a satoshi amount. */
export function formatSats (value: unknown): string {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) return UNAVAILABLE
  return `${value.toLocaleString('en-AU')} sat`
}

export function formatCount (value: unknown): string {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? String(value) : UNAVAILABLE
}

export function formatTime (value: unknown): string {
  if (typeof value !== 'string' || value === '') return UNAVAILABLE
  const t = Date.parse(value)
  return Number.isFinite(t)
    ? new Date(t).toLocaleString('en-AU', { dateStyle: 'medium', timeStyle: 'short' })
    : UNAVAILABLE
}

export function isTxid (value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

export function shortTxid (txid: unknown): string {
  return isTxid(txid) ? `${txid.slice(0, 10)}…${txid.slice(-6)}` : UNAVAILABLE
}

export function compactIdentity (identity: unknown): string {
  return typeof identity === 'string' && /^(02|03)[0-9a-f]{64}$/.test(identity)
    ? `${identity.slice(0, 6)}…${identity.slice(-4)}`
    : UNAVAILABLE
}
