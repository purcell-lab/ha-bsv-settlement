import { test } from 'node:test'
import assert from 'node:assert/strict'
import { formatReading, formatSats, formatAud, formatCount, formatNumber, shortTxid, UNAVAILABLE } from '../src/ev/format.ts'

const r = (value: number | string | null, unit: string | null = null) => ({ value, unit, reason: value === null ? 'unknown' : null, last_updated: null })

test('unknown is never shown as zero', () => {
  assert.equal(formatReading(r(null, 'kWh')), UNAVAILABLE)
  assert.equal(formatReading(null), UNAVAILABLE)
  assert.equal(formatReading(undefined), UNAVAILABLE)
  assert.equal(formatReading(r('')), UNAVAILABLE)
  for (const bad of [null, undefined, NaN, Infinity, '0', '', 'unknown', {}]) {
    assert.equal(formatSats(bad), UNAVAILABLE)
    assert.equal(formatAud(bad), UNAVAILABLE)
    assert.equal(formatCount(bad), UNAVAILABLE)
    assert.equal(formatNumber(bad), UNAVAILABLE)
  }
  assert.equal(formatSats(1.5), UNAVAILABLE)
  assert.equal(shortTxid('zz'), UNAVAILABLE)
})

test('real zero and real values are shown', () => {
  assert.equal(formatReading(r(0, 'kWh')), '0 kWh')
  assert.equal(formatReading(r(1.2345, 'AUD'), 2), '1.23 AUD')
  assert.equal(formatReading(r('recording')), 'recording')
  assert.equal(formatSats(0), '0 sats')
  assert.equal(formatSats(1200), '1,200 sats')
  assert.equal(formatAud(-0.42), '-A$0.42')
  assert.equal(formatCount(0), '0')
  assert.equal(shortTxid('a'.repeat(64)), 'aaaaaaaaaa…aaaaaa')
})
