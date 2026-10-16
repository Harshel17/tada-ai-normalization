import type { CanonicalRecord, FileDetection, UnitResult } from '../api/types'
import { recordHeading } from './describe'

/** One statistic on a collapsed card. Every one is counted from the records the API returned. */
export interface Tile {
  label: string
  value: number
}

export interface UnitSummary {
  tiles: Tile[]
  /** Short, safe lines about what is inside (names, codes, periods). Never account digits. */
  preview: string[]
  /** Where the owning employee of these records came from. null when owners are not a question for this entity. */
  owner: 'in-file' | 'assigned' | null
}

const NOUN: Record<string, [string, string]> = {
  company: ['Company', 'Companies'],
  employee: ['Employee', 'Employees'],
  direct_deposit: ['Direct deposit', 'Direct deposits'],
  deduction: ['Deduction', 'Deductions'],
  payroll_run: ['Payroll run', 'Payroll runs'],
  paycheck: ['Paycheck', 'Paychecks'],
}
const noun = (key: string, n: number): string => (NOUN[key] ?? [key.replace(/_/g, ' '), `${key.replace(/_/g, ' ')}s`])[n === 1 ? 0 : 1]!

const text = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')
const more = (names: string[], shown = 3): string => (names.length > shown ? `${names.slice(0, shown).join(' · ')} · +${names.length - shown} more` : names.join(' · '))
const ALLOCATION: Record<string, string> = { fixed_amount: 'fixed amount', percentage: 'percentage', remaining_balance: 'remaining balance' }

function tally(records: CanonicalRecord[], pick: (r: CanonicalRecord) => string): string[] {
  const counts = new Map<string, number>()
  for (const r of records) {
    const k = pick(r)
    if (k) counts.set(k, (counts.get(k) ?? 0) + 1)
  }
  return [...counts].map(([k, n]) => `${n} ${k}`)
}

/**
 * Display facts for one converted section, from its own records plus, for employees, the sibling sections that came from
 * the SAME file (a file that embeds deposits or deductions inside its employees). Nothing is inferred: a missing field
 * produces no tile or line.
 */
export function summarizeUnit(unit: UnitResult, all: UnitResult[], detections: FileDetection[]): UnitSummary {
  const records = unit.records
  const tiles: Tile[] = Object.entries(unit.counts).map(([k, n]) => ({ label: noun(k, n), value: n }))
  const preview: string[] = []
  const detected = detections.find((f) => f.name === unit.file)?.detection.units.find((u) => u.entity === unit.entity)
  const needsOwner = !!detected?.requires.includes('employee_id')

  if (unit.entity === 'employee') {
    for (const sibling of all) {
      if (sibling === unit || sibling.file !== unit.file || sibling.status !== 'converted' || sibling.records.length === 0) continue
      const embedded = detections.find((f) => f.name === sibling.file)?.detection.units.find((u) => u.entity === sibling.entity)?.nested_in === 'employee'
      if (sibling.entity === 'direct_deposit' || sibling.entity === 'deduction') {
        const base = noun(sibling.entity, sibling.records.length)
        tiles.push({ label: embedded && sibling.entity === 'deduction' ? `Embedded ${base.toLowerCase()}` : base, value: sibling.records.length })
      }
    }
    const names = records.map((r) => recordHeading(r)).filter(Boolean)
    if (names.length) preview.push(more(names))
  } else if (unit.entity === 'company') {
    const names = records.map((r) => text(r.legal_name) || text(r.display_name)).filter(Boolean)
    if (names.length) preview.push(more(names))
    const entities = records.reduce((n, r) => n + (Array.isArray(r.legal_entities) ? r.legal_entities.length : 0), 0)
    if (entities > 0) tiles.push({ label: entities === 1 ? 'Legal entity' : 'Legal entities', value: entities })
  } else if (unit.entity === 'direct_deposit') {
    const active = records.filter((r) => r.active === true || r.status === 'active').length
    if (active > 0) tiles.push({ label: 'Active', value: active })
    const mix = tally(records, (r) => ALLOCATION[text(r.allocation_type)] ?? '')
    if (mix.length) preview.push(mix.join(' · '))
    const kinds = [...new Set(records.map((r) => text(r.account_type) || text(r.account_type_raw)).filter(Boolean))]
    if (kinds.length) preview.push(`Account types: ${kinds.join(', ')}`) // types only: never account digits
  } else if (unit.entity === 'deduction') {
    const names = records.map((r) => text(r.name) || text(r.deduction_code)).filter(Boolean)
    if (names.length) preview.push(more(names))
  } else if (unit.entity === 'payroll') {
    const runs = records.filter((r) => r.record_type === 'payroll_run')
    const starts = runs.map((r) => text(r.period_start)).filter(Boolean).sort()
    const ends = runs.map((r) => text(r.period_end)).filter(Boolean).sort()
    if (starts.length && ends.length) preview.push(`Pay period ${starts[0]} to ${ends[ends.length - 1]}`)
  }

  let owner: UnitSummary['owner'] = null
  if (unit.entity === 'direct_deposit' || unit.entity === 'deduction') owner = needsOwner ? 'assigned' : 'in-file'
  return { tiles, preview, owner }
}

