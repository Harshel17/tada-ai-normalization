import { describe, expect, it } from 'vitest'
import { ENTITIES, PROVIDERS } from '../api/types'
import { ENTITY_PLAIN, EXAMPLE_INFO, examplesFor, idLabel, PROVIDER_INFO } from './examples'
import { SCENARIOS } from './scenarios'

describe('plain-English metadata', () => {
  it('describes every example file with a friendly title and explanation (no bare filenames)', () => {
    for (const sc of SCENARIOS) {
      const info = EXAMPLE_INFO[sc.id]
      expect(info, sc.id).toBeDefined()
      expect(info!.title.length).toBeGreaterThan(8)
      expect(info!.title).not.toMatch(/\.json/)
      expect(info!.about.length).toBeGreaterThan(20)
    }
  })
  it('names the provider in each example title', () => {
    for (const sc of SCENARIOS) expect(EXAMPLE_INFO[sc.id]!.title).toContain(PROVIDER_INFO[sc.provider].name)
  })
  it('explains every provider and entity type', () => {
    for (const p of PROVIDERS) expect(PROVIDER_INFO[p.id].vocabulary).toBeTruthy()
    for (const e of ENTITIES) expect(ENTITY_PLAIN[e.id].what.length).toBeGreaterThan(20)
  })
  it('offers each provider exactly one example per kind of data', () => {
    for (const p of PROVIDERS) {
      const kinds = examplesFor(SCENARIOS, p.id).map((s) => s.entity).sort()
      expect(kinds).toEqual(['company', 'deduction', 'direct_deposit', 'employee', 'payroll'])
    }
  })
  it('uses the provider’s own word for the employer id', () => {
    expect(idLabel('prismhr', 'company_id')).toContain('client ID')
    expect(idLabel('isolved', 'company_id')).toContain('legal company ID')
    expect(idLabel('ukg-ready', 'employee_id')).toContain('account ID')
    expect(idLabel('prismhr', 'tenant_id')).toBe('Workspace name')
  })
  it('keeps technical vocabulary out of the labels a newcomer reads', () => {
    const words = [...Object.values(ENTITY_PLAIN).flatMap((e) => [e.title, e.what]), ...Object.values(EXAMPLE_INFO).flatMap((e) => [e.title, e.about])].join(' ')
    for (const jargon of ['tenant', 'fixture', 'canonical', 'normaliz', 'provenance', 'extras']) expect(words.toLowerCase()).not.toContain(jargon)
  })
})
