// Public indicative rates, ported from frontend/driver/portal-prices.js.
// Presentation only: a price never confers spending or session authority.
// A stale, estimated, out-of-window or malformed price is "Unavailable".
import { UNAVAILABLE } from './format.ts'

export interface PriceView { value: string, available: boolean }

export function priceCard (prices: unknown, direction: 'import' | 'export', now = Date.now()): PriceView {
  const p = (prices !== null && typeof prices === 'object' ? prices : {}) as Record<string, unknown>
  const item = (p[direction] !== null && typeof p[direction] === 'object' ? p[direction] : {}) as Record<string, unknown>
  const checked = Date.parse(String(p.checked_at))
  const start = Date.parse(String(item.start))
  const end = Date.parse(String(item.end))
  const raw = item.aud_per_kwh
  const valid = item.available === true && item.estimate === false &&
    typeof raw === 'string' && raw.trim() !== '' && Number.isFinite(Number(raw)) && Math.abs(Number(raw)) <= 1000000 &&
    Number.isFinite(checked) && now - checked <= 90000 && checked <= now + 5000 &&
    Number.isFinite(start) && Number.isFinite(end) && start <= now && now < end && end > start
  return valid ? { value: `${Number(raw).toFixed(4)} $/kWh`, available: true } : { value: UNAVAILABLE, available: false }
}
