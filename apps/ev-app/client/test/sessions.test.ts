import { test } from 'node:test'
import assert from 'node:assert/strict'
import { UNAVAILABLE } from '../src/ev/format.ts'
import { priceCard } from '../src/ev/prices.ts'
import { DEMO_RATE_LABEL, projectSession, projectSessions, provisionalSats, transactionStatus } from '../src/ev/sessions.ts'

const now = Date.parse('2026-10-04T00:00:00Z')
const rate = { available: true, estimate: false, aud_per_kwh: '0.285', start: '2026-10-03T23:55:00Z', end: '2026-10-04T00:05:00Z' }
const prices = { checked_at: new Date(now).toISOString(), import: rate, export: { ...rate, aud_per_kwh: '-0.052' } }

test('public prices keep direction, zero and negative values', () => {
  assert.deepEqual(priceCard(prices, 'import', now), { value: '0.2850 $/kWh', available: true })
  assert.equal(priceCard(prices, 'export', now).value, '-0.0520 $/kWh')
  assert.equal(priceCard({ ...prices, import: { ...rate, aud_per_kwh: '0' } }, 'import', now).value, '0.0000 $/kWh')
})

test('missing, estimated, stale or out-of-window prices are unavailable, never 0', () => {
  for (const patch of [{ available: false }, { estimate: true }, { estimate: null }, { aud_per_kwh: null }, { aud_per_kwh: '' },
    { aud_per_kwh: 'NaN' }, { aud_per_kwh: 0.285 }, { aud_per_kwh: '1000001' }, { start: 'invalid' }, { end: '2026-10-04T00:00:00Z' }]) {
    assert.equal(priceCard({ ...prices, import: { ...rate, ...patch } }, 'import', now).value, UNAVAILABLE)
  }
  for (const p of [null, undefined, {}, 'x', { checked_at: 'invalid', import: rate }]) assert.equal(priceCard(p, 'import', now).value, UNAVAILABLE)
  assert.equal(priceCard(prices, 'import', now + 90001).available, false)
  assert.equal(priceCard(prices, 'import', now - 5001).available, false)
})

const credit = {
  id: 'b1', direction: 'operator_to_driver', state: 'provider_confirmed', txid: 'c'.repeat(64), amount_sats: 1520,
  fee_sats: 12, confirmations: 3, created_at: '2026-10-03T22:00:00Z', wallet_receipt_status: 'wallet_reported_accepted',
  wallet_imported_at: '2026-10-03T22:05:00Z'
}
const closed = {
  session_key: 'proxy|s1', session_id: 's1', transaction_id: 42, opened_at: '2026-10-03T20:00:00Z', ended_at: '2026-10-03T21:00:00Z',
  import_kwh: '0', export_kwh: '7.25', net_amount_aud: '-1.52', meter_updated_at: '2026-10-03T21:00:00Z', satoshis_per_aud: null,
  quality_flags: [], agreements: [], transactions: [credit]
}

test('a closed session projects only what the portal returned', () => {
  const v = projectSession(closed, 0, now)
  assert.equal(v.key, 'proxy|s1')
  assert.equal(v.title, 'Charging session 42')
  assert.equal(v.importKwh, '0.000 kWh') // real zero stays zero
  assert.equal(v.exportKwh, '7.250 kWh')
  assert.equal(v.netAud, '-A$1.52')
  assert.equal(v.summary, 'Credit 1,520 sat')
  assert.equal(v.status, 'Received')
  assert.equal(v.provisional, null)
  assert.equal(v.warning, false)
  assert.deepEqual(v.payments.map(p => [p.direction, p.amount, p.fee, p.confirmations, p.txid]),
    [['Credit to you', '1,520 sat', '12 sat', '3', 'c'.repeat(64)]])
  assert.equal(v.payments[0].status, 'Confirmed · wallet acceptance recorded')
})

test('unknown session values show unavailable, never 0', () => {
  const v = projectSession({ ...closed, import_kwh: null, export_kwh: undefined, net_amount_aud: '', opened_at: null,
    transactions: [{ id: 'd', direction: 'driver_to_operator', state: 'submitted', amount_sats: null, fee_sats: '12', txid: 'bad', confirmations: null }] }, 0, now)
  assert.equal(v.importKwh, UNAVAILABLE)
  assert.equal(v.exportKwh, UNAVAILABLE)
  assert.equal(v.netAud, UNAVAILABLE)
  assert.equal(v.opened, UNAVAILABLE)
  assert.equal(v.summary, 'Pay amount unavailable')
  const p = v.payments[0]
  assert.deepEqual([p.amount, p.fee, p.confirmations, p.txid, p.txidShort], [UNAVAILABLE, UNAVAILABLE, UNAVAILABLE, null, 'Not submitted'])
  const junk = projectSessions([null, 'x', 7], now)
  assert.equal(junk.length, 3)
  assert.ok(junk.every(s => s.netAud === UNAVAILABLE && s.payments.length === 0))
})

test('an open session shows a labelled provisional amount only from fresh data and a valid rate', () => {
  const open = { ...closed, ended_at: null, net_amount_aud: '2.50', satoshis_per_aud: '1000', meter_updated_at: new Date(now - 30000).toISOString(),
    transactions: [{ id: 'w', direction: 'driver_to_operator', state: 'waiting_for_session_end' }] }
  const v = projectSession(open, 0, now)
  assert.ok(v.provisional)
  assert.equal(v.provisional.amount, 'Provisional charge: 2,500 sat')
  assert.match(v.provisional.note, /Not a payment request/)
  assert.ok(v.provisional.note.includes(DEMO_RATE_LABEL))
  assert.equal(DEMO_RATE_LABEL, 'demonstration rate, not market FX')
  assert.equal(v.status, 'Active')
  const credit = projectSession({ ...open, net_amount_aud: '-0.40' }, 0, now)
  assert.equal(credit.provisional?.amount, 'Provisional credit to you: 400 sat')
  const stale = projectSession({ ...open, meter_updated_at: new Date(now - 121000).toISOString() }, 0, now)
  assert.equal(stale.provisional?.amount, 'Provisional amount unavailable')
  assert.match(stale.provisional?.note ?? '', /fresh meter update/)
  const noRate = projectSession({ ...open, satoshis_per_aud: null }, 0, now)
  assert.equal(noRate.provisional?.amount, 'Provisional amount unavailable')
  const submitted = projectSession({ ...open, transactions: [{ id: 'x', state: 'submitted', txid: 'e'.repeat(64) }] }, 0, now)
  assert.equal(submitted.provisional, null) // never replaces a submitted payment
})

test('provisional satoshis use decimal half-up rounding and fail closed', () => {
  assert.equal(provisionalSats('0.0015', '1000'), 2)
  assert.equal(provisionalSats('-0.0015', '1000'), 2) // magnitude; direction comes from the sign, as frontend/ui.js
  assert.equal(provisionalSats('1', '0'), null)
  assert.equal(provisionalSats('', '1000'), null)
  assert.equal(provisionalSats('1', 'abc'), null)
})

test('status wording keeps provider confirmation and wallet acceptance separate', () => {
  assert.match(transactionStatus({ direction: 'operator_to_driver', state: 'provider_confirmed' }), /receipt sync needed/)
  assert.equal(transactionStatus({ state: 'broadcast_unknown' }), 'Submission uncertain · do not retry payment')
  assert.equal(transactionStatus({ state: 'monthly_reserved' }), 'Session amount reserved within allowance. No payment confirmed.')
  assert.equal(transactionStatus({}), 'Review required')
  assert.equal(transactionStatus({ state: 'odd_new_state' }), 'odd new state')
})
