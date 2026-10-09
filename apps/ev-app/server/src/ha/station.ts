// Public station status built from allowlisted HA sensor states.
// Unknown / unavailable / unreadable values are ALWAYS null with a reason —
// never coerced to 0. Amounts here are provisional display values, not bills.
import { HaError, type HaState, type HaStateReader } from './client.js'
import type { EntityMap, EntityRole } from './config.js'

export interface Reading<T extends number | string> {
  value: T | null
  unit: string | null
  reason: string | null // why value is null; null when value is present
  last_updated: string | null
}

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

const NULL_STATES: Record<string, string> = { unknown: 'unknown', unavailable: 'unavailable', none: 'empty', '': 'empty' }
const NUMBER = /^-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][-+]?\d+)?$/

function unitOf (state: HaState): string | null {
  const unit = state.attributes.unit_of_measurement
  return typeof unit === 'string' && unit.length <= 32 ? unit : null
}

function cleanText (value: string): string {
  // eslint-disable-next-line no-control-regex
  return value.replace(/[\u0000-\u001f\u007f]/g, '').slice(0, 128)
}

export function toReading (state: HaState | HaError, kind: 'number'): Reading<number>
export function toReading (state: HaState | HaError, kind: 'text'): Reading<string>
export function toReading (state: HaState | HaError, kind: 'number' | 'text'): Reading<number> | Reading<string> {
  if (state instanceof HaError) {
    return { value: null, unit: null, reason: `ha_${state.code}`, last_updated: null }
  }
  const unit = unitOf(state)
  const raw = state.state.trim()
  const nullReason = NULL_STATES[raw.toLowerCase()]
  if (nullReason !== undefined) return { value: null, unit, reason: nullReason, last_updated: state.last_updated }
  if (kind === 'number') {
    if (!NUMBER.test(raw)) return { value: null, unit, reason: 'not_numeric', last_updated: state.last_updated }
    const value = Number(raw)
    if (!Number.isFinite(value)) return { value: null, unit, reason: 'not_numeric', last_updated: state.last_updated }
    return { value, unit, reason: null, last_updated: state.last_updated }
  }
  return { value: cleanText(raw), unit, reason: null, last_updated: state.last_updated }
}

export const STATION_ROLES: EntityRole[] = [
  'recorderStatus', 'provisionalCost', 'importEnergy', 'exportEnergy', 'proxyTransactionId',
  'satoshisPerAud', 'importPrice', 'exportPrice', 'ocppShadowLifecycle'
]

export async function readStation (reader: HaStateReader, entities: EntityMap, now = new Date()): Promise<StationStatus> {
  const results = await Promise.all(STATION_ROLES.map(async role => {
    try {
      return [role, await reader.getState(entities[role])] as const
    } catch (error) {
      return [role, error instanceof HaError ? error : new HaError('network', entities[role])] as const
    }
  }))
  const got = Object.fromEntries(results) as Record<string, HaState | HaError>
  return {
    generated_at: now.toISOString(),
    recorder: { status: toReading(got.recorderStatus, 'text') },
    session: {
      transaction_id: toReading(got.proxyTransactionId, 'text'),
      import_energy: toReading(got.importEnergy, 'number'),
      export_energy: toReading(got.exportEnergy, 'number'),
      provisional_cost: {
        ...toReading(got.provisionalCost, 'number'),
        label: 'Provisional estimate while charging. Not a bill or a payment request.'
      }
    },
    prices: {
      import: toReading(got.importPrice, 'number'),
      export: toReading(got.exportPrice, 'number'),
      label: 'Current tariff prices reported by Home Assistant.'
    },
    conversion: {
      satoshis_per_aud: toReading(got.satoshisPerAud, 'number'),
      label: 'demonstration rate, not market FX'
    },
    ocpp_shadow: {
      lifecycle: toReading(got.ocppShadowLifecycle, 'text'),
      label: 'OCPP shadow only. Observed for comparison; not used for billing or charger control.'
    }
  }
}
