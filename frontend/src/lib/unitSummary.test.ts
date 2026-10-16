import { describe, expect, it } from 'vitest'
import type { FileDetection, UnitResult } from '../api/types'
import { record } from '../test/utils'
import { summarizeUnit } from './unitSummary'

const unit = (over: Partial<UnitResult>): UnitResult => ({ file: 'f.json', entity: 'employee', label: 'x', status: 'converted', missing: [], counts: {}, issues: [], records: [], traces: [], ...over })
const det = (name: string, units: { entity: string; nested_in?: string | null; requires?: string[] }[]): FileDetection => ({
  name,
  detection: { status: 'ok', units: units.map((u) => ({ entity: u.entity, shape: 'x', label: u.entity, keys: null, counts: {}, derived_counts: {}, evidence: [], requires: u.requires ?? [], optional: [], nested_in: u.nested_in ?? null, convention: false, strength: 'strong', notes: [] })) as never, candidates: [], warnings: [], message: null, provider_hint: null },
})

describe('summarizeUnit', () => {
  const emps = unit({ file: 'e.json', counts: { employee: 2 }, records: [record({ source_id: 'E1', first_name: 'Jordan', middle_name: 'Q', last_name: 'Rivera' }), record({ source_id: 'E2', first_name: 'Casey', last_name: 'Nguyen' })] })
  const deps = unit({ file: 'e.json', entity: 'direct_deposit', counts: { direct_deposit: 3 }, records: [1, 2, 3].map((i) => record({ record_type: 'direct_deposit', source_id: `D${i}`, active: true, allocation_type: i === 1 ? 'fixed_amount' : 'percentage', account_type: 'checking', account_number_masked: '****6789', bank_name: 'Test Bank' })) })
  const deds = unit({ file: 'e.json', entity: 'deduction', counts: { deduction: 2 }, records: [record({ record_type: 'deduction', source_id: 'P' }), record({ record_type: 'deduction', source_id: 'A' })] })
  const files = [det('e.json', [{ entity: 'employee' }, { entity: 'direct_deposit', nested_in: 'employee' }, { entity: 'deduction', nested_in: 'employee' }])]
  const all = [emps, deps, deds]

  it('employees: counts and names come from the records, and embedded siblings of the same file are counted', () => {
    const s = summarizeUnit(emps, all, files)
    expect(s.tiles).toEqual([{ label: 'Employees', value: 2 }, { label: 'Direct deposits', value: 3 }, { label: 'Embedded deductions', value: 2 }])
    expect(s.preview).toEqual(['Jordan Q Rivera · Casey Nguyen'])
    expect(s.owner).toBeNull()
  })
  it('employees from another file are not counted as embedded', () => {
    const other = unit({ file: 'other.json', entity: 'direct_deposit', counts: { direct_deposit: 9 }, records: [record({ record_type: 'direct_deposit' })] })
    expect(summarizeUnit(emps, [emps, other], files).tiles).toEqual([{ label: 'Employees', value: 2 }])
  })
  it('truncates long name lists with "+N more"', () => {
    const many = unit({ counts: { employee: 5 }, records: [1, 2, 3, 4, 5].map((i) => record({ source_id: `E${i}`, first_name: `N${i}`, last_name: 'X' })) })
    expect(summarizeUnit(many, [many], []).preview[0]).toBe('N1 X · N2 X · N3 X · +2 more')
  })
  it('deposits: safe payment-method facts only, never any account digits', () => {
    const s = summarizeUnit(deps, all, files)
    expect(s.tiles).toEqual([{ label: 'Direct deposits', value: 3 }, { label: 'Active', value: 3 }])
    expect(s.preview).toEqual(['1 fixed amount · 2 percentage', 'Account types: checking'])
    expect(JSON.stringify(s)).not.toMatch(/6789|\*\*\*\*|Test Bank/)
    expect(s.owner).toBe('in-file') // embedded: the employee record names the owner
  })
  it('a separate list that needs an employee id is flagged as an ASSIGNED owner, never as verified', () => {
    const bare = unit({ file: 'd.json', entity: 'direct_deposit', counts: { direct_deposit: 1 }, records: [record({ record_type: 'direct_deposit' })] })
    expect(summarizeUnit(bare, [bare], [det('d.json', [{ entity: 'direct_deposit', requires: ['company_id', 'employee_id'] }])]).owner).toBe('assigned')
    const named = unit({ file: 'x.json', entity: 'deduction', counts: { deduction: 1 }, records: [record({ record_type: 'deduction', name: 'Medical' })] })
    const s = summarizeUnit(named, [named], [det('x.json', [{ entity: 'deduction' }])])
    expect(s.owner).toBe('in-file')
    expect(s.preview).toEqual(['Medical'])
  })
  it('payroll: run and paycheck counts and the real pay period', () => {
    const pay = unit({ file: 'p.json', entity: 'payroll', counts: { payroll_run: 2, paycheck: 3 }, records: [
      record({ record_type: 'payroll_run', period_start: '2025-02-01', period_end: '2025-02-15' }), record({ record_type: 'payroll_run', period_start: '2025-02-16', period_end: '2025-02-28' }),
    ] })
    const s = summarizeUnit(pay, [pay], [])
    expect(s.tiles).toEqual([{ label: 'Payroll runs', value: 2 }, { label: 'Paychecks', value: 3 }])
    expect(s.preview).toEqual(['Pay period 2025-02-01 to 2025-02-28'])
  })
  it('company: name and legal entities when present; nothing invented when absent', () => {
    const co = unit({ entity: 'company', counts: { company: 1 }, records: [record({ record_type: 'company', legal_name: 'Acme LLC', legal_entities: [{}, {}] })] })
    expect(summarizeUnit(co, [co], []).tiles).toEqual([{ label: 'Company', value: 1 }, { label: 'Legal entities', value: 2 }])
    expect(summarizeUnit(co, [co], []).preview).toEqual(['Acme LLC'])
    const bare = unit({ entity: 'company', counts: { company: 1 }, records: [record({ record_type: 'company' })] })
    expect(summarizeUnit(bare, [bare], []).preview).toEqual([])
  })
  it('pluralizes correctly', () => {
    const one = unit({ entity: 'company', counts: { company: 1 }, records: [record({ record_type: 'company' })] })
    const two = unit({ entity: 'company', counts: { company: 2 }, records: [record({ record_type: 'company' }), record({ record_type: 'company' })] })
    expect(summarizeUnit(one, [one], []).tiles[0]!.label).toBe('Company')
    expect(summarizeUnit(two, [two], []).tiles[0]!.label).toBe('Companies')
  })
})
