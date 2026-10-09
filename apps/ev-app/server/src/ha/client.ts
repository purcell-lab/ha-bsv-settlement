// Read-only Home Assistant state reader.
//
// The ONLY request this module can make is `GET <HA_URL>/api/states/<entity_id>`
// for an entity id in the configured allowlist. There is deliberately no code
// path for /api/services, websocket service calls, POST, or any other write.
// The bearer token is attached to that one request and is never logged, returned
// or embedded in an error message.
import type { HaConfig } from './config.js'

export interface HaState {
  entity_id: string
  state: string
  attributes: Record<string, unknown>
  last_updated: string | null
}

export type HaErrorCode =
  | 'not_allowlisted' | 'timeout' | 'oversize' | 'redirect' | 'http_status'
  | 'not_found' | 'unauthorized' | 'bad_content_type' | 'bad_json' | 'bad_shape' | 'network'

export class HaError extends Error {
  readonly code: HaErrorCode
  readonly entityId: string
  constructor (code: HaErrorCode, entityId: string) {
    // Message is built only from the fixed code and the (allowlisted) entity id.
    super(`home assistant read failed: ${code} (${entityId})`)
    this.name = 'HaError'
    this.code = code
    this.entityId = entityId
  }
}

export interface Logger { warn: (message: string) => void }

type FetchLike = typeof fetch

export class HaStateReader {
  private readonly config: HaConfig
  private readonly allowlist: ReadonlySet<string>
  private readonly fetchImpl: FetchLike
  private readonly logger: Logger

  constructor (config: HaConfig, opts: { fetchImpl?: FetchLike, logger?: Logger } = {}) {
    this.config = config
    this.allowlist = new Set(Object.values(config.entities))
    this.fetchImpl = opts.fetchImpl ?? fetch
    this.logger = opts.logger ?? { warn: (m) => { console.warn(m) } }
  }

  isAllowed (entityId: string): boolean {
    return this.allowlist.has(entityId)
  }

  async getState (entityId: string): Promise<HaState> {
    try {
      return await this.read(entityId)
    } catch (error) {
      const failure = error instanceof HaError ? error : new HaError('network', entityId)
      this.logger.warn(failure.message) // code + entity id only, never the token/headers
      throw failure
    }
  }

  private async read (entityId: string): Promise<HaState> {
    if (!this.allowlist.has(entityId)) throw new HaError('not_allowlisted', entityId)
    const url = `${this.config.baseUrl}/api/states/${encodeURIComponent(entityId)}`
    const controller = new AbortController()
    let timedOut = false
    const timer = setTimeout(() => { timedOut = true; controller.abort() }, this.config.timeoutMs)
    try {
      let response: Response
      try {
        response = await this.fetchImpl(url, {
          method: 'GET',
          headers: { authorization: `Bearer ${this.config.token}`, accept: 'application/json' },
          redirect: 'manual',
          signal: controller.signal
        })
      } catch {
        throw new HaError(timedOut ? 'timeout' : 'network', entityId)
      }
      if (response.status >= 300 && response.status < 400) { await discard(response); throw new HaError('redirect', entityId) }
      if (response.type === 'opaqueredirect') throw new HaError('redirect', entityId)
      if (response.status === 401 || response.status === 403) { await discard(response); throw new HaError('unauthorized', entityId) }
      if (response.status === 404) { await discard(response); throw new HaError('not_found', entityId) }
      if (response.status !== 200) { await discard(response); throw new HaError('http_status', entityId) }
      const type = response.headers.get('content-type') ?? ''
      if (!/^application\/json(\s*;|$)/i.test(type)) { await discard(response); throw new HaError('bad_content_type', entityId) }
      let bytes: Uint8Array
      try {
        bytes = await readCapped(response, this.config.maxResponseBytes, entityId)
      } catch (error) {
        if (error instanceof HaError) throw error
        throw new HaError(timedOut ? 'timeout' : 'network', entityId)
      }
      return parseState(bytes, entityId)
    } finally {
      clearTimeout(timer)
    }
  }
}

async function discard (response: Response): Promise<void> {
  try { await response.body?.cancel() } catch { /* ignore */ }
}

async function readCapped (response: Response, cap: number, entityId: string): Promise<Uint8Array> {
  const declared = response.headers.get('content-length')
  if (declared !== null && /^\d+$/.test(declared) && Number(declared) > cap) {
    await discard(response)
    throw new HaError('oversize', entityId)
  }
  if (response.body === null) return new Uint8Array()
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let total = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    total += value.byteLength
    if (total > cap) {
      try { await reader.cancel() } catch { /* ignore */ }
      throw new HaError('oversize', entityId)
    }
    chunks.push(value)
  }
  const out = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) { out.set(chunk, offset); offset += chunk.byteLength }
  return out
}

function isPlainObject (value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function parseState (bytes: Uint8Array, entityId: string): HaState {
  let parsed: unknown
  try {
    parsed = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  } catch {
    throw new HaError('bad_json', entityId)
  }
  if (!isPlainObject(parsed) || parsed.entity_id !== entityId || typeof parsed.state !== 'string' ||
      !isPlainObject(parsed.attributes)) {
    throw new HaError('bad_shape', entityId)
  }
  return {
    entity_id: entityId,
    state: parsed.state,
    attributes: parsed.attributes,
    last_updated: typeof parsed.last_updated === 'string' ? parsed.last_updated : null
  }
}
