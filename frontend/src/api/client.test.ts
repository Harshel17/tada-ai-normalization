import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockFetch } from '../test/utils'
import { ApiError, buildEnvelope, cleanContext, fetchFixtureIndex, getStatus, ingest, normalize } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('buildEnvelope', () => {
  it('inserts the payload text exactly as written, so decimal scale survives', () => {
    const payload = '{"annualPay": "100000.00", "salary": 3846.150, "n": 500.00}'
    const body = buildEnvelope({ tenant_id: 't', company_id: 'ACME' }, payload)
    expect(body).toContain(payload) // not parsed and re-serialised
    expect(JSON.parse(body).context).toEqual({ tenant_id: 't', company_id: 'ACME' })
  })
  it('drops empty and whitespace-only context values and trims the rest', () => {
    expect(cleanContext({ tenant_id: ' t ', company_id: '', employee_id: '   ' })).toEqual({ tenant_id: 't' })
  })
})

describe('normalize / ingest', () => {
  const ctx = { tenant_id: 't', company_id: 'ACME' }
  it('POSTs to the proxied route with provider and entity URL-encoded', async () => {
    const { calls } = mockFetch([{ match: /\/api\/normalize\//, reply: { body: { ok: true, issues: [], records: [], counts: {} } } }])
    await normalize('ukg-ready', 'direct_deposit', ctx, '{}')
    expect(calls[0]).toMatchObject({ url: '/api/normalize/ukg-ready/direct_deposit', method: 'POST' })
    expect(calls[0]?.body).toBe('{"context":{"tenant_id":"t","company_id":"ACME"},"payload":{}}')
  })
  it('returns a 422 body (it carries the issue list) instead of throwing', async () => {
    mockFetch([{ match: /ingest/, reply: { status: 422, body: { ok: false, issues: [{ code: 'missing_id' }], stored: {} } } }])
    const r = await ingest('prismhr', 'employee', ctx, '{}')
    expect(r.status).toBe(422)
    expect(r.body.ok).toBe(false)
  })
  it('throws ApiError with the server detail for 404 / 400 / 503', async () => {
    mockFetch([{ match: /normalize/, reply: { status: 404, body: { detail: "unknown provider 'x'" } } }])
    await expect(normalize('x', 'employee', ctx, '{}')).rejects.toMatchObject({ status: 404, message: "unknown provider 'x'" })
    mockFetch([{ match: /ingest/, reply: { status: 503, body: { detail: 'database unavailable' } } }])
    await expect(ingest('prismhr', 'employee', ctx, '{}')).rejects.toMatchObject({ status: 503, message: 'database unavailable' })
  })
  it('formats FastAPI validation errors from location and message only, never the submitted value', async () => {
    mockFetch([{ match: /normalize/, reply: { status: 422, body: { detail: [{ loc: ['context', 'tenant_id'], msg: 'Field required', input: '900-44-3333' }] } } }])
    const err = await normalize('prismhr', 'employee', ctx, '{}').catch((e: ApiError) => e)
    expect(err).toBeInstanceOf(ApiError)
    expect((err as ApiError).message).toBe('context.tenant_id: Field required')
    expect((err as ApiError).message).not.toContain('900')
  })
  it('turns a network failure into a clear message without leaking the request', async () => {
    mockFetch([{ match: /normalize/, reply: new TypeError('Failed to fetch') }])
    const err = await normalize('prismhr', 'employee', ctx, '{"ssn":"123-45-6789"}').catch((e: ApiError) => e)
    expect((err as ApiError).status).toBe(0)
    expect((err as ApiError).message).toMatch(/Cannot reach the API/)
    expect((err as ApiError).message).not.toContain('123-45')
  })
})

describe('getStatus / fixtures', () => {
  it('reports 404 without throwing (used by the isolation checks)', async () => {
    mockFetch([{ match: /tenants/, reply: { status: 404, body: { detail: 'nope' } } }])
    expect(await getStatus('/tenants/a/prismhr/companies/x')).toEqual({ status: 404, body: { detail: 'nope' } })
  })
  it('re-exports the example loader from the bundled module', async () => {
    const mod = await import('./fixtures')
    expect(fetchFixtureIndex).toBe(mod.fetchFixtureIndex)
  })
})

describe('buildFilesBody', () => {
  it('embeds file text unparsed and adds the confirmed entity, context and trace flag only when given', async () => {
    const { buildFilesBody } = await import('./client')
    const text = '{"annualPay": 100000.00}'
    const plain = JSON.parse(buildFilesBody([{ name: 'a.json', text }])) as Record<string, unknown>
    expect(Object.keys(plain)).toEqual(['files'])
    expect(buildFilesBody([{ name: 'a.json', text }])).toContain(text)
    const full = JSON.parse(buildFilesBody([{ name: 'a"b.json', text, entity: 'employee' }], { context: { tenant_id: 't', company_id: ' ' }, trace: true })) as {
      files: { name: string; entity: string }[]; context: Record<string, string>; trace: boolean
    }
    expect(full.files[0]).toMatchObject({ name: 'a"b.json', entity: 'employee' })
    expect(full.context).toEqual({ tenant_id: 't' }) // blank ids are dropped, never sent
    expect(full.trace).toBe(true)
    expect(buildFilesBody([{ name: 'a', text }], { context: { tenant_id: 't' } })).not.toContain('save')
  })
})

describe('buildFilesBody integrity flag', () => {
  it('sends "integrity": true only when asked for', async () => {
    const { buildFilesBody } = await import('./client')
    const files = [{ name: 'a.json', text: '{}' }]
    expect(JSON.parse(buildFilesBody(files, { context: { tenant_id: 't' } }))).not.toHaveProperty('integrity')
    expect(JSON.parse(buildFilesBody(files, { context: { tenant_id: 't' }, integrity: true }))).toMatchObject({ integrity: true })
    expect(JSON.parse(buildFilesBody(files, { trace: true, integrity: true }))).toMatchObject({ trace: true, integrity: true })
  })
})

