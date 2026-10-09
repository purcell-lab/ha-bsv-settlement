// Per-identity credit projection from the operator wallet status sensor.
//
// Only rows provably belonging to the verified wallet identity are returned:
//  - ongoing_credit.sessions rows whose driver_identity equals the identity;
//  - automatic_credit.payments rows that either carry a matching driver_identity
//    or link (by receiving budget id, else recipient address) to one of the
//    identity's verified ongoing rows, and to no other driver's row.
// Anything without verifiable ownership is excluded. Output is a fixed minimal
// projection; raw attributes and recipient addresses are never returned.

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

const IDENTITY = /^0[23][0-9a-f]{64}$/

export function isIdentityKey (value: unknown): value is string {
  return typeof value === 'string' && IDENTITY.test(value)
}

function isObject (value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function rows (container: unknown, key: string): Array<Record<string, unknown>> {
  if (!isObject(container)) return []
  const list = container[key]
  return Array.isArray(list) ? list.filter(isObject) : []
}

function text (value: unknown, max: number): string | null {
  return typeof value === 'string' && value.length > 0 && value.length <= max ? value : null
}

function count (value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null
}

function finite (value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function project (row: Record<string, unknown>, source: CreditRow['source']): CreditRow {
  const txid = typeof row.txid === 'string' && /^[0-9a-f]{64}$/.test(row.txid) ? row.txid : null
  return {
    source,
    session_id: text(row.session_id, 128),
    state: text(row.state, 64),
    amount_sats: count(row.amount_sats),
    fee_sats: count(row.fee_sats),
    net_amount_aud: finite(row.net_amount_aud),
    txid,
    confirmations: count(row.confirmations),
    created_at: text(row.created_at, 64),
    wallet_receipt_status: text(row.wallet_receipt_status, 64)
  }
}

export function creditsForIdentity (attributes: Record<string, unknown>, identityKey: string): CreditRow[] {
  if (!isIdentityKey(identityKey)) return []
  const ongoing = rows(attributes.ongoing_credit, 'sessions')
  const automatic = rows(attributes.automatic_credit, 'payments')

  const mine: Array<Record<string, unknown>> = []
  const myBudgets = new Set<string>()
  const myAddresses = new Set<string>()
  const otherBudgets = new Set<string>()
  const otherAddresses = new Set<string>()
  for (const row of ongoing) {
    if (!isIdentityKey(row.driver_identity)) continue // unverifiable: excluded
    const budget = text(row.receiving_budget_id, 128)
    const address = text(row.recipient_address, 128)
    if (row.driver_identity === identityKey) {
      mine.push(row)
      if (budget !== null) myBudgets.add(budget)
      if (address !== null) myAddresses.add(address)
    } else {
      if (budget !== null) otherBudgets.add(budget)
      if (address !== null) otherAddresses.add(address)
    }
  }
  // A link claimed by more than one identity proves nothing.
  for (const b of otherBudgets) myBudgets.delete(b)
  for (const a of otherAddresses) myAddresses.delete(a)

  const result = mine.map(row => project(row, 'ongoing_credit'))
  const seenTxids = new Set(result.map(r => r.txid).filter((t): t is string => t !== null))
  for (const row of automatic) {
    let owned: boolean
    if ('driver_identity' in row) {
      owned = row.driver_identity === identityKey && isIdentityKey(row.driver_identity)
    } else {
      const budget = text(row.budget_id, 128)
      const address = text(row.recipient_address, 128)
      owned = (budget !== null && myBudgets.has(budget)) ||
        (budget === null && address !== null && myAddresses.has(address))
      // A row whose budget links to me but whose address belongs to someone else is inconsistent.
      if (owned && address !== null && otherAddresses.has(address)) owned = false
    }
    if (!owned) continue
    const projected = project(row, 'automatic_credit')
    if (projected.txid !== null && seenTxids.has(projected.txid)) continue
    if (projected.txid !== null) seenTxids.add(projected.txid)
    result.push(projected)
  }
  return result
}
