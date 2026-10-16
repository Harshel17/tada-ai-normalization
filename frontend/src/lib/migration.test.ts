import { describe, expect, it } from 'vitest'
import type { ConvertResponse, UnitResult } from '../api/types'
import { record } from '../test/utils'
import { buildRequest, employeesIn, formProblem, resolvePath, sourceSuggestions, sourceView } from './migration'
import type { FormState } from './migration'

const form = (over: Partial<FormState> = {}): FormState => ({
  companyId: '4410', username: 'pnair', confirmSourceUsername: false, useEmployeeNumber: false, useSourceId: false, addressCountry: '', einId: '', ...over,
})
const unit = (over: Partial<UnitResult>): UnitResult => ({ file: 'e.json', entity: 'employee', label: 'x', status: 'converted', missing: [], counts: {}, issues: [], records: [], traces: [], ...over })
const conv = (units: UnitResult[]): ConvertResponse => ({ provider: 'prismhr', files: [], results: units, suggestions: [] })

describe('employeesIn', () => {
  it('lists only employee records of converted sections, with their file', () => {
    const list = employeesIn(conv([
      unit({ records: [record({ source_id: 'A' }), record({ record_type: 'paycheck', source_id: 'P' })] }),
      unit({ file: 'x.json', status: 'failed', records: [record({ source_id: 'Z' })] }),
      unit({ file: 'y.json', records: [record({ source_id: 'B' })] }),
    ]))
    expect(list.map((c) => [c.file, c.record.source_id])).toEqual([['e.json', 'A'], ['y.json', 'B']])
  })
  it('is empty without a result', () => expect(employeesIn(null)).toEqual([]))
})

describe('the form', () => {
  const r = record({ username: 'srcuser', employee_number: '3001' })
  it('needs a destination company id', () => {
    expect(formProblem(form({ companyId: '' }), r)).toMatch(/company ID/)
    expect(formProblem(form({ companyId: '   ' }), r)).toMatch(/company ID/)
  })
  it.each(['a b', '../x', 'x'.repeat(41), 'a/b', '1;2'])('rejects the unsafe company id %s', (id) => {
    expect(formProblem(form({ companyId: id }), r)).toMatch(/letters, digits/)
  })
  it('accepts a normal id', () => expect(formProblem(form({ companyId: 'CID-4410_a' }), r)).toBeNull())
  it('requires explicit confirmation before the SOURCE username may be used', () => {
    expect(formProblem(form({ username: 'srcuser' }), r)).toMatch(/source system/)
    expect(formProblem(form({ username: 'srcuser', confirmSourceUsername: true }), r)).toBeNull()
    expect(formProblem(form({ username: 'someone-else' }), r)).toBeNull() // typed by the reviewer: no extra step
  })
  it('validates the optional country and EIN', () => {
    expect(formProblem(form({ addressCountry: 'US' }), r)).toMatch(/three-letter/)
    expect(formProblem(form({ addressCountry: 'usa' }), r)).toBeNull()
    expect(formProblem(form({ einId: '12x' }), r)).toMatch(/whole number/)
  })
})

describe('buildRequest', () => {
  const r = record({ username: 'srcuser', employee_number: '3001' })
  it('suggests, never sends, the source username without confirmation', () => {
    expect(sourceSuggestions(r)).toEqual({ username: 'srcuser', employeeNumber: '3001', sourceId: 'E1' })
    expect(buildRequest(r, form({ username: 'srcuser' })).operator.username).toBeUndefined()
    expect(buildRequest(r, form({ username: 'srcuser', confirmSourceUsername: true })).operator.username).toBe('srcuser')
    expect(buildRequest(r, form({ username: ' own ' })).operator.username).toBe('own')
  })
  it('sends no operator ids unless confirmed, and never invents any', () => {
    expect(buildRequest(r, form()).operator).toEqual({ username: 'pnair' })
    const on = buildRequest(r, form({ useEmployeeNumber: true, useSourceId: true, addressCountry: 'usa', einId: '12' }))
    expect(on.operator).toEqual({ username: 'pnair', use_source_employee_number: true, use_source_id_as_external_id: true, address_country: 'USA' })
    expect(on.destination).toEqual({ company_id: '4410', ein_id: 12 })
  })
  it('passes the canonical record through untouched', () => {
    expect(buildRequest(r, form()).employee).toBe(r)
  })
})

describe('source lookup', () => {
  const text = JSON.stringify({ employee: [{ id: 'A', compensation: { ssn: '907-31-4417' }, note: 'call 123-45-6789' }, { id: 'B' }], results: { nested: [{ x: 1 }] } })
  it('follows provenance paths', () => {
    expect(resolvePath(JSON.parse(text), '$.employee[1]')).toEqual({ id: 'B' })
    expect(resolvePath(JSON.parse(text), '$.results.nested[0].x')).toBe(1)
    expect(resolvePath(JSON.parse(text), '$.employee[9]')).toBeUndefined()
    expect(resolvePath(JSON.parse(text), '$.missing')).toBeUndefined()
    expect(resolvePath(JSON.parse(text), null)).toBeUndefined()
  })
  it('shows the source masked and never invents it', () => {
    const view = sourceView(text, '$.employee[0]')!
    expect(view).toContain('"id": "A"')
    expect(view).not.toContain('907-31-4417')
    expect(view).not.toContain('123-45-6789')
    expect(sourceView(undefined, '$.employee[0]')).toBeNull()
    expect(sourceView('{bad', '$.employee[0]')).toBeNull()
    expect(sourceView(text, '$.employee[7]')).toBeNull()
  })
  it('masks identity numbers held under generic keys', () => {
    const t = JSON.stringify({ employee: { national_id_numbers: [{ type: 'SSN', value: '903-44-8812' }] } })
    expect(sourceView(t, '$.employee')).not.toContain('903-44-8812')
  })
})
