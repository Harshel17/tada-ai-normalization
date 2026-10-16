import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import type { ReactElement } from 'react'
import { vi } from 'vitest'
import type { CanonicalRecord, NormalizeResponse } from '../api/types'

export function renderWithClient(ui: ReactElement) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  return { client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) }
}

export interface Call {
  url: string
  method: string
  body?: string
}
type Reply = { status?: number; body?: unknown; text?: string } | Error
type Route = { match: RegExp | ((url: string, init?: RequestInit) => boolean); reply: Reply | ((call: Call) => Reply) }

/** Replaces global fetch with a router. Unmatched requests fail the test loudly. */
export function mockFetch(routes: Route[]) {
  const calls: Call[] = []
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const call: Call = { url, method: init?.method ?? 'GET', body: typeof init?.body === 'string' ? init.body : undefined }
    calls.push(call)
    const route = routes.find((r) => (r.match instanceof RegExp ? r.match.test(url) : r.match(url, init)))
    if (!route) throw new Error(`Unmocked fetch: ${call.method} ${url}`)
    const reply = typeof route.reply === 'function' ? route.reply(call) : route.reply
    if (reply instanceof Error) throw reply
    const status = reply.status ?? 200
    const text = reply.text ?? JSON.stringify(reply.body ?? null)
    return new Response(text, { status, headers: { 'content-type': 'application/json' } })
  })
  vi.stubGlobal('fetch', fn)
  return { calls, fn }
}

export function record(over: Partial<CanonicalRecord> = {}): CanonicalRecord {
  return {
    record_type: 'employee',
    provider: 'prismhr',
    tenant_id: 't',
    company_id: 'ACME',
    source_id: 'E1',
    extras: {},
    provenance: { source_endpoint: 'GET /employee/v1/getEmployee', source_path: '$.employee[0]', redacted_paths: [], derived_fields: [] },
    first_name: 'Jordan',
    last_name: 'Rivera',
    ...over,
  }
}

export function normalizeResponse(over: Partial<NormalizeResponse> = {}): NormalizeResponse {
  const records = over.records ?? [record()]
  return { provider: 'prismhr', entity_type: 'employee', ok: true, counts: { employee: records.length }, issues: [], records, ...over }
}

// --- real repository fixtures, for tests that need the genuine source text ------------------------
import { readdirSync, readFileSync } from 'node:fs'
import path from 'node:path'

const FIXTURE_ROOT = path.resolve(__dirname, '../../../tests/fixtures')
export const repoFixture = (dir: string, name: string): string => readFileSync(path.join(FIXTURE_ROOT, dir, name), 'utf8')

/** Serves the real fixture files the way the dev-server middleware does. */
export const fixtureRoute = {
  match: /__fixtures\/file\?dir=/,
  reply: (call: { url: string }): { text: string } => {
    const u = new URL(call.url, 'http://x')
    return { text: repoFixture(u.searchParams.get('dir')!, u.searchParams.get('name')!) }
  },
}

/** Real canonical output of every backend fixture run (the repository's regression snapshots). */
const SNAPSHOT_ROOT = path.resolve(__dirname, '../../../tests/snapshots/canonical')
export const snapshotNames = (): string[] => readdirSync(SNAPSHOT_ROOT).filter((n) => n.endsWith('.json')).map((n) => n.replace(/\.json$/, '')).sort()
export const repoSnapshot = (name: string): NormalizeResponse =>
  JSON.parse(readFileSync(path.join(SNAPSHOT_ROOT, `${name}.json`), 'utf8')) as NormalizeResponse

/** What the API reports as `counts`: records per record type (the snapshot files are the raw engine output). */
export const countsOf = (records: { record_type: string }[]): Record<string, number> =>
  records.reduce<Record<string, number>>((m, r) => ({ ...m, [r.record_type]: (m[r.record_type] ?? 0) + 1 }), {})
