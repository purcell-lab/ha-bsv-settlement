// Read-only API calls for the driver page. Uses the generated bounded,
// redirect-free apiClient. Nothing here creates transactions or payment
// signatures: the only wallet signature is the BRC-103 login proof.
import type { WalletInterface } from '@bsv/sdk'
import { apiFetch, readApiJson } from '../bsv/apiClient.js'
import { createAuthProof } from '../bsv/auth.js'
import { getServerIdentity, requireIdentityKey } from '../bsv/serverIdentity.js'
import type { Reading } from './format.js'

export interface StationStatus {
  generated_at: string
  recorder: { status: Reading<string> }
  session: {
    transaction_id: Reading<string>
    import_energy: Reading<number>
    export_energy: Reading<number>
    provisional_cost: Reading<number> & { label: string }
  }
  prices: { import: Reading<number>, export: Reading<number>, label: string }
  conversion: { satoshis_per_aud: Reading<number>, label: string }
  ocpp_shadow: { lifecycle: Reading<string>, label: string }
}

export interface CreditRow {
  source: 'ongoing_credit' | 'automatic_credit'
  session_id: string | null
  state: string | null
  amount_sats: number | null
  fee_sats: number | null
  net_amount_aud: number | null
  txid: string | null
  confirmations: number | null
  created_at: string | null
  wallet_receipt_status: string | null
}

export interface MyCredits {
  identity_key: string
  generated_at: string
  credits: CreditRow[] | null
  reason: string | null
  note: string
}

export interface LoginSession { identityKey: string, sessionToken: string, expiresAt: number }

function isObject (v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

export async function fetchStation (): Promise<StationStatus> {
  const res = await apiFetch('/api/station')
  if (!res.ok) throw new Error('station status request failed: ' + String(res.status))
  const body = await readApiJson(res)
  if (!isObject(body) || !isObject(body.session) || !isObject(body.recorder)) throw new Error('invalid station response')
  return body as unknown as StationStatus
}

export async function signIn (wallet: WalletInterface, expectedIdentity: string | null): Promise<LoginSession> {
  const counterparty = requireIdentityKey(await getServerIdentity())
  const proof = await createAuthProof(wallet, { counterparty, action: 'login' })
  const res = await apiFetch('/api/login', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(proof)
  })
  if (!res.ok) throw new Error('sign-in failed: ' + String(res.status))
  const body = await readApiJson(res)
  if (!isObject(body) || typeof body.sessionToken !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(body.sessionToken) ||
      typeof body.expiresAt !== 'number') {
    throw new Error('invalid sign-in response')
  }
  const identityKey = requireIdentityKey(body.identityKey)
  if (expectedIdentity !== null && identityKey !== expectedIdentity) throw new Error('server returned another wallet identity')
  return { identityKey, sessionToken: body.sessionToken, expiresAt: body.expiresAt }
}

export async function fetchMyCredits (session: LoginSession): Promise<MyCredits> {
  const res = await apiFetch('/api/me/credits', { headers: { authorization: `Bearer ${session.sessionToken}` } })
  if (res.status === 401) throw new Error('Your sign-in has expired. Sign in again.')
  if (!res.ok) throw new Error('credits request failed: ' + String(res.status))
  const body = await readApiJson(res)
  if (!isObject(body) || body.identity_key !== session.identityKey || !(body.credits === null || Array.isArray(body.credits))) {
    throw new Error('invalid credits response')
  }
  return body as unknown as MyCredits
}
