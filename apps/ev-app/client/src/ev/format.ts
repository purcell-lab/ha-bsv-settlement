// Display formatters. Rule: a missing / unknown value is shown as "unavailable",
// NEVER as 0. Pure module with no imports so it can be unit-tested directly.

export interface Reading<T extends number | string> {
  value: T | null
  unit: string | null
  reason: string | null
  last_updated: string | null
}

export const UNAVAILABLE = 'unavailable'

const REASONS: Record<string, string> = {
  unknown: 'unknown in Home Assistant',
  unavailable: 'sensor unavailable',
  empty: 'no value reported',
  not_numeric: 'not a number',
  ha_timeout: 'Home Assistant did not answer in time',
  ha_not_found: 'sensor not found',
  ha_unauthorized: 'not permitted',
  ha_oversize: 'response too large',
  ha_network: 'Home Assistant unreachable'
}

export function describeReason (reason: string | null): string {
  if (reason === null) return ''
  return REASONS[reason] ?? (reason.startsWith('ha_') ? 'Home Assistant read failed' : reason)
}

function isNumber (value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function formatNumber (value: unknown, opts: { decimals?: number, unit?: string | null } = {}): string {
  if (!isNumber(value)) return UNAVAILABLE
  const text = value.toLocaleString('en-AU', {
    minimumFractionDigits: opts.decimals ?? 0,
    maximumFractionDigits: opts.decimals ?? 3
  })
  return opts.unit != null && opts.unit !== '' ? `${text} ${opts.unit}` : text
}

/** Format a server Reading. Null / malformed → "unavailable", never "0". */
export function formatReading (reading: Reading<number | string> | null | undefined, decimals?: number): string {
  if (reading == null || reading.value === null || reading.value === undefined) return UNAVAILABLE
  if (typeof reading.value === 'string') return reading.value === '' ? UNAVAILABLE : reading.value
  return formatNumber(reading.value, { decimals, unit: reading.unit })
}

export function formatSats (value: unknown): string {
  if (!isNumber(value) || !Number.isInteger(value)) return UNAVAILABLE
  return `${value.toLocaleString('en-AU')} sats`
}

export function formatAud (value: unknown): string {
  if (!isNumber(value)) return UNAVAILABLE
  const sign = value < 0 ? '-' : ''
  return `${sign}A$${Math.abs(value).toFixed(2)}`
}

export function formatCount (value: unknown): string {
  return isNumber(value) && Number.isInteger(value) && value >= 0 ? String(value) : UNAVAILABLE
}

export function shortTxid (txid: unknown): string {
  return typeof txid === 'string' && /^[0-9a-f]{64}$/.test(txid) ? `${txid.slice(0, 10)}…${txid.slice(-6)}` : UNAVAILABLE
}
