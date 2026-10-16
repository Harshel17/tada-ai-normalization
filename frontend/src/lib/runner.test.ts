import { afterEach, describe, expect, it, vi } from 'vitest'
import { mockFetch } from '../test/utils'
import { runScenario } from './runner'
import type { StepResult } from './runner'
import { SCENARIOS } from './scenarios'

afterEach(() => vi.unstubAllGlobals())

const sc = SCENARIOS.find((s) => s.id === 'prismhr-employee')!
const stored = (inserted: number, unchanged: number) => ({ ok: true, counts: { employee: 2 }, stored: { employee: { inserted, updated: 0, unchanged } }, issues: [] })

/** A backend that behaves correctly: first save inserts, second reports unchanged, other tenants see nothing. */
function healthyBackend() {
  let saves = 0
  return mockFetch([
    { match: /__fixtures\/file/, reply: { text: '{"employee":[]}' } },
    { match: /\/api\/normalize\//, reply: { body: { ok: true, counts: { employee: 2 }, issues: [], records: [] } } },
    { match: /\/api\/ingest\//, reply: () => ({ body: saves++ === 0 ? stored(2, 0) : stored(0, 2) }) },
    { match: (u) => u.includes('-other/') || u.includes('/isolved/'), reply: { body: [] } },
    { match: /\/employees$/, reply: { body: [{}, {}] } },
  ])
}

describe('runScenario derives every verdict from the responses', () => {
  it('passes all seven steps against a correct backend, and really called it', async () => {
    const { calls } = healthyBackend()
    const seen: StepResult[][] = []
    const steps = await runScenario(sc, 'tenantX', { onStep: (s) => seen.push(s) })
    expect(steps.map((s) => s.state)).toEqual(Array(7).fill('pass'))
    expect(calls.filter((c) => c.method === 'POST').map((c) => c.url)).toEqual([
      '/api/normalize/prismhr/employee', '/api/ingest/prismhr/employee', '/api/ingest/prismhr/employee',
    ])
    expect(seen.some((s) => s.some((x) => x.state === 'running'))).toBe(true) // progress was reported live
    expect(steps[4]?.detail).toContain('2 items')
  })

  it('FAILS the idempotency step when the second save writes again', async () => {
    mockFetch([
      { match: /__fixtures\/file/, reply: { text: '{}' } },
      { match: /normalize/, reply: { body: { ok: true, counts: { employee: 2 }, issues: [], records: [] } } },
      { match: /ingest/, reply: { body: stored(2, 0) } }, // always "inserted": wrong the second time
      { match: /.*/, reply: { body: [] } },
    ])
    const steps = await runScenario(sc, 't', { onStep: () => {} })
    expect(steps.find((s) => s.id === 'ingest2')?.state).toBe('fail')
    expect(steps.find((s) => s.id === 'readback')?.state).toBe('skipped') // later steps are never faked as passed
  })

  it('FAILS normalize when the record counts differ from what the scenario expects', async () => {
    mockFetch([
      { match: /__fixtures\/file/, reply: { text: '{}' } },
      { match: /normalize/, reply: { body: { ok: true, counts: { employee: 1 }, issues: [], records: [] } } },
    ])
    const steps = await runScenario(sc, 't', { onStep: () => {} })
    expect(steps[1]).toMatchObject({ id: 'normalize', state: 'fail' })
    expect(steps[1]?.detail).toContain('1 employee')
    expect(steps.slice(2).every((s) => s.state === 'skipped')).toBe(true)
  })

  it('FAILS isolation when another tenant can read the data', async () => {
    let n = 0
    mockFetch([
      { match: /__fixtures\/file/, reply: { text: '{}' } },
      { match: /normalize/, reply: { body: { ok: true, counts: { employee: 2 }, issues: [], records: [] } } },
      { match: /ingest/, reply: () => ({ body: n++ === 0 ? stored(2, 0) : stored(0, 2) }) },
      { match: /.*/, reply: { body: [{}, {}] } }, // leaks to every tenant
    ])
    const steps = await runScenario(sc, 't', { onStep: () => {} })
    expect(steps.find((s) => s.id === 'tenant')?.state).toBe('fail')
  })

  it('reports an unreachable API as an error, not a pass', async () => {
    mockFetch([
      { match: /__fixtures\/file/, reply: { text: '{}' } },
      { match: /normalize/, reply: new TypeError('offline') },
    ])
    const steps = await runScenario(sc, 't', { onStep: () => {} })
    expect(steps[1]?.state).toBe('error')
    expect(steps.some((s) => s.state === 'pass' && s.id !== 'fixture')).toBe(false)
  })

  it('stops at once when the fixture cannot be loaded', async () => {
    mockFetch([{ match: /__fixtures/, reply: { status: 404, body: {} } }])
    const steps = await runScenario(sc, 't', { onStep: () => {} })
    expect(steps[0]?.state).toBe('error')
    expect(steps.slice(1).every((s) => s.state === 'skipped')).toBe(true)
  })
})
