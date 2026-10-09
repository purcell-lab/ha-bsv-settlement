// The app must sign the portal challenge exactly as the driver page does, so
// the integration's existing verifier (portal.py) accepts it unchanged.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { KeyDeriver, PrivateKey, ProtoWallet, Signature } from '@bsv/sdk'
import { bytes, canonical, loginDescription, loginProtocol, loginScope, signPortalLogin, type LoginWallet } from '../src/ev/login.ts'
import type { Challenge } from '../src/ev/portal.ts'
import { walletSubstrate } from '../src/bsv/walletAcquisition.ts'

const origin = 'https://charging.example.com'

function challenge (patch: Record<string, unknown> = {}): Challenge {
  const now = Math.floor(Date.now() / 1000)
  const payload = {
    action: 'sign_in_driver_portal', version: 1, origin, scope: loginScope,
    nonce: 'a'.repeat(43), browser_binding: 'b'.repeat(64), issued_at: now, expires_at: now + 120, ...patch
  }
  return { payload: canonical(payload), protocolID: loginProtocol, keyID: String(payload.nonce) }
}

function recordingWallet (seed: number) {
  const wallet = new ProtoWallet(new PrivateKey(seed))
  const requests: Array<Record<string, unknown>> = []
  const sign = wallet.createSignature.bind(wallet)
  const recorded: LoginWallet = {
    getPublicKey: args => wallet.getPublicKey(args),
    createSignature: async args => { requests.push(args as unknown as Record<string, unknown>); return sign(args) }
  }
  return { wallet: recorded, requests }
}

test('signs the portal login protocol with the nonce key ID and identity key', async () => {
  const { wallet, requests } = recordingWallet(19)
  const c = challenge()
  const proof = await signPortalLogin(wallet, c, origin)
  const identity = (await wallet.getPublicKey({ identityKey: true })).publicKey
  assert.equal(proof.identity, identity)
  assert.equal(proof.payload, c.payload)
  assert.match(proof.signature, /^[0-9a-f]{16,144}$/) // portal.py's accepted signature form
  assert.equal(requests.length, 1)
  assert.deepEqual(requests[0], {
    protocolID: [2, 'ev portal login'], keyID: 'a'.repeat(43), counterparty: 'anyone',
    data: bytes(c.payload), description: loginDescription
  })
  // The verifier's derivation: child of the identity key for "2-ev portal login-<nonce>", counterparty anyone.
  const key = new KeyDeriver('anyone').derivePublicKey(loginProtocol, 'a'.repeat(43), identity)
  assert.ok(key.verify(bytes(c.payload), Signature.fromDER(Array.from(Buffer.from(proof.signature, 'hex')))))
})

test('rejects a wrong origin, expiry, scope, binding or extra field before contacting the wallet', async () => {
  const wallet: LoginWallet = {
    getPublicKey: () => assert.fail('Do not contact wallet'),
    createSignature: () => assert.fail('Do not contact wallet')
  }
  for (const patch of [{ origin: 'https://evil.example' }, { expires_at: 1 }, { issued_at: 99999999999 },
    { scope: 'authorise_spending' }, { nonce: 'short' }, { browser_binding: 'bad' }, { version: 2 }, { amount_sats: 1000 },
    { action: 'collect' }]) {
    await assert.rejects(() => signPortalLogin(wallet, challenge(patch), origin), /Invalid/)
  }
  const c = challenge()
  await assert.rejects(() => signPortalLogin(wallet, { ...c, keyID: 'b'.repeat(43) }, origin), /Invalid/)
  await assert.rejects(() => signPortalLogin(wallet, { ...c, protocolID: [2, 'ev session spending'] }, origin), /Invalid/)
  await assert.rejects(() => signPortalLogin(wallet, { ...c, payload: c.payload.replace('{', '{ ') }, origin), /Invalid/)
  await assert.rejects(() => signPortalLogin(wallet, { ...c, payload: 'not json' }, origin), /Invalid/)
})

test('a wallet signature that does not verify is never sent', async () => {
  const { wallet } = recordingWallet(19)
  const other = recordingWallet(23).wallet
  const liar: LoginWallet = { getPublicKey: wallet.getPublicKey, createSignature: other.createSignature }
  await assert.rejects(() => signPortalLogin(liar, challenge(), origin), /did not verify/)
})

test('same substrate choice as the driver page', () => {
  assert.equal(walletSubstrate({ CWI: {} }), 'window.CWI')
  assert.equal(walletSubstrate({}), 'auto')
  assert.equal(walletSubstrate(null), 'auto')
})

// Byte-for-byte parity with the driver page's own signPortalLogin. Needs
// frontend/driver/node_modules (CI installs it in the EV app job).
const driverModel = new URL('../../../../frontend/driver/portal-model.js', import.meta.url)
const driverReady = existsSync(new URL('../../../../frontend/driver/node_modules/@bsv/sdk/package.json', import.meta.url))
test('createSignature arguments and proof match frontend/driver/portal-model.js', { skip: !driverReady && process.env.CI === undefined && 'run npm ci in frontend/driver' }, async () => {
  const driver = await import(driverModel.href) as {
    signPortalLogin: (w: LoginWallet, c: Challenge, o: string) => Promise<unknown>, loginProtocol: unknown, loginScope: unknown
  }
  assert.deepEqual(driver.loginProtocol, loginProtocol)
  assert.equal(driver.loginScope, loginScope)
  const c = challenge()
  const a = recordingWallet(31)
  const b = recordingWallet(31)
  const ours = await signPortalLogin(a.wallet, c, origin)
  const theirs = await driver.signPortalLogin(b.wallet, c, origin)
  assert.deepEqual(a.requests, b.requests)
  assert.deepEqual(ours, theirs) // RFC 6979 deterministic signatures: identical proof
})
