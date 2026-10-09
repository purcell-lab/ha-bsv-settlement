// Fetch the configured API's identity public key once and cache it.
// This is endpoint/TLS trust, not independent key authentication. Pass a pinned key to
// the login/signed-request hooks when the application requires identity continuity.
import { PublicKey } from '@bsv/sdk'
import { apiFetch, readApiJson } from './apiClient.js'

let cached: string | null = null
let pending: Promise<string> | null = null

export function requireIdentityKey (value: unknown): string {
  if (typeof value !== 'string' || !/^(?:02|03)[0-9a-f]{64}$/.test(value)) {
    throw new Error('server returned an invalid identity key')
  }
  try {
    if (PublicKey.fromString(value).toString() !== value) throw new Error()
  } catch {
    throw new Error('server returned an invalid identity key')
  }
  return value
}

export async function readIdentityKeyResponse (response: Response): Promise<string> {
  const parsed = await readApiJson(response)
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed) ||
      (Object.getPrototypeOf(parsed) !== Object.prototype && Object.getPrototypeOf(parsed) !== null) ||
      Object.getOwnPropertySymbols(parsed).length !== 0) {
    throw new Error('server returned an invalid identity response')
  }
  const descriptors = Object.getOwnPropertyDescriptors(parsed)
  if (Object.keys(descriptors).length !== 1 || !Object.prototype.hasOwnProperty.call(descriptors, 'identityKey') ||
      Object.values(descriptors).some(property => property.get != null || property.set != null)) {
    throw new Error('server returned an invalid identity response')
  }
  return requireIdentityKey(descriptors.identityKey?.value)
}

export async function getServerIdentity (endpoint = '/api/identity'): Promise<string> {
  if (cached !== null) return cached
  pending ??= (async () => {
    const res = await apiFetch(endpoint)
    if (!res.ok) throw new Error('failed to fetch server identity: ' + String(res.status))
    const identityKey = await readIdentityKeyResponse(res)
    cached = identityKey
    return identityKey
  })()
  try {
    return await pending
  } finally {
    pending = null
  }
}
