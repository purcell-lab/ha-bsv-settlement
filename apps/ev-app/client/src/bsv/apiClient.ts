// One bounded client for every generated API request. It refuses redirects so
// proofs, identities, and future credentials never move to another network authority.
import { API_BASE_URL, API_ORIGIN } from './config.js'

const API_TIMEOUT_MS = 10_000
const MAX_API_REQUEST_BYTES = 1024 * 1024
const MAX_API_RESPONSE_BYTES = 1024 * 1024

function endpointUrl (path: string): string {
  if (!/^\/[A-Za-z0-9/_-]*$/.test(path) || path.includes('..')) {
    throw new TypeError('API endpoint must be a safe absolute path')
  }
  return API_BASE_URL + path
}

function contentLength (response: Response): number | undefined {
  // Fetch exposes decoded response bytes while some implementations retain the
  // encoded Content-Length. The streaming ceiling below remains authoritative.
  if (response.headers.get('content-encoding') !== null) return undefined
  const value = response.headers.get('content-length')
  if (value === null) return undefined
  if (!/^(?:0|[1-9]\d*)$/.test(value)) throw new Error('API response has an invalid Content-Length')
  const length = Number(value)
  if (!Number.isSafeInteger(length)) throw new Error('API response has an invalid Content-Length')
  return length
}

async function readBoundedBody (response: Response): Promise<Uint8Array> {
  const declared = contentLength(response)
  if (declared !== undefined && declared > MAX_API_RESPONSE_BYTES) {
    throw new Error('API response exceeds the byte limit')
  }
  if (response.body === null) return new Uint8Array()
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let total = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      total += value.byteLength
      if (total > MAX_API_RESPONSE_BYTES) {
        await reader.cancel()
        throw new Error('API response exceeds the byte limit')
      }
      chunks.push(value)
    }
  } finally {
    reader.releaseLock()
  }
  if (declared !== undefined && declared !== total) {
    throw new Error('API response length does not match Content-Length')
  }
  const body = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) { body.set(chunk, offset); offset += chunk.byteLength }
  return body
}

export async function apiFetch (path: string, init: RequestInit = {}): Promise<Response> {
  if (init.body != null && typeof init.body !== 'string') {
    throw new TypeError('API request body must be a string')
  }
  if (typeof init.body === 'string' && new TextEncoder().encode(init.body).byteLength > MAX_API_REQUEST_BYTES) {
    throw new Error('API request exceeds the byte limit')
  }
  const controller = new AbortController()
  const timer = setTimeout(() => { controller.abort() }, API_TIMEOUT_MS)
  try {
    const response = await fetch(endpointUrl(path), {
      ...init,
      credentials: 'omit',
      redirect: 'error',
      referrerPolicy: 'no-referrer',
      signal: controller.signal
    })
    if (response.redirected || (response.url !== '' && new URL(response.url).origin !== API_ORIGIN)) {
      throw new Error('API response changed network authority')
    }
    const body = await readBoundedBody(response)
    const headers = new Headers(response.headers)
    headers.delete('content-encoding')
    headers.delete('content-length')
    // ev-app fix: TS 6 DOM types need an ArrayBuffer-backed view (body is freshly allocated).
    return new Response(body.length === 0 ? null : (body as Uint8Array<ArrayBuffer>), {
      status: response.status,
      statusText: response.statusText,
      headers
    })
  } finally {
    clearTimeout(timer)
  }
}

export async function readApiJson (response: Response): Promise<unknown> {
  const bytes = new Uint8Array(await response.arrayBuffer())
  let text: string
  try {
    text = new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    throw new Error('API response is not valid UTF-8')
  }
  try {
    return JSON.parse(text)
  } catch {
    throw new Error('API response is not valid JSON')
  }
}
