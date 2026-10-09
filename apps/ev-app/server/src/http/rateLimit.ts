// Simple per-client-IP token bucket. In-memory and per process (see README).
import type { Request, Response, NextFunction } from 'express'

export interface RateLimitOptions {
  capacity: number // burst size
  refillPerSecond: number
  maxClients?: number
  now?: () => number
}

export function rateLimit (opts: RateLimitOptions) {
  const buckets = new Map<string, { tokens: number, at: number }>()
  const maxClients = opts.maxClients ?? 10_000
  const now = opts.now ?? Date.now
  return (req: Request, res: Response, next: NextFunction): void => {
    const key = req.ip ?? req.socket.remoteAddress ?? 'unknown'
    const t = now()
    let bucket = buckets.get(key)
    if (bucket === undefined) {
      if (buckets.size >= maxClients) {
        // Drop refilled (idle) buckets first; if still full, refuse rather than grow unbounded.
        for (const [k, b] of buckets) {
          if (b.tokens + ((t - b.at) / 1000) * opts.refillPerSecond >= opts.capacity) buckets.delete(k)
        }
        if (buckets.size >= maxClients) { res.status(429).set('retry-after', '1').json({ error: 'rate limited' }); return }
      }
      bucket = { tokens: opts.capacity, at: t }
      buckets.set(key, bucket)
    }
    bucket.tokens = Math.min(opts.capacity, bucket.tokens + ((t - bucket.at) / 1000) * opts.refillPerSecond)
    bucket.at = t
    if (bucket.tokens < 1) {
      const wait = Math.max(1, Math.ceil((1 - bucket.tokens) / opts.refillPerSecond))
      res.status(429).set('retry-after', String(wait)).json({ error: 'rate limited' })
      return
    }
    bucket.tokens -= 1
    next()
  }
}
