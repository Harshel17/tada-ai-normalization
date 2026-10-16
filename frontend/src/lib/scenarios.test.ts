import { existsSync } from 'node:fs'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import { missingContext } from './context'
import { SCENARIOS, scenarioCompany } from './scenarios'

const FIXTURES = path.resolve(__dirname, '../../../tests/fixtures')

describe('scenario definitions', () => {
  it('covers all 15 provider × entity combinations exactly once', () => {
    const keys = SCENARIOS.map((s) => `${s.provider}/${s.entity}`)
    expect(new Set(keys).size).toBe(15)
    expect(SCENARIOS).toHaveLength(15)
  })
  it('has unique ids', () => expect(new Set(SCENARIOS.map((s) => s.id)).size).toBe(SCENARIOS.length))
  it.each(SCENARIOS.map((s) => [s.id, s] as const))('%s points at a fixture that exists in the repository', (_id, s) => {
    expect(existsSync(path.join(FIXTURES, s.fixture.dir, s.fixture.name))).toBe(true)
  })
  it.each(SCENARIOS.map((s) => [s.id, s] as const))('%s supplies all required context', (_id, s) => {
    expect(missingContext(s.provider, s.entity, { tenant_id: 't', ...s.context })).toEqual([])
  })
  it('read-back paths are tenant- and provider-scoped', () => {
    for (const s of SCENARIOS) {
      const p = s.readback.path({ provider: s.provider, tenant: 'T 1', company: scenarioCompany(s) })
      expect(p).toContain(`/tenants/T%201/${encodeURIComponent(s.provider)}/`)
    }
  })
})
