// Wire contract with custom_components/bsv_settlement/portal.py, as used by
// frontend/driver/portal.js api(): POST JSON, same-origin credentials.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { createPortal, portalRequest, PORTAL_ACTIONS, PORTAL_URL, PortalError, type FetchLike, type PortalAction } from '../src/ev/portal.ts'

interface Call { url: string, init: RequestInit, body: Record<string, unknown> }

function fakeFetch (reply: (body: Record<string, unknown>) => { status?: number, json?: unknown, text?: string }) {
  const calls: Call[] = []
  const fetchImpl: FetchLike = async (url, init) => {
    const body = JSON.parse(String(init.body)) as Record<string, unknown>
    calls.push({ url, init, body })
    const r = reply(body)
    const text = r.text ?? JSON.stringify(r.json ?? {})
    return new Response(text, { status: r.status ?? 200, headers: { 'Content-Type': 'application/json' } })
  }
  return { calls, fetchImpl }
}

const ID = '02' + 'ab'.repeat(32)

test('every request is a same-origin JSON POST to the existing portal endpoint', async () => {
  const { calls, fetchImpl } = fakeFetch(body => ({
    json: body.action === 'sessions'
      ? { identity: ID, sessions: [], total: 0, offset: 0, has_more: false, expires_in: 600 }
      : body.action === 'login' ? { identity: ID, expires_in: 900, scope: 'x' }
        : body.action === 'challenge' ? { payload: '{}', protocolID: [2, 'ev portal login'], keyID: 'k' }
          : {}
  }))
  const portal = createPortal(fetchImpl)
  await portal.prices()
  await portal.challenge()
  await portal.login({ identity: ID, payload: '{"a":1}', signature: 'ab'.repeat(35) })
  await portal.sessions(25)
  await portal.logout()
  assert.deepEqual(calls.map(c => c.body), [
    { action: 'prices' },
    { action: 'challenge' },
    { action: 'login', identity: ID, payload: '{"a":1}', signature: 'ab'.repeat(35) },
    { action: 'sessions', offset: 25 },
    { action: 'logout' }
  ])
  for (const c of calls) {
    assert.equal(c.url, '/api/bsv_settlement/portal')
    assert.equal(c.init.method, 'POST')
    assert.equal(c.init.credentials, 'same-origin')
    assert.equal(c.init.cache, 'no-store')
    assert.equal(c.init.referrerPolicy, 'no-referrer')
    assert.deepEqual(c.init.headers, { 'Content-Type': 'application/json' })
    assert.ok(c.init.signal instanceof AbortSignal)
    // The browser adds Origin; the page never sets it, nor any token or Authorization header.
    assert.ok(!('Origin' in (c.init.headers as Record<string, string>)))
    assert.ok(!('Authorization' in (c.init.headers as Record<string, string>)))
  }
  assert.equal(PORTAL_URL, '/api/bsv_settlement/portal')
})

test('only the five read-only actions can be sent', async () => {
  assert.deepEqual([...PORTAL_ACTIONS], ['prices', 'challenge', 'login', 'sessions', 'logout'])
  const { calls, fetchImpl } = fakeFetch(() => ({ json: {} }))
  for (const action of ['pairing_create', 'pairing_cancel', 'credit_receipt', 'acknowledge_credit_receipt', 'station',
    'monthly_status', 'monthly_accept', 'monthly_cancel', 'debit_jobs', 'debit_claim', 'debit_authorise', 'debit_report',
    'registration_offer', 'registration_accept', 'registration_receive', 'waive', '', 'PRICES']) {
    await assert.rejects(() => portalRequest(fetchImpl, action as PortalAction), /does not use that portal action/)
  }
  await assert.rejects(() => portalRequest(fetchImpl, 'sessions', { action: 'collect' }), /Invalid portal request/)
  assert.equal(calls.length, 0)
})

test('the app source contains no write-flow actions, BRC-103 client or API token', () => {
  const root = new URL('../src/', import.meta.url)
  const files = (readdirSync(root, { recursive: true }) as string[]).filter(f => /\.(ts|tsx)$/.test(f))
  assert.ok(files.length > 0)
  const source = files.map(f => readFileSync(new URL(f, root), 'utf8')).join('\n')
  // Every non-read portal action in portal.py, monthly_portal.py, portal_debits.py and portal_registration.py.
  for (const banned of ['pairing_create', 'pairing_cancel', 'credit_receipt', "'station'", 'monthly_status',
    'monthly_challenge', 'monthly_accept', 'monthly_cancel', 'debit_jobs', 'debit_status', 'debit_claim',
    'debit_authorise', 'debit_report', 'debit_failure', 'registration_', '@bsv/auth', '@bsv/wallet-relay', 'Authorization', 'Bearer', 'createAction', 'signAction',
    'internalizeAction', 'localStorage', 'sessionStorage', 'document.cookie']) {
    assert.ok(!source.includes(banned), `src must not reference ${banned}`)
  }
})

test('server errors keep status and code; 401 is distinguishable', async () => {
  const { fetchImpl } = fakeFetch(() => ({ status: 401, json: { error: 'Sign-in or request could not be verified.' } }))
  const portal = createPortal(fetchImpl)
  await assert.rejects(() => portal.sessions(), (e: unknown) =>
    e instanceof PortalError && e.status === 401 && e.message === 'Sign-in or request could not be verified.')
  const conflict = createPortal(fakeFetch(() => ({ status: 409, json: { error: 'Held', code: 'held' } })).fetchImpl)
  await assert.rejects(() => conflict.prices(), (e: unknown) => e instanceof PortalError && e.status === 409 && e.code === 'held')
  const html = createPortal(fakeFetch(() => ({ status: 502, text: '<html>bad gateway</html>' })).fetchImpl)
  await assert.rejects(() => html.prices(), (e: unknown) => e instanceof PortalError && e.status === 502 && e.message === 'Portal request failed')
  const notObject = createPortal(fakeFetch(() => ({ text: '[1,2]' })).fetchImpl)
  await assert.rejects(() => notObject.prices(), /Unexpected portal response/)
})

test('login and sessions responses are checked before use', async () => {
  const proof = { identity: ID, payload: '{}', signature: 'ab'.repeat(35) }
  const other = '03' + 'cd'.repeat(32)
  for (const json of [{ identity: other, expires_in: 900 }, { identity: ID, expires_in: 0 },
    { identity: ID, expires_in: 901 }, { identity: ID, expires_in: '900' }, { identity: ID }]) {
    await assert.rejects(() => createPortal(fakeFetch(() => ({ json })).fetchImpl).login(proof))
  }
  for (const json of [{ identity: 'x', sessions: [], total: 0, expires_in: 1 }, { identity: ID, sessions: {}, total: 0, expires_in: 1 },
    { identity: ID, sessions: [], total: -1, expires_in: 1 }, { identity: ID, sessions: [], total: 0 }]) {
    await assert.rejects(() => createPortal(fakeFetch(() => ({ json })).fetchImpl).sessions(), /Unexpected session history/)
  }
  const { calls, fetchImpl } = fakeFetch(() => ({ json: {} }))
  for (const offset of [-1, 1.5, 100001, Number.NaN]) {
    await assert.rejects(() => createPortal(fetchImpl).sessions(offset), /Invalid page offset/)
  }
  assert.equal(calls.length, 0)
  const challenge = createPortal(fakeFetch(() => ({ json: { payload: 1, keyID: 'k' } })).fetchImpl)
  await assert.rejects(() => challenge.challenge(), /Invalid portal sign-in challenge/)
})
