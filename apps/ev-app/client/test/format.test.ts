import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  compactIdentity, formatAud, formatCount, formatKwh, formatSats, formatTime, shortTxid, toFinite, UNAVAILABLE
} from '../src/ev/format.ts'

test('unknown is never shown as zero', () => {
  for (const bad of [null, undefined, NaN, Infinity, -Infinity, '', ' ', 'unknown', 'unavailable', '1e3', '0x10', {}, [], true]) {
    assert.equal(toFinite(bad), null)
    assert.equal(formatAud(bad), UNAVAILABLE)
    assert.equal(formatKwh(bad), UNAVAILABLE)
    assert.equal(formatSats(bad), UNAVAILABLE)
    assert.equal(formatCount(bad), UNAVAILABLE)
    assert.equal(formatTime(bad), UNAVAILABLE)
  }
  assert.equal(formatSats(1.5), UNAVAILABLE)
  assert.equal(formatSats('1200'), UNAVAILABLE) // satoshis are integers, never parsed strings
  assert.equal(formatSats(2 ** 60), UNAVAILABLE)
  assert.equal(formatCount(-1), UNAVAILABLE)
  assert.equal(formatTime('not a date'), UNAVAILABLE)
  assert.equal(shortTxid('zz'), UNAVAILABLE)
  assert.equal(compactIdentity('04' + 'ab'.repeat(32)), UNAVAILABLE)
})

test('real zero and real values are shown', () => {
  assert.equal(formatAud(0), 'A$0.00')
  assert.equal(formatAud('0'), 'A$0.00')
  assert.equal(formatAud('-0.42'), '-A$0.42')
  assert.equal(formatAud(12.345), 'A$12.35')
  assert.equal(formatKwh('0'), '0.000 kWh')
  assert.equal(formatKwh(7.5), '7.500 kWh')
  assert.equal(formatSats(0), '0 sat')
  assert.equal(formatSats(1200), '1,200 sat')
  assert.equal(formatCount(0), '0')
  assert.equal(shortTxid('a'.repeat(64)), 'aaaaaaaaaa…aaaaaa')
  assert.equal(compactIdentity('02' + 'ab'.repeat(32)), '02abab…abab')
  assert.notEqual(formatTime('2026-10-04T00:00:00Z'), UNAVAILABLE)
})
