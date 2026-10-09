import { test, describe, before, after, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import type { AddressInfo } from 'node:net'
import { ProtoWallet, PrivateKey } from '@bsv/sdk'
import { FakeHa, ALL_REQUESTS } from './fakeHa.js'
import { createApp, PROOF_HEADER, ME_CREDITS_ACTION } from '../src/app.js'
import { loadHaConfig, ENTITY_DEFAULTS, type HaConfig } from '../src/ha/config.js'
import { HaStateReader, HaError } from '../src/ha/client.js'
import { creditsForIdentity } from '../src/ha/credits.js'
import { createAuthProof } from '../src/bsv/auth.js'

const TOKEN = 'test-token-SECRET-0123456789abcdef-never-leak'
const E = ENTITY_DEFAULTS

// Capture everything written to the console so we can prove the token never appears.
const logged: string[] = []
for (const level of ['log', 'warn', 'error', 'info', 'debug'] as const) {
  const original = console[level].bind(console)
  console[level] = (...args: unknown[]) => { logged.push(args.map(String).join(' ')); void original }
}

function haConfig (url: string, extra: Record<string, string> = {}): HaConfig {
  return loadHaConfig({ HA_URL: url, HA_TOKEN: TOKEN, HA_ALLOW_INSECURE_LAN: '1', HA_TIMEOUT_MS: '300', HA_MAX_RESPONSE_BYTES: '65536', ...extra })
}

async function identityOf (wallet: ProtoWallet): Promise<string> {
  return (await wallet.getPublicKey({ identityKey: true })).publicKey
}

interface Harness { base: string, close: () => Promise<void>, logs: string[] }

async function startApp (fake: FakeHa, opts: { capacity?: number, cfg?: Record<string, string> } = {}): Promise<Harness & { serverWallet: ProtoWallet }> {
  const serverWallet = new ProtoWallet(PrivateKey.fromRandom())
  const cfg = haConfig(fake.url, opts.cfg)
  const logs: string[] = []
  const ha = new HaStateReader(cfg, { logger: { warn: (m) => { logs.push(m) } } })
  const app = createApp({
    serverWallet, clientOrigin: 'http://localhost:5173', ha, entities: cfg.entities,
    rateLimit: { capacity: opts.capacity ?? 1000, refillPerSecond: 0.001 }, stationCacheMs: 0
  })
  const server = http.createServer(app)
  await new Promise<void>(resolve => { server.listen(0, '127.0.0.1', resolve) })
  const { port } = server.address() as AddressInfo
  return {
    base: `http://127.0.0.1:${port}`,
    logs,
    serverWallet,
    close: async () => { server.closeAllConnections(); await new Promise<void>(resolve => { server.close(() => { resolve() }) }) }
  }
}

const ME = new ProtoWallet(PrivateKey.fromRandom())
const OTHER = new ProtoWallet(PrivateKey.fromRandom())
let meKey = ''
let otherKey = ''
const TXA = 'a'.repeat(64)
const TXB = 'b'.repeat(64)
const TXC = 'c'.repeat(64)
const TXD = 'd'.repeat(64)

function walletAttributes (): Record<string, unknown> {
  return {
    mode: 'mainnet',
    receive_address: '1OperatorAddr',
    ongoing_credit: {
      sessions: [
        { credit_id: 'r1', session_id: 'S-ME-1', state: 'confirmed', driver_identity: meKey, recipient_address: '1MyAddr',
          receiving_budget_id: 'budget-me', amount_sats: 1200, fee_sats: 10, net_amount_aud: -0.42, txid: TXA,
          confirmations: 3, created_at: '2026-10-01T00:00:00Z', wallet_receipt_status: 'wallet_reported_accepted', secret_extra: 'x' },
        { credit_id: 'r2', session_id: 'S-OTHER-1', state: 'confirmed', driver_identity: otherKey, recipient_address: '1OtherAddr',
          receiving_budget_id: 'budget-other', amount_sats: 999, txid: TXB },
        { credit_id: 'r3', session_id: 'S-NOID', state: 'confirmed', recipient_address: '1Anon', receiving_budget_id: 'budget-anon', amount_sats: 5 },
        { credit_id: 'r4', session_id: 'S-BADID', state: 'confirmed', driver_identity: 'not-a-key', amount_sats: 6 }
      ]
    },
    automatic_credit: {
      payments: [
        { state: 'confirmed', budget_id: 'budget-me', session_id: 'S-ME-AUTO', recipient_address: '1MyAddr', amount_sats: 300,
          fee_sats: 5, net_amount_aud: -0.1, txid: TXC, confirmations: 1, created_at: '2026-10-02T00:00:00Z', wallet_receipt_status: 'not_recorded' },
        { state: 'confirmed', budget_id: 'budget-other', session_id: 'S-OTHER-AUTO', recipient_address: '1OtherAddr', amount_sats: 400, txid: TXD },
        { state: 'confirmed', budget_id: 'budget-unknown', session_id: 'S-UNLINKED', recipient_address: '1Nobody', amount_sats: 1 },
        { state: 'confirmed', session_id: 'S-NOLINK', amount_sats: 2 },
        { state: 'confirmed', budget_id: 'budget-me', session_id: 'S-INCONSISTENT', recipient_address: '1OtherAddr', amount_sats: 3 }
      ]
    }
  }
}

function seedStation (fake: FakeHa): void {
  fake.setState(E.recorderStatus, 'recording')
  fake.setState(E.provisionalCost, '1.234', { unit_of_measurement: 'AUD' })
  fake.setState(E.importEnergy, 'unknown', { unit_of_measurement: 'kWh' })
  fake.setState(E.exportEnergy, 'unavailable', { unit_of_measurement: 'kWh' })
  fake.setState(E.proxyTransactionId, 'TX-42')
  fake.setState(E.satoshisPerAud, '250000')
  fake.setState(E.importPrice, '0.31', { unit_of_measurement: '$/kWh' })
  fake.setState(E.exportPrice, 'abc', { unit_of_measurement: '$/kWh' })
  fake.setState(E.ocppShadowLifecycle, 'idle')
  fake.setState(E.walletStatus, 'ready', walletAttributes())
}

before(async () => {
  meKey = await identityOf(ME)
  otherKey = await identityOf(OTHER)
})

describe('HA configuration', () => {
  test('requires https unless HA_ALLOW_INSECURE_LAN=1 for a LAN host', () => {
    assert.throws(() => loadHaConfig({ HA_URL: 'http://192.168.1.5:8123', HA_TOKEN: TOKEN }), /https/)
    assert.throws(() => loadHaConfig({ HA_URL: 'http://ha.example.com', HA_TOKEN: TOKEN, HA_ALLOW_INSECURE_LAN: '1' }), /LAN/)
    assert.throws(() => loadHaConfig({ HA_URL: 'ftp://x', HA_TOKEN: TOKEN }), /https/)
    assert.throws(() => loadHaConfig({ HA_URL: 'https://u:p@ha.example.com', HA_TOKEN: TOKEN }), /credentials/)
    assert.equal(loadHaConfig({ HA_URL: 'https://ha.example.com/', HA_TOKEN: TOKEN }).baseUrl, 'https://ha.example.com')
    assert.equal(loadHaConfig({ HA_URL: 'http://homeassistant.local:8123', HA_TOKEN: TOKEN, HA_ALLOW_INSECURE_LAN: '1' }).baseUrl,
      'http://homeassistant.local:8123')
  })

  test('token errors never echo the token', () => {
    const bad = 'short secret with spaces'
    try { loadHaConfig({ HA_URL: 'https://ha.example.com', HA_TOKEN: bad }); assert.fail('expected throw') } catch (e) {
      assert.doesNotMatch(String(e), /short secret/)
    }
    assert.throws(() => loadHaConfig({ HA_URL: 'https://ha.example.com' }), /HA_TOKEN is required/)
  })

  test('entity ids are configurable and validated', () => {
    const cfg = loadHaConfig({ HA_URL: 'https://h.example', HA_TOKEN: TOKEN, HA_ENTITY_RECORDER_STATUS: 'sensor.custom_recorder' })
    assert.equal(cfg.entities.recorderStatus, 'sensor.custom_recorder')
    assert.equal(cfg.entities.walletStatus, E.walletStatus)
    assert.throws(() => loadHaConfig({ HA_URL: 'https://h.example', HA_TOKEN: TOKEN, HA_ENTITY_WALLET_STATUS: '../api/services' }), /valid entity id/)
  })
})

describe('HA state reader', () => {
  const fake = new FakeHa()
  before(async () => { await fake.start() })
  after(async () => { await fake.stop() })
  beforeEach(() => { fake.requests.length = 0; fake.replies.clear() })

  test('refuses non-allowlisted entities without contacting HA', async () => {
    const reader = new HaStateReader(haConfig(fake.url), { logger: { warn: () => {} } })
    fake.setState('lock.front_door', 'locked')
    await assert.rejects(reader.getState('lock.front_door'), (e: unknown) => e instanceof HaError && e.code === 'not_allowlisted')
    await assert.rejects(reader.getState('../services/lock/unlock'), (e: unknown) => e instanceof HaError && e.code === 'not_allowlisted')
    assert.equal(fake.requests.length, 0)
  })

  test('sends only GET /api/states/<id> with the bearer token', async () => {
    const reader = new HaStateReader(haConfig(fake.url), { logger: { warn: () => {} } })
    fake.setState(E.recorderStatus, 'recording')
    const state = await reader.getState(E.recorderStatus)
    assert.equal(state.state, 'recording')
    assert.deepEqual(fake.requests, [{ method: 'GET', url: `/api/states/${E.recorderStatus}`, authorization: `Bearer ${TOKEN}` }])
  })

  test('oversize responses are refused (declared and chunked)', async () => {
    const logs: string[] = []
    const reader = new HaStateReader(haConfig(fake.url, { HA_MAX_RESPONSE_BYTES: '2048' }), { logger: { warn: (m) => { logs.push(m) } } })
    const huge = { entity_id: E.walletStatus, state: 'ready', attributes: { blob: 'x'.repeat(10_000) } }
    fake.replies.set(E.walletStatus, { body: huge })
    await assert.rejects(reader.getState(E.walletStatus), (e: unknown) => e instanceof HaError && e.code === 'oversize')
    fake.replies.set(E.walletStatus, { body: huge, chunked: true })
    await assert.rejects(reader.getState(E.walletStatus), (e: unknown) => e instanceof HaError && e.code === 'oversize')
    assert.ok(logs.every(l => !l.includes(TOKEN)))
  })

  test('slow responses time out', async () => {
    const reader = new HaStateReader(haConfig(fake.url, { HA_TIMEOUT_MS: '150' }), { logger: { warn: () => {} } })
    fake.replies.set(E.recorderStatus, { body: { entity_id: E.recorderStatus, state: 'x', attributes: {} }, delayMs: 2000 })
    const started = Date.now()
    await assert.rejects(reader.getState(E.recorderStatus), (e: unknown) => e instanceof HaError && e.code === 'timeout')
    assert.ok(Date.now() - started < 1500)
  })

  test('redirects are never followed', async () => {
    const reader = new HaStateReader(haConfig(fake.url), { logger: { warn: () => {} } })
    fake.replies.set(E.recorderStatus, { status: 302, raw: '', headers: { location: `${fake.url}/api/services/switch/turn_on` } })
    await assert.rejects(reader.getState(E.recorderStatus), (e: unknown) => e instanceof HaError && e.code === 'redirect')
    assert.equal(fake.requests.length, 1)
  })

  test('strict JSON and shape', async () => {
    const reader = new HaStateReader(haConfig(fake.url), { logger: { warn: () => {} } })
    fake.replies.set(E.recorderStatus, { raw: '{"entity_id": "x", ' })
    await assert.rejects(reader.getState(E.recorderStatus), (e: unknown) => e instanceof HaError && e.code === 'bad_json')
    fake.replies.set(E.recorderStatus, { body: { entity_id: 'sensor.other', state: '1', attributes: {} } })
    await assert.rejects(reader.getState(E.recorderStatus), (e: unknown) => e instanceof HaError && e.code === 'bad_shape')
    fake.replies.set(E.recorderStatus, { raw: '{}', headers: { 'content-type': 'text/html' } })
    await assert.rejects(reader.getState(E.recorderStatus), (e: unknown) => e instanceof HaError && e.code === 'bad_content_type')
    fake.replies.set(E.recorderStatus, { status: 401, body: { message: 'nope' } })
    await assert.rejects(reader.getState(E.recorderStatus), (e: unknown) => e instanceof HaError && e.code === 'unauthorized')
  })
})

describe('GET /api/station', () => {
  const fake = new FakeHa()
  let h: Harness
  before(async () => { await fake.start(); h = await startApp(fake) })
  after(async () => { await h.close(); await fake.stop() })
  beforeEach(() => { fake.requests.length = 0; fake.replies.clear(); seedStation(fake) })

  test('maps values; unknown/unavailable/non-numeric stay null with a reason, never 0', async () => {
    const res = await fetch(`${h.base}/api/station`)
    assert.equal(res.status, 200)
    const text = await res.text()
    assert.ok(!text.includes(TOKEN))
    const body = JSON.parse(text)
    assert.equal(body.recorder.status.value, 'recording')
    assert.equal(body.session.transaction_id.value, 'TX-42')
    assert.equal(body.session.provisional_cost.value, 1.234)
    assert.equal(body.session.provisional_cost.unit, 'AUD')
    assert.match(body.session.provisional_cost.label, /Provisional/)
    assert.match(body.session.provisional_cost.label, /Not a bill/)
    assert.deepEqual([body.session.import_energy.value, body.session.import_energy.reason], [null, 'unknown'])
    assert.deepEqual([body.session.export_energy.value, body.session.export_energy.reason], [null, 'unavailable'])
    assert.deepEqual([body.prices.export.value, body.prices.export.reason], [null, 'not_numeric'])
    assert.equal(body.prices.import.value, 0.31)
    assert.equal(body.conversion.satoshis_per_aud.value, 250000)
    assert.equal(body.conversion.label, 'demonstration rate, not market FX')
    assert.equal(body.ocpp_shadow.lifecycle.value, 'idle')
    assert.match(body.ocpp_shadow.label, /shadow only/i)
    // The wallet sensor is not part of the public station view.
    assert.ok(!fake.requests.some(r => r.url.includes(E.walletStatus)))
  })

  test('HA failures produce null with an ha_* reason', async () => {
    fake.replies.delete(E.provisionalCost)
    fake.replies.set(E.satoshisPerAud, { status: 500, raw: 'boom' })
    fake.replies.set(E.importPrice, { body: { entity_id: E.importPrice, state: '1', attributes: { pad: 'y'.repeat(70_000) } } })
    fake.replies.set(E.recorderStatus, { body: { entity_id: E.recorderStatus, state: 'x', attributes: {} }, delayMs: 2000 })
    const body = await (await fetch(`${h.base}/api/station`)).json()
    assert.deepEqual([body.session.provisional_cost.value, body.session.provisional_cost.reason], [null, 'ha_not_found'])
    assert.deepEqual([body.conversion.satoshis_per_aud.value, body.conversion.satoshis_per_aud.reason], [null, 'ha_http_status'])
    assert.deepEqual([body.prices.import.value, body.prices.import.reason], [null, 'ha_oversize'])
    assert.deepEqual([body.recorder.status.value, body.recorder.status.reason], [null, 'ha_timeout'])
    assert.ok(h.logs.length > 0 && h.logs.every(l => !l.includes(TOKEN)))
  })
})

describe('GET /api/me/credits', () => {
  const fake = new FakeHa()
  let h: Awaited<ReturnType<typeof startApp>>
  let serverKey = ''
  before(async () => { await fake.start(); h = await startApp(fake); serverKey = await identityOf(h.serverWallet) })
  after(async () => { await h.close(); await fake.stop() })
  beforeEach(() => { fake.requests.length = 0; fake.replies.clear(); seedStation(fake) })

  async function login (wallet: ProtoWallet): Promise<string> {
    const proof = await createAuthProof(wallet, { counterparty: serverKey, action: 'login' })
    const res = await fetch(`${h.base}/api/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(proof) })
    assert.equal(res.status, 200)
    const body = await res.json() as { identityKey: string, sessionToken: string }
    assert.equal(body.identityKey, await identityOf(wallet))
    return body.sessionToken
  }

  test('requires a wallet login session or signed request', async () => {
    assert.equal((await fetch(`${h.base}/api/me/credits`)).status, 401)
    assert.equal((await fetch(`${h.base}/api/me/credits`, { headers: { authorization: 'Bearer ' + 'A'.repeat(43) } })).status, 401)
    assert.equal((await fetch(`${h.base}/api/me/credits`, { headers: { authorization: 'Basic abc' } })).status, 401)
    assert.equal((await fetch(`${h.base}/api/me/credits`, { headers: { [PROOF_HEADER]: Buffer.from('{"data":{}}').toString('base64') } })).status, 401)
    // A login proof cannot be replayed.
    const proof = await createAuthProof(ME, { counterparty: serverKey, action: 'login' })
    const post = async (): Promise<number> => (await fetch(`${h.base}/api/login`, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(proof) })).status
    assert.equal(await post(), 200)
    assert.equal(await post(), 401)
    // HA is never contacted for an unauthenticated request.
    assert.ok(!fake.requests.some(r => r.url.includes(E.walletStatus)) || fake.requests.length === 0)
  })

  test('session returns only the verified identity rows, minimal projection', async () => {
    fake.requests.length = 0
    const token = await login(ME)
    const res = await fetch(`${h.base}/api/me/credits`, { headers: { authorization: `Bearer ${token}` } })
    assert.equal(res.status, 200)
    const text = await res.text()
    const body = JSON.parse(text)
    assert.equal(body.identity_key, meKey)
    assert.deepEqual(body.credits.map((c: { session_id: string }) => c.session_id).sort(), ['S-ME-1', 'S-ME-AUTO'])
    for (const forbidden of ['S-OTHER', 'S-NOID', 'S-BADID', 'S-UNLINKED', 'S-NOLINK', 'S-INCONSISTENT', otherKey, '1OtherAddr',
      '1MyAddr', '1OperatorAddr', 'secret_extra', 'receiving_budget_id', 'budget-', TOKEN]) {
      assert.ok(!text.includes(forbidden), `response leaked ${forbidden}`)
    }
    const first = body.credits.find((c: { session_id: string }) => c.session_id === 'S-ME-1')
    assert.deepEqual(Object.keys(first).sort(), ['amount_sats', 'confirmations', 'created_at', 'fee_sats', 'net_amount_aud',
      'session_id', 'source', 'state', 'txid', 'wallet_receipt_status'])
    assert.equal(first.txid, TXA)
    assert.deepEqual(fake.requests.map(r => `${r.method} ${r.url}`), [`GET /api/states/${E.walletStatus}`])
  })

  test('another driver sees only their own rows', async () => {
    const token = await login(OTHER)
    const body = await (await fetch(`${h.base}/api/me/credits`, { headers: { authorization: `Bearer ${token}` } })).json()
    assert.deepEqual(body.credits.map((c: { session_id: string }) => c.session_id).sort(), ['S-OTHER-1', 'S-OTHER-AUTO'])
  })

  test('signed request header works once and is action-bound', async () => {
    const proof = await createAuthProof(ME, { counterparty: serverKey, action: ME_CREDITS_ACTION })
    const header = Buffer.from(JSON.stringify(proof)).toString('base64')
    const ok = await fetch(`${h.base}/api/me/credits`, { headers: { [PROOF_HEADER]: header } })
    assert.equal(ok.status, 200)
    assert.equal((await ok.json()).identity_key, meKey)
    assert.equal((await fetch(`${h.base}/api/me/credits`, { headers: { [PROOF_HEADER]: header } })).status, 401, 'replay')
    const loginProof = await createAuthProof(ME, { counterparty: serverKey, action: 'login' })
    const wrong = Buffer.from(JSON.stringify(loginProof)).toString('base64')
    assert.equal((await fetch(`${h.base}/api/me/credits`, { headers: { [PROOF_HEADER]: wrong } })).status, 401, 'wrong action')
  })

  test('unavailable wallet sensor yields credits null with a reason, not an empty list', async () => {
    const token = await login(ME)
    fake.setState(E.walletStatus, 'unavailable')
    let body = await (await fetch(`${h.base}/api/me/credits`, { headers: { authorization: `Bearer ${token}` } })).json()
    assert.deepEqual([body.credits, body.reason], [null, 'unavailable'])
    fake.replies.set(E.walletStatus, { status: 500, raw: 'x' })
    body = await (await fetch(`${h.base}/api/me/credits`, { headers: { authorization: `Bearer ${token}` } })).json()
    assert.deepEqual([body.credits, body.reason], [null, 'ha_http_status'])
  })
})

describe('credit projection', () => {
  test('excludes rows with no verifiable identity and ambiguous links', () => {
    const rows = creditsForIdentity(walletAttributes(), meKey)
    assert.deepEqual(rows.map(r => r.session_id), ['S-ME-1', 'S-ME-AUTO'])
    assert.deepEqual(creditsForIdentity(walletAttributes(), 'not-a-key'), [])
    assert.deepEqual(creditsForIdentity({}, meKey), [])
    // An automatic row with its own driver_identity must match exactly.
    const attrs = { automatic_credit: { payments: [{ driver_identity: meKey, session_id: 'A' }, { driver_identity: otherKey, session_id: 'B' }] } }
    assert.deepEqual(creditsForIdentity(attrs, meKey).map(r => r.session_id), ['A'])
    // A budget shared by two identities proves nothing.
    const shared = {
      ongoing_credit: { sessions: [
        { driver_identity: meKey, receiving_budget_id: 'b', session_id: 'M' },
        { driver_identity: otherKey, receiving_budget_id: 'b', session_id: 'O' }] },
      automatic_credit: { payments: [{ budget_id: 'b', session_id: 'SHARED' }] }
    }
    assert.deepEqual(creditsForIdentity(shared, meKey).map(r => r.session_id), ['M'])
  })

  test('malformed numeric fields become null, never 0', () => {
    const attrs = { ongoing_credit: { sessions: [{ driver_identity: meKey, session_id: 'X', amount_sats: 'unknown', fee_sats: -1, txid: 'zz', net_amount_aud: null }] } }
    const [row] = creditsForIdentity(attrs, meKey)
    assert.deepEqual([row.amount_sats, row.fee_sats, row.txid, row.net_amount_aud], [null, null, null, null])
  })
})

describe('rate limit and CORS', () => {
  const fake = new FakeHa()
  let h: Harness
  before(async () => { await fake.start(); seedStation(fake); h = await startApp(fake, { capacity: 3 }) })
  after(async () => { await h.close(); await fake.stop() })

  test('per-IP token bucket returns 429 once the burst is spent', async () => {
    const statuses: number[] = []
    for (let i = 0; i < 5; i++) statuses.push((await fetch(`${h.base}/health`)).status)
    assert.deepEqual(statuses, [200, 200, 200, 429, 429])
    assert.equal((await fetch(`${h.base}/api/me/credits`)).status, 429)
  })
})

describe('CORS', () => {
  const fake = new FakeHa()
  let h: Harness
  before(async () => { await fake.start(); seedStation(fake); h = await startApp(fake) })
  after(async () => { await h.close(); await fake.stop() })

  test('only the configured client origin is allowed', async () => {
    const good = await fetch(`${h.base}/health`, { headers: { origin: 'http://localhost:5173' } })
    assert.equal(good.headers.get('access-control-allow-origin'), 'http://localhost:5173')
    const bad = await fetch(`${h.base}/health`, { headers: { origin: 'https://evil.example' } })
    assert.notEqual(bad.headers.get('access-control-allow-origin'), 'https://evil.example')
  })
})

describe('global invariants', () => {
  test('HA only ever received GET /api/states/<allowlisted entity> (no writes, no services)', () => {
    const allowed = new Set(Object.values(E).map(id => `/api/states/${id}`))
    assert.ok(ALL_REQUESTS.length > 10)
    for (const r of ALL_REQUESTS) {
      assert.equal(r.method, 'GET')
      assert.ok(allowed.has(r.url), `unexpected HA path ${r.url}`)
      assert.ok(!r.url.includes('/api/services'))
    }
  })

  test('the token never appeared in any console output', () => {
    assert.ok(logged.every(l => !l.includes(TOKEN)))
  })
})
