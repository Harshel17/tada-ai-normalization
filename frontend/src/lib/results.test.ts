import { describe, expect, it } from 'vitest'
import type { UnitResult } from '../api/types'
import { recordHeading } from './describe'
import { readableSections, storyFor, unitAsResponse } from './results'
import { countsOf, record, repoSnapshot, snapshotNames } from '../test/utils'

const SECRETS = ['123-45-6789', '987-65-4321', '900-44-3333', '000123456789', '998877665544', '5550001111', '4400112233', '000999888777']
// Empty sections are omitted, so a deposit with no bank details (a UKG reserved live check) only has the split rule.
const SECTION_FOR: Record<string, string[]> = {
  employee: ['Who this is'], company: ['The employer'], direct_deposit: ['Where the money goes', 'How pay is split'],
  deduction: ['The deduction'], payroll_run: ['The pay run'], paycheck: ['The paycheck'],
}

const unit = (name: string): UnitResult => {
  const snap = repoSnapshot(name)
  return { file: `${name}.json`, entity: snap.entity_type as UnitResult['entity'], label: name, status: 'converted', missing: [], counts: countsOf(snap.records), issues: snap.issues, records: snap.records, traces: [] }
}

describe('readable results from the real converted output (all 17 fixture runs)', () => {
  const names = snapshotNames()

  it('covers every provider and all six record types', () => {
    expect(names).toHaveLength(17)
    const types = new Set(names.flatMap((n) => repoSnapshot(n).records.map((r) => r.record_type)))
    expect([...types].sort()).toEqual(Object.keys(SECTION_FOR).sort())
    expect(new Set(names.map((n) => n.split('-')[0])).size).toBeGreaterThanOrEqual(3)
  })

  it.each(names)('%s: every record is described in plain words with its own section and a heading', (name) => {
    for (const r of repoSnapshot(name).records) {
      const sections = readableSections(r)
      expect(sections.length, `${r.record_type} ${r.source_id}`).toBeGreaterThan(0)
      expect(sections.map((s) => s.title).some((t) => SECTION_FOR[r.record_type]!.includes(t)), `${r.record_type} ${r.source_id}`).toBe(true)
      for (const s of sections) {
        expect(s.rows.length).toBeGreaterThan(0)
        for (const row of s.rows) {
          expect(row.label.trim()).not.toBe('')
          expect(row.value.trim()).not.toBe('')
          expect(row.value).not.toMatch(/\[object Object\]|undefined|null|NaN/)
        }
      }
      expect(recordHeading(r).trim()).not.toBe('')
    }
  })

  it.each(names)('%s: no secret and no hash appears anywhere in the readable output', (name) => {
    const all = JSON.stringify(repoSnapshot(name).records.map((r) => readableSections(r)))
    for (const secret of SECRETS) expect(all).not.toContain(secret)
    expect(all).not.toMatch(/account_number_hash|[0-9a-f]{40,}/)
  })

  it('shows names, dates, money and masked accounts the way a person reads them', () => {
    const emp = repoSnapshot('prismhr-employee').records[0]!
    const flat = readableSections(emp).flatMap((s) => s.rows.map((r) => `${r.label}=${r.value}`)).join('|')
    expect(flat).toContain('Name=Jordan Q Rivera')
    expect(flat).toContain('Hired=2021-03-01')
    expect(flat).toContain('Annual salary=100000.00')
    expect(flat).toContain('SSN=Last 4 digits only: 6789')
    const dep = repoSnapshot('isolved-direct-deposit').records[0]!
    const depText = readableSections(dep).flatMap((s) => s.rows.map((r) => `${r.label}=${r.value}`)).join('|')
    expect(depText).toMatch(/Account=\*{4}\d{4}/)
  })

  it('never shows a full SSN, account number or token even when a record wrongly carries one', () => {
    const risky = record({ ssn: '123-45-6789', account_number: '000123456789', password: 'hunter2hunter2', first_name: 'Ann', last_name: 'Lee' })
    const out = JSON.stringify(readableSections(risky))
    for (const secret of ['123-45-6789', '000123456789', 'hunter2hunter2']) expect(out).not.toContain(secret)
    expect(out).toContain('Ann Lee')
  })

  it('the unit adapter keeps the response the helpers expect, and ok means no error issue', () => {
    const u = unit('prismhr-employee')
    const res = unitAsResponse('prismhr', u)
    expect(res).toMatchObject({ provider: 'prismhr', entity_type: 'employee', ok: true })
    expect(res.records).toBe(u.records)
    expect(unitAsResponse('prismhr', { ...u, issues: [{ severity: 'error', code: 'x', message: 'm', path: null, source_id: null }] }).ok).toBe(false)
    expect(unitAsResponse('prismhr', { ...u, status: 'failed' }).ok).toBe(false)
  })

  it.each(names)('%s: the story is counted from the response, never estimated', (name) => {
    const u = unit(name)
    const story = storyFor('x', u)
    expect(story.records).toBe(u.records.length)
    expect(story.convertedFields).toBeGreaterThan(0)
    expect(story.withheld).toBe(u.records.reduce((n, r) => n + r.provenance.redacted_paths.length, 0))
    const issueTotal = story.problems.length + story.notUnderstood.length + story.providerGaps.length + story.notes.length
    expect(issueTotal).toBe(u.issues.length)
  })
})
