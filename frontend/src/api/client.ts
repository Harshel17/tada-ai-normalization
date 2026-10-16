import type {
  CanonicalRecord,
  ConvertResponse,
  DetectResponse,
  HealthResponse,
  IngestResponse,
  MigrationPreview,
  MigrationRequestBody,
  NormalizeResponse,
  Scope,
  SourceContext,
  StatsResponse,
} from './types'
import { ApiError } from './errors'

/** All backend calls go through the Vite proxy: /api/health -> FastAPI /health. */
export const API_BASE = '/api'

export { ApiError }

function detailOf(status: number, body: unknown): string {
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // FastAPI validation errors: keep location + message only, never the submitted value
      return detail
        .map((d) => {
          const e = d as { loc?: unknown[]; msg?: string }
          return `${(e.loc ?? []).join('.')}: ${e.msg ?? 'invalid'}`
        })
        .join('; ')
    }
  }
  return `HTTP ${status}`
}

async function parse(res: Response): Promise<unknown> {
  const text = await res.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

async function send(path: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}${path}`, init)
  } catch {
    throw new ApiError(0, 'Cannot reach the API. Is the FastAPI server running?')
  }
}

export async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  const res = await send(path, { signal })
  const body = await parse(res)
  if (!res.ok) throw new ApiError(res.status, detailOf(res.status, body))
  return body as T
}

/** GET that reports the status instead of throwing on 404 (used by the isolation checks). */
export async function getStatus(path: string): Promise<{ status: number; body: unknown }> {
  const res = await send(path)
  return { status: res.status, body: await parse(res) }
}

export const enc = encodeURIComponent

/** Context without empty strings (the backend rejects unknown keys but accepts missing ones). */
export function cleanContext(ctx: SourceContext): SourceContext {
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(ctx)) {
    const trimmed = typeof v === 'string' ? v.trim() : ''
    if (trimmed) out[k] = trimmed
  }
  return out as unknown as SourceContext
}

/**
 * Builds the request body. The payload text is inserted AS WRITTEN (not parsed and re-serialised),
 * so numbers such as 100000.00 keep their scale: the backend parses them as Decimal.
 */
export function buildEnvelope(ctx: SourceContext, rawPayload: string): string {
  return `{"context":${JSON.stringify(cleanContext(ctx))},"payload":${rawPayload}}`
}

export interface PostResult<T> {
  status: number
  body: T
}

async function post<T>(
  kind: 'normalize' | 'ingest',
  provider: string,
  entity: string,
  ctx: SourceContext,
  rawPayload: string,
): Promise<PostResult<T>> {
  const res = await send(`/${kind}/${enc(provider)}/${enc(entity)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: buildEnvelope(ctx, rawPayload),
  })
  const body = await parse(res)
  // 200 = ok, 422 = "your payload had errors" and the body still carries the issue list
  if (res.status === 200 || (res.status === 422 && body && typeof body === 'object' && 'issues' in body)) {
    return { status: res.status, body: body as T }
  }
  throw new ApiError(res.status, detailOf(res.status, body))
}

export const normalize = (p: string, e: string, c: SourceContext, raw: string) =>
  post<NormalizeResponse>('normalize', p, e, c, raw)

export const ingest = (p: string, e: string, c: SourceContext, raw: string) =>
  post<IngestResponse>('ingest', p, e, c, raw)

export const fetchHealth = (signal?: AbortSignal) => getJson<HealthResponse>('/health', signal)
export const fetchStats = (signal?: AbortSignal) => getJson<StatsResponse>('/stats', signal)

// --- stored data (read endpoints) ----------------------------------------------------------------
export const companyBase = (s: Scope) =>
  `/tenants/${enc(s.tenant)}/${enc(s.provider)}/companies/${enc(s.company)}`

const page = (limit: number, offset: number) => `limit=${limit}&offset=${offset}`

export const fetchCompany = (s: Scope) => getJson<CanonicalRecord>(companyBase(s))
export const fetchEmployees = (s: Scope, limit = 25, offset = 0) =>
  getJson<CanonicalRecord[]>(`${companyBase(s)}/employees?${page(limit, offset)}`)
export const fetchEmployee = (s: Scope, id: string) =>
  getJson<CanonicalRecord>(`${companyBase(s)}/employees/${enc(id)}`)
export const fetchDeposits = (s: Scope, id: string) =>
  getJson<CanonicalRecord[]>(`${companyBase(s)}/employees/${enc(id)}/direct-deposits`)
export const fetchDeductions = (s: Scope, id: string) =>
  getJson<CanonicalRecord[]>(`${companyBase(s)}/employees/${enc(id)}/deductions`)
export const fetchEmployeePaychecks = (s: Scope, id: string) =>
  getJson<CanonicalRecord[]>(`${companyBase(s)}/employees/${enc(id)}/paychecks?${page(50, 0)}`)
export const fetchRuns = (s: Scope, limit = 25, offset = 0) =>
  getJson<CanonicalRecord[]>(`${companyBase(s)}/payroll-runs?${page(limit, offset)}`)
export const fetchRunPaychecks = (s: Scope, runId: string) =>
  getJson<CanonicalRecord[]>(`${companyBase(s)}/payroll-runs/${enc(runId)}/paychecks?${page(100, 0)}`)
export const fetchPaycheck = (s: Scope, id: string) =>
  getJson<CanonicalRecord>(`${companyBase(s)}/paychecks/${enc(id)}`)

// --- bundled example files: see ./fixtures (compiled into the app; the same in dev and in a production build) -----
export { fetchFixtureIndex, fetchFixtureText } from './fixtures'
export type { FixtureRef } from './fixtures'

// --- detect / convert (preview only; saving stays a separate call to /ingest) ---------------------
export interface FileToSend {
  name: string
  /** The file's text, which must already be valid JSON. It is inserted AS WRITTEN. */
  text: string
  /** The user's confirmation when detection could not decide. */
  entity?: string
}

/** Body for /detect and /convert. File texts are embedded unparsed so number scale (100000.00) survives. */
export function buildFilesBody(files: FileToSend[], extra: { context?: SourceContext; trace?: boolean; integrity?: boolean } = {}): string {
  const items = files.map((f) => `{"name":${JSON.stringify(f.name)},"content":${f.text}${f.entity ? `,"entity":${JSON.stringify(f.entity)}` : ''}}`)
  const parts = [`"files":[${items.join(',')}]`]
  if (extra.context) parts.push(`"context":${JSON.stringify(cleanContext(extra.context))}`)
  if (extra.trace) parts.push('"trace":true')
  if (extra.integrity) parts.push('"integrity":true')
  return `{${parts.join(',')}}`
}

async function postFiles<T>(path: string, body: string): Promise<T> {
  const res = await send(path, { method: 'POST', headers: { 'content-type': 'application/json' }, body })
  const parsed = await parse(res)
  if (!res.ok) throw new ApiError(res.status, detailOf(res.status, parsed))
  return parsed as T
}

export const detectFiles = (provider: string, files: FileToSend[]) =>
  postFiles<DetectResponse>(`/detect/${enc(provider)}`, buildFilesBody(files))

export const convertFiles = (provider: string, files: FileToSend[], context: SourceContext) =>
  postFiles<ConvertResponse>(`/convert/${enc(provider)}`, buildFilesBody(files, { context, trace: true, integrity: true }))

// --- migration preview (read-only dry run; the server never contacts the destination) ---------------
export async function previewMigration(body: MigrationRequestBody): Promise<MigrationPreview> {
  const res = await send('/migrate/preview/ukg-ready', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  const parsed = await parse(res)
  if (!res.ok) throw new ApiError(res.status, detailOf(res.status, parsed))
  return parsed as MigrationPreview
}
