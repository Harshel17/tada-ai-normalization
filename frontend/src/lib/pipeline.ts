import type { CanonicalRecord, TraceStep } from '../api/types'
import { isSensitiveKey, maskObject } from './mask'

export const OP_INFO: Record<TraceStep['op'], { title: string; tone: 'green' | 'blue' | 'amber' | 'indigo' | 'slate' }> = {
  copy: { title: 'Copied as text', tone: 'green' },
  decimal: { title: 'Read as an exact number', tone: 'green' },
  integer: { title: 'Read as a whole number', tone: 'green' },
  boolean: { title: 'Converted to true / false', tone: 'blue' },
  date: { title: 'Read as a date', tone: 'blue' },
  datetime: { title: 'Read as a date and time', tone: 'blue' },
  enum: { title: 'Translated to a standard value', tone: 'indigo' },
  redact: { title: 'Withheld (only the last 4 kept)', tone: 'amber' },
  combine: { title: 'Combined from several fields', tone: 'slate' },
  derive: { title: 'Calculated', tone: 'slate' },
  route: { title: 'Placed by a rule', tone: 'slate' },
  assigned: { title: 'Set by the normalizer', tone: 'slate' },
  context: { title: 'Taken from your input', tone: 'slate' },
}

/** A plain sentence for the middle column of the pipeline. */
export function describeStep(step: TraceStep): string {
  const raw = step.sources.length === 1 ? String(step.sources[0]?.raw ?? '') : ''
  switch (step.op) {
    case 'enum':
      return `Looked up in the ${step.table ?? 'code'} table${raw ? `: “${raw}” → “${String(step.value)}”` : ''}`
    case 'redact':
      return 'The full value is never shown or stored; only the last four characters are kept'
    case 'derive':
    case 'combine':
    case 'route':
    case 'assigned':
      return step.note ?? OP_INFO[step.op].title
    case 'context':
      return 'Not in the file: this is the id you entered'
    default:
      return OP_INFO[step.op].title
  }
}

const STRUCTURAL = new Set(['record_type', 'provider', 'tenant_id', 'company_id', 'source_id', 'extras', 'provenance'])

export interface CanonicalLeaf {
  path: string
  value: unknown
}

/** Every filled scalar field of a converted record, as a dotted path (lists use [i]). */
export function flattenCanonical(record: CanonicalRecord): CanonicalLeaf[] {
  const out: CanonicalLeaf[] = []
  const walk = (node: unknown, path: string) => {
    if (node === null || node === undefined || node === '') return
    if (Array.isArray(node)) {
      node.forEach((v, i) => walk(v, `${path}[${i}]`))
    } else if (typeof node === 'object') {
      for (const [k, v] of Object.entries(node as Record<string, unknown>)) walk(v, path ? `${path}.${k}` : k)
    } else {
      out.push({ path, value: node })
    }
  }
  for (const [k, v] of Object.entries(maskObject(record))) if (!STRUCTURAL.has(k)) walk(v, k)
  return out
}

/** Fields that have a value but no verified source mapping. We never invent one. */
export function unmappedLeaves(record: CanonicalRecord, steps: TraceStep[]): CanonicalLeaf[] {
  const traced = new Set(steps.map((s) => s.dest))
  return flattenCanonical(record).filter((l) => !traced.has(l.path) && !/hash/i.test(l.path))
}

/** The part of a source path after the record's own root, e.g. `$.employee[0].client.jobCode` → `client.jobCode`. */
export function relativeSource(path: string, recordPath: string | null): string {
  if (recordPath && path.startsWith(recordPath + '.')) return path.slice(recordPath.length + 1)
  return path.replace(/^\$\.?/, '')
}

export function filterSteps(steps: TraceStep[], query: string, op: TraceStep['op'] | 'all'): TraceStep[] {
  const q = query.trim().toLowerCase()
  return steps.filter((s) => {
    if (op !== 'all' && s.op !== op) return false
    if (!q) return true
    const hay = [s.dest, String(s.value ?? ''), ...s.sources.flatMap((x) => [x.path, String(x.raw ?? '')])].join(' ').toLowerCase()
    return hay.includes(q)
  })
}

export function opCounts(steps: TraceStep[]): [TraceStep['op'], number][] {
  const m = new Map<TraceStep['op'], number>()
  for (const s of steps) m.set(s.op, (m.get(s.op) ?? 0) + 1)
  return [...m.entries()]
}

export function displayValue(v: unknown): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? 'true' : 'false'
  return String(v)
}

// --- grouping, coverage and default selection (all derived from the real trace) ---------------------
export const GROUPS = ['Identity', 'Employment', 'Dates', 'Job', 'Compensation', 'Privacy', 'Other'] as const
export type Group = (typeof GROUPS)[number]

const EMPLOYMENT = /^(employment_|termination_|eligible_for_rehire|is_contractor|full_time_equivalent|rehire_)/
const JOB = /^(job_|manager_id|supervisor_id|work_location_code|division_code|department_code|employee_type_raw|flsa_exempt|is_officer|is_manager|is_supervisor|is_seasonal|is_union|union_|soc_code|eeo_|workers_comp_code|timeclock_id)/
const IDENTITY = /^(employee_number|username|first_name|middle_name|last_name|preferred_|name_|gender|marital_|email|work_email|personal_email|secondary_email|home_phone|mobile_phone|work_phone|fax)/

/** Groups for the other record types, in display order. A field joins the first group whose pattern matches. */
const TYPE_GROUPS: Record<string, readonly (readonly [string, RegExp])[]> = {
  company: [
    ['Identity', /^(source_id|legal_name|display_name|federal_ein|naics_code|sic_code|parent_company_|service_type_raw)/],
    ['Status and dates', /^(status|first_payroll_date)|_date$/],
    ['Contact', /^(phone|email|website)/],
    ['Address', /^address\./],
    ['Legal entities', /^legal_entities\[/],
  ],
  direct_deposit: [
    ['Account', /^(routing_number|account_|bank_name)/],
    ['How pay is split', /^(allocation_|amount|percentage|cap_amount|skip_if_insufficient|voucher_types_raw|sequence)/],
    ['Status and dates', /^(status|active|effective_|prenote_|frequency_raw)/],
    ['Identity and links', /^(source_id|employee_id|source_position|name|instruction_type_raw|currency)/],
  ],
  deduction: [
    ['What it is', /^(deduction_code|name|setup_kind|category_raw|memo)/],
    ['Amount and rules', /^(amount|percentage|calc_|per_period_limit|max_|annual_max_amount|employer_|employee_frequency_raw|tracks_arrears|mandatory)/],
    ['Dates and status', /^(start_date|stop_date|status)/],
    ['Payee and references', /^(payee_|loan_or_garnishment_id|benefit_plan_id|reference_number)/],
    ['Identity and links', /^(source_id|employee_id|sequence)/],
  ],
  payroll_run: [
    ['The run', /^(name|run_type_raw|status_raw|run_number|ein_id)/],
    ['Dates', /_date$|^period_/],
    ['Identity', /^source_id/],
  ],
  paycheck: [
    ['Identity and links', /^(source_id|employee_id|run_id|check_number)/],
    ['Pay period and dates', /^(pay_date|period_|void_date|pay_group_raw)/],
    ['Totals', /^(gross_pay|net_pay|check_amount)/],
    ['Type and status', /^(paycheck_type|status_raw)/],
  ],
}

/** The group names of a record type, in the order they are shown. */
export function groupOrder(recordType = 'employee'): string[] {
  const own = TYPE_GROUPS[recordType]
  return own ? [...own.map(([g]) => g), 'Privacy', 'Other'] : [...GROUPS]
}

/**
 * Which group a mapped field belongs to. Decided only from the field's real destination path and the
 * operation that produced it: withheld values are Privacy; employees use their own rules (compensation.*,
 * `_date` names, ...); the other record types use ordered patterns. Anything else is Other.
 */
export function groupOf(step: Pick<TraceStep, 'dest' | 'op'>, recordType = 'employee'): string {
  const d = step.dest
  if (step.op === 'redact' || /ssn|hash|account_number/.test(d)) return 'Privacy'
  const own = TYPE_GROUPS[recordType]
  if (own) return own.find(([, re]) => re.test(d))?.[0] ?? 'Other'
  if (/ssn|hash|account/.test(d)) return 'Privacy'
  if (d.startsWith('compensation.')) return 'Compensation'
  if (/(^|_|\.)date$/.test(d)) return 'Dates'
  if (EMPLOYMENT.test(d)) return 'Employment'
  if (JOB.test(d)) return 'Job'
  if (IDENTITY.test(d)) return 'Identity'
  return 'Other'
}

export function groupSteps(steps: TraceStep[], recordType = 'employee'): { group: string; steps: TraceStep[] }[] {
  return groupOrder(recordType)
    .map((group) => ({ group, steps: steps.filter((s) => groupOf(s, recordType) === group) }))
    .filter((g) => g.steps.length > 0)
}

/** Steps worth drawing the eye to: a plain copy or exact-number read is not a real conversion. */
export const isMeaningful = (s: TraceStep): boolean => !['copy', 'decimal', 'integer', 'context', 'assigned'].includes(s.op)

export interface Coverage {
  traced: number
  total: number
}

/** How many of the record's filled fields have a recorded source. Never rounds up or assumes completeness. */
export function coverageOf(record: CanonicalRecord, steps: TraceStep[]): Coverage {
  const leaves = flattenCanonical(record)
  const traced = new Set(steps.map((s) => s.dest))
  return { traced: leaves.filter((l) => traced.has(l.path)).length, total: leaves.length }
}

interface UnitLike {
  entity: string
  status: string
  records: unknown[]
  traces: TraceStep[][]
}

/** Opens on evidence: prefer an employee unit that has traces, then any unit with traces, then the first unit. */
export function defaultSelection(units: UnitLike[]): { unit: number; record: number } {
  const hasTrace = (u: UnitLike) => u.traces.some((t) => t.length > 0)
  let unit = units.findIndex((u) => u.entity === 'employee' && hasTrace(u))
  if (unit < 0) unit = units.findIndex(hasTrace)
  if (unit < 0) unit = 0
  const record = Math.max(0, units[unit]?.traces.findIndex((t) => t.length > 0) ?? 0)
  return { unit, record }
}

/**
 * Why some filled fields have no source: the part of the record we do not trace yet is named, and
 * nothing is guessed. Line-item lists (earnings, taxes, ...) are called out by name.
 */
export function untracedExplanation(unmapped: CanonicalLeaf[]): string {
  const lists = [...new Set(unmapped.map((l) => /^([a-z_]+)\[/.exec(l.path)?.[1]).filter((n): n is string => !!n))]
  const base = 'These standard fields have values, but this version does not record which original field they came from. We do not guess, and this list is not a sign of an error.'
  return lists.length ? `${base} Line items (${lists.join(', ')}) are not traced yet.` : base
}

export const WITHHELD = '[withheld]'
const WITHHELD_DEST = /hash|token|password|secret|ssn_full/i
const lastKey = (path: string): string => path.replace(/\[\d+\]/g, '').split('.').pop() ?? path

/**
 * What the page may show for one step. The server already withholds sensitive values; this is a second line
 * of defence in the browser: a source whose key name is sensitive (accountNumber, ssn, ...) and a destination
 * that is a hash or credential are never displayed, whatever arrived.
 */
export function safeStep(step: TraceStep): TraceStep {
  const hideValue = WITHHELD_DEST.test(step.dest) || (isSensitiveKey(lastKey(step.dest)) && !/_masked$|_last4$/.test(step.dest))
  return {
    ...step,
    value: hideValue ? WITHHELD : step.value,
    sources: step.sources.map((x) => (isSensitiveKey(lastKey(x.path)) || WITHHELD_DEST.test(lastKey(x.path)) ? { ...x, raw: WITHHELD } : x)),
  }
}
