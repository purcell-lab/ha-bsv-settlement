// Home Assistant adapter configuration. Read from the environment only.
// HA_TOKEN must belong to a dedicated, NON-ADMIN Home Assistant user. This app
// only ever reads /api/states/<allowlisted entity>; it never needs admin rights.
import { isIP } from 'node:net'

export const ENTITY_DEFAULTS = {
  recorderStatus: 'sensor.sigen_charging_sessions_recorder_status',
  provisionalCost: 'sensor.sigen_charging_sessions_provisional_session_cost',
  importEnergy: 'sensor.sigen_charging_sessions_session_import_energy',
  exportEnergy: 'sensor.sigen_charging_sessions_session_export_energy',
  proxyTransactionId: 'sensor.sigen_charging_sessions_proxy_transaction_id',
  satoshisPerAud: 'sensor.bsv_satoshis_per_aud',
  importPrice: 'sensor.amber_express_amber_general_price',
  exportPrice: 'sensor.amber_express_amber_feed_in_price',
  walletStatus: 'sensor.bsv_operator_wallet_mainnet_operator_wallet_status',
  ocppShadowLifecycle: 'sensor.ocpp_import_shadow_ocpp_session_lifecycle'
} as const

export type EntityRole = keyof typeof ENTITY_DEFAULTS
export type EntityMap = Record<EntityRole, string>

// Env var name per role, e.g. HA_ENTITY_RECORDER_STATUS.
export const ENTITY_ENV: Record<EntityRole, string> = {
  recorderStatus: 'HA_ENTITY_RECORDER_STATUS',
  provisionalCost: 'HA_ENTITY_PROVISIONAL_COST',
  importEnergy: 'HA_ENTITY_IMPORT_ENERGY',
  exportEnergy: 'HA_ENTITY_EXPORT_ENERGY',
  proxyTransactionId: 'HA_ENTITY_PROXY_TRANSACTION_ID',
  satoshisPerAud: 'HA_ENTITY_SATOSHIS_PER_AUD',
  importPrice: 'HA_ENTITY_IMPORT_PRICE',
  exportPrice: 'HA_ENTITY_EXPORT_PRICE',
  walletStatus: 'HA_ENTITY_WALLET_STATUS',
  ocppShadowLifecycle: 'HA_ENTITY_OCPP_SHADOW_LIFECYCLE'
}

export interface HaConfig {
  baseUrl: string // origin + optional base path, no trailing slash
  token: string
  walletEntryId: string | null
  entities: EntityMap
  timeoutMs: number
  maxResponseBytes: number
}

const ENTITY_ID = /^[a-z_]+\.[a-z0-9_]{1,200}$/

function intEnv (env: NodeJS.ProcessEnv, name: string, fallback: number, min: number, max: number): number {
  const text = env[name]
  if (text == null || text === '') return fallback
  if (!/^\d{1,9}$/.test(text)) throw new Error(`${name} must be an integer`)
  const value = Number(text)
  if (value < min || value > max) throw new Error(`${name} must be between ${min} and ${max}`)
  return value
}

/** True for hosts that are plainly on a local network (never public DNS names). */
export function isLanHost (hostname: string): boolean {
  const host = hostname.replace(/^\[|\]$/g, '').toLowerCase()
  const family = isIP(host)
  if (family === 4) {
    const [a, b] = host.split('.').map(Number)
    return a === 10 || a === 127 || (a === 192 && b === 168) || (a === 172 && b >= 16 && b <= 31) ||
      (a === 169 && b === 254)
  }
  if (family === 6) {
    return host === '::1' || /^f[cd][0-9a-f]{2}:/.test(host) || /^fe[89ab][0-9a-f]:/.test(host)
  }
  if (host === 'localhost') return true
  if (!host.includes('.')) return true // single-label LAN name, e.g. "homeassistant"
  return /\.(local|lan|home\.arpa|internal)$/.test(host)
}

export function loadHaConfig (env: NodeJS.ProcessEnv = process.env): HaConfig {
  const urlText = env.HA_URL
  if (urlText == null || urlText === '') throw new Error('HA_URL is required')
  let url: URL
  try { url = new URL(urlText) } catch { throw new Error('HA_URL is not a valid URL') }
  if (url.username !== '' || url.password !== '' || url.search !== '' || url.hash !== '') {
    throw new Error('HA_URL must not contain credentials, a query or a fragment')
  }
  if (url.protocol === 'http:') {
    if (env.HA_ALLOW_INSECURE_LAN !== '1') throw new Error('HA_URL must use https (set HA_ALLOW_INSECURE_LAN=1 for a LAN http URL)')
    if (!isLanHost(url.hostname)) throw new Error('HA_ALLOW_INSECURE_LAN only permits http to LAN hosts')
  } else if (url.protocol !== 'https:') {
    throw new Error('HA_URL must use https')
  }

  const token = env.HA_TOKEN
  // Never echo the token in any error message.
  if (token == null || token === '') throw new Error('HA_TOKEN is required')
  if (!/^[\x21-\x7e]{16,4096}$/.test(token)) throw new Error('HA_TOKEN has an invalid format')

  const entryId = env.HA_WALLET_ENTRY_ID
  if (entryId != null && entryId !== '' && !/^[A-Za-z0-9_-]{1,64}$/.test(entryId)) {
    throw new Error('HA_WALLET_ENTRY_ID has an invalid format')
  }

  const entities = {} as EntityMap
  for (const role of Object.keys(ENTITY_DEFAULTS) as EntityRole[]) {
    const value = env[ENTITY_ENV[role]] ?? ENTITY_DEFAULTS[role]
    if (!ENTITY_ID.test(value)) throw new Error(`${ENTITY_ENV[role]} is not a valid entity id`)
    entities[role] = value
  }

  return {
    baseUrl: url.href.replace(/\/+$/, ''),
    token,
    walletEntryId: entryId == null || entryId === '' ? null : entryId,
    entities,
    timeoutMs: intEnv(env, 'HA_TIMEOUT_MS', 5000, 100, 60_000),
    maxResponseBytes: intEnv(env, 'HA_MAX_RESPONSE_BYTES', 512 * 1024, 1024, 8 * 1024 * 1024)
  }
}
