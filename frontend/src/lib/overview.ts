import type { CanonicalRecord, ConvertResponse } from '../api/types'
import { leafCount } from './json'
import { filledFields } from './summary'

export interface Link {
  /** Readable labels, safe to show: a record type plus a name or code, never account digits. */
  from: string
  to: string
  via: string
  resolved: boolean
  /** The exact references, for the detailed view. `null` where the id embeds bank-account digits (direct deposits). */
  fromId: string | null
  toId: string | null
}

export interface Overview {
  records: number
  byType: Record<string, number>
  mappedFields: number
  unmappedValues: number
  withheld: number
  masked: number
  links: Link[]
  integrity: { passed: number; warning: number; failed: number; not_evaluated: number } | null
  issues: { error: number; warning: number; info: number }
}

const LABEL: Record<string, string> = { employee: 'Employee', paycheck: 'Paycheck', payroll_run: 'Payroll run', direct_deposit: 'Direct deposit', deduction: 'Deduction', company: 'Company' }
const text = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')

/** A readable label: the record type plus a name or code. Deposit ids contain routing and account digits, so they are never used. */
export function labelOf(r: CanonicalRecord, ordinal: number): string {
  const type = LABEL[r.record_type] ?? r.record_type
  switch (r.record_type) {
    case 'employee': {
      const name = [text(r.first_name), text(r.last_name)].filter(Boolean).join(' ')
      return name ? `${type} ${name}` : `${type} ${r.source_id}`
    }
    case 'direct_deposit': {
      const kind = text(r.account_type_raw) || text(r.account_type)
      const bank = text(r.bank_name)
      return `${type} #${ordinal}${kind || bank ? ` (${[kind, bank].filter(Boolean).join(', ')})` : ''}`
    }
    case 'deduction': {
      const what = text(r.name) || text(r.deduction_code)
      return what ? `${type} ${what}` : `${type} #${ordinal}`
    }
    default:
      return `${type} ${r.source_id}`
  }
}

/** Every number is counted from the records the API returned. Nothing is estimated. */
export function overviewOf(conv: ConvertResponse): Overview {
  const records = conv.results.filter((u) => u.status === 'converted').flatMap((u) => u.records)
  const byType: Record<string, number> = {}
  const ids: Record<string, Set<string>> = {}
  let mappedFields = 0
  let unmappedValues = 0
  let withheld = 0
  let masked = 0
  for (const r of records) {
    byType[r.record_type] = (byType[r.record_type] ?? 0) + 1
    ;(ids[r.record_type] ??= new Set()).add(r.source_id)
    mappedFields += filledFields(r).length
    unmappedValues += leafCount(r.extras)
    withheld += r.provenance.redacted_paths.length
    if (typeof r.account_number_masked === 'string' && r.account_number_masked) masked += 1
    if (typeof r.ssn_last4 === 'string' && r.ssn_last4) masked += 1
  }
  const links: Link[] = []
  // a position per record within its type and employee, so two deposits (or deductions) of one person stay distinguishable
  const ordinals = new Map<CanonicalRecord, number>()
  const seen = new Map<string, number>()
  for (const r of records) {
    const key = `${r.record_type}|${String(r.employee_id ?? '')}`
    const n = (seen.get(key) ?? 0) + 1
    seen.set(key, n)
    ordinals.set(r, n)
  }
  const byId = new Map(records.map((r) => [`${r.record_type}|${r.source_id}`, r]))
  const exact = (r: CanonicalRecord) => (r.record_type === 'direct_deposit' ? null : r.source_id)
  const add = (r: CanonicalRecord, field: string, targetType: string, via: string) => {
    const id = r[field]
    if (typeof id !== 'string' || !id) return
    const target = byId.get(`${targetType}|${id}`)
    links.push({
      from: labelOf(r, ordinals.get(r) ?? 1),
      to: target ? labelOf(target, ordinals.get(target) ?? 1) : `${LABEL[targetType]} ${id}`,
      via,
      resolved: ids[targetType]?.has(id) ?? false,
      fromId: exact(r),
      toId: id,
    })
  }
  for (const r of records) {
    if (r.record_type === 'paycheck') {
      add(r, 'run_id', 'payroll_run', 'run')
      add(r, 'employee_id', 'employee', 'employee')
    } else if (r.record_type === 'direct_deposit' || r.record_type === 'deduction') add(r, 'employee_id', 'employee', 'employee')
  }
  const issues = { error: 0, warning: 0, info: 0 }
  for (const u of conv.results) for (const i of u.issues) issues[i.severity] += 1
  return {
    records: records.length,
    byType,
    mappedFields,
    unmappedValues,
    withheld,
    masked,
    links,
    integrity: conv.integrity ? { passed: conv.integrity.summary.passed, warning: conv.integrity.summary.warning, failed: conv.integrity.summary.failed, not_evaluated: conv.integrity.summary.not_evaluated } : null,
    issues,
  }
}
