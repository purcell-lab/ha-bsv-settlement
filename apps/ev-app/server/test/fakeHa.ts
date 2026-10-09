// Minimal fake Home Assistant REST server for tests. Records every request so
// tests can prove only GET /api/states/<allowlisted> was ever called.
import http from 'node:http'
import type { AddressInfo } from 'node:net'

export interface FakeReply {
  status?: number
  body?: unknown // JSON-encoded unless raw is set
  raw?: string | Buffer
  headers?: Record<string, string>
  delayMs?: number
  chunked?: boolean
}

export interface RecordedRequest { method: string, url: string, authorization: string | undefined }

// Every request any fake HA instance received during the whole test run.
export const ALL_REQUESTS: RecordedRequest[] = []

export class FakeHa {
  readonly requests: RecordedRequest[] = []
  readonly replies = new Map<string, FakeReply>()
  private server: http.Server | null = null
  url = ''

  setState (entityId: string, state: string, attributes: Record<string, unknown> = {}): void {
    this.replies.set(entityId, {
      body: { entity_id: entityId, state, attributes, last_updated: '2026-10-09T00:00:00+00:00' }
    })
  }

  async start (): Promise<void> {
    this.server = http.createServer((req, res) => {
      const record = { method: req.method ?? '', url: req.url ?? '', authorization: req.headers.authorization }
      this.requests.push(record)
      ALL_REQUESTS.push(record)
      req.resume()
      const match = /^\/api\/states\/([^/?]+)$/.exec(req.url ?? '')
      const reply = req.method === 'GET' && match !== null ? this.replies.get(decodeURIComponent(match[1])) : undefined
      if (reply === undefined) {
        res.writeHead(404, { 'content-type': 'application/json' }).end('{"message":"Entity not found."}')
        return
      }
      const send = (): void => {
        const payload = reply.raw ?? JSON.stringify(reply.body)
        const headers: Record<string, string> = { 'content-type': 'application/json', ...reply.headers }
        if (reply.chunked === true) {
          res.writeHead(reply.status ?? 200, headers)
          const buf = Buffer.from(payload)
          for (let i = 0; i < buf.length; i += 4096) res.write(buf.subarray(i, i + 4096))
          res.end()
        } else {
          res.writeHead(reply.status ?? 200, headers).end(payload)
        }
      }
      if (reply.delayMs !== undefined) setTimeout(send, reply.delayMs).unref()
      else send()
    })
    await new Promise<void>(resolve => { this.server!.listen(0, '127.0.0.1', resolve) })
    const { port } = this.server.address() as AddressInfo
    this.url = `http://127.0.0.1:${port}`
  }

  async stop (): Promise<void> {
    this.server?.closeAllConnections()
    await new Promise<void>(resolve => { this.server?.close(() => { resolve() }) ?? resolve() })
  }
}
