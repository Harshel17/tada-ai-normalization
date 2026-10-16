import type { CanonicalRecord } from '../api/types'

/** Readable sections built ONLY from fields that exist on the record the API returned. */
export interface Row {
  label: string
  value: string
  /** Extra explanation shown under the value (e.g. the provider's own code). */
  note?: string
}
export interface Section {
  title: string
  rows: Row[]
}

type Obj = Record<string, unknown>
const get = (o: unknown, path: string): unknown =>
  path.split('.').reduce<unknown>((v, k) => (v && typeof v === 'object' ? (v as Obj)[k] : undefined), o)
const filled = (v: unknown): boolean => v !== null && v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0)
const text = (v: unknown): string => (typeof v === 'boolean' ? (v ? 'Yes' : 'No') : String(v))

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ')

function addressLine(a: unknown): string | null {
  if (!a || typeof a !== 'object') return null
  const o = a as Obj
  const street = [o.house_number, o.line1, o.line2, o.line3].filter(filled).join(' ')
  const parts = [street, o.city, [o.state, o.postal_code].filter(filled).join(' '), o.country].filter(filled)
  return parts.length ? parts.join(', ') : null
}

const STATUS_WORDS: Record<string, string> = {
  active: 'Active', on_leave: 'On leave', terminated: 'Terminated', pending: 'Pending', inactive: 'Inactive', cancelling: 'Cancelling',
}

/** "Active" for a recognised status, or the provider's own label when the system did not interpret it. */
function statusRow(label: string, canonical: unknown, raw: unknown): Row | null {
  if (filled(canonical)) {
    const word = STATUS_WORDS[String(canonical)] ?? sentence(String(canonical))
    return { label, value: word, note: filled(raw) ? `The provider’s own label: “${String(raw)}”` : undefined }
  }
  if (filled(raw)) return { label, value: String(raw), note: 'The provider’s own label; this system does not interpret it, so it is shown as received.' }
  return null
}

const rows = (...candidates: (Row | null | undefined)[]): Row[] => candidates.filter((r): r is Row => !!r)
const row = (label: string, v: unknown, note?: string): Row | null => (filled(v) ? { label, value: text(v), note } : null)

export function recordHeading(r: CanonicalRecord): string {
  switch (r.record_type) {
    case 'employee': {
      const name = [r.first_name, r.middle_name, r.last_name].filter(filled).join(' ')
      return name || `Employee ${r.source_id}`
    }
    case 'company':
      return String(r.legal_name ?? r.display_name ?? `Employer ${r.source_id}`)
    case 'direct_deposit':
      return `${filled(r.bank_name) ? String(r.bank_name) : 'Bank account'} ${filled(r.account_number_masked) ? String(r.account_number_masked) : ''}`.trim()
    case 'deduction':
      return String(r.name ?? r.deduction_code ?? `Deduction ${r.source_id}`)
    case 'payroll_run':
      return String(r.name ?? `Pay run ${r.source_id}`)
    case 'paycheck':
      return `Paycheck ${r.source_id}`
    default:
      return r.source_id
  }
}

const ALLOCATION_WORDS: Record<string, string> = {
  fixed_amount: 'A fixed amount',
  percentage: 'A percentage of pay',
  remaining_balance: 'Whatever is left after the other deposits',
}

function employeeSections(r: CanonicalRecord): Section[] {
  const pay = (r.compensation ?? {}) as Obj
  const taxes = Array.isArray(r.tax_withholdings) ? r.tax_withholdings.length : 0
  const contacts = Array.isArray(r.emergency_contacts) ? r.emergency_contacts.length : 0
  const allocations = Array.isArray(r.labor_allocations) ? r.labor_allocations.length : 0
  return [
    {
      title: 'Who this is',
      rows: rows(
        row('Name', recordHeading(r)),
        row('Goes by', r.preferred_name),
        row('Employee number', r.employee_number),
        row('Employee ID in the provider’s system', r.source_id),
        row('Date of birth', r.birth_date),
        filled(r.gender)
          ? { label: 'Gender', value: sentence(String(r.gender)), note: filled(r.gender_raw) ? `The provider sent the code “${String(r.gender_raw)}”` : undefined }
          : filled(r.gender_raw)
            ? { label: 'Gender', value: String(r.gender_raw), note: 'A code the provider documentation does not explain, so it is kept as received rather than guessed.' }
            : null,
        row('Email', r.email ?? r.work_email ?? r.personal_email),
        row('Mobile phone', r.mobile_phone),
        row('Home phone', r.home_phone),
        row('Work phone', r.work_phone),
        row('Home address', addressLine(r.address)),
        row('Mailing address', addressLine(r.mailing_address)),
        row('SSN', filled(r.ssn_last4) ? `Last 4 digits only: ${String(r.ssn_last4)}` : null, 'The full number is never stored or shown.'),
      ),
    },
    {
      title: 'Employment',
      rows: rows(
        statusRow('Status', r.employment_status, r.employment_status_raw),
        row('Employer', r.company_id, 'Employer ID; the employer’s name is only known if the employer record was also converted.'),
        row('Hired', r.hire_date),
        row('Originally hired', r.original_hire_date),
        row('Left on', r.termination_date),
        row('Job title', r.job_title),
        row('Job code', r.job_code),
        row('Department', r.department_code),
        row('Division', r.division_code),
        row('Work location', r.work_location_code),
        row('Manager (ID)', r.manager_id),
        row('Employee type', r.employee_type_raw, 'The provider’s own code'),
        row('Exempt from overtime (FLSA)', r.flsa_exempt),
        row('Employment type effective', r.employee_type_effective_date),
        row('Union member since', r.union_start_date),
        row('ACA employment status (provider’s label)', r.aca_employment_status_raw),
      ),
    },
    {
      title: 'Pay set-up',
      rows: rows(
        filled(pay.pay_type) ? { label: 'Paid', value: sentence(String(pay.pay_type)), note: filled(pay.pay_type_raw) ? `The provider’s code: “${String(pay.pay_type_raw)}”` : undefined } : row('Pay type (provider’s code)', pay.pay_type_raw),
        row('Annual salary', pay.annual_salary),
        row('Pay per pay period', pay.pay_period_amount),
        row('Hourly rate', pay.hourly_rate),
        row('Standard hours per pay period', pay.standard_hours_per_pay_period),
        row('Standard hours per week', pay.standard_hours_per_week),
        row('Pay group', pay.pay_group_raw),
        row('Pay set-up effective', pay.effective_date),
        row('Last paid', pay.last_pay_date),
        row('Next pay review', pay.next_review_date),
        row('Compa-ratio (as the provider gives it)', pay.compa_ratio),
        row('Last performance review', pay.last_performance_review_date, filled(pay.last_performance_review_rating_raw) ? `Rating as the provider gave it: “${String(pay.last_performance_review_rating_raw)}”` : undefined),
        row('Job title at that review', pay.last_performance_review_job_title),
        row('Next performance review', pay.next_performance_review_date),
        row('Currency', pay.currency),
      ),
    },
    {
      title: 'Other details converted',
      rows: rows(
        taxes ? { label: 'Tax withholding elections', value: `${taxes}`, note: 'Federal / state / local filing choices for this employee' } : null,
        contacts ? { label: 'Emergency contacts', value: `${contacts}` } : null,
        allocations ? { label: 'Labor-cost allocations', value: `${allocations}` } : null,
        r.benefits ? row('Benefit group', get(r, 'benefits.benefit_group')) : null,
      ),
    },
  ]
}

function companySections(r: CanonicalRecord): Section[] {
  const entities = Array.isArray(r.legal_entities) ? (r.legal_entities as Obj[]) : []
  return [
    {
      title: 'The employer',
      rows: rows(
        row('Legal name', r.legal_name),
        row('Also known as', r.display_name),
        statusRow('Status', r.status, r.status_raw),
        row('Employer ID in the provider’s system', r.source_id),
        row('Belongs to (parent ID)', r.parent_company_id),
        row('Tax ID (EIN)', r.federal_ein),
        row('Address', addressLine(r.address)),
        row('Phone', r.phone),
        row('Email', r.email),
        row('Website', r.website),
        row('First payroll', r.first_payroll_date),
        entities.length
          ? { label: 'Tax entities (EINs)', value: entities.map((e) => [e.name, e.tax_id].filter(filled).join(' · ') || String(e.entity_id)).join('; ') }
          : null,
      ),
    },
  ]
}

function depositSections(r: CanonicalRecord): Section[] {
  const how = filled(r.allocation_type)
    ? ALLOCATION_WORDS[String(r.allocation_type)] ?? sentence(String(r.allocation_type))
    : filled(r.allocation_raw) ? `Provider code “${String(r.allocation_raw)}”` : null
  const amount = filled(r.amount) ? `${String(r.amount)}` : filled(r.percentage) ? `${String(r.percentage)}%` : null
  return [
    {
      title: 'Where the money goes',
      rows: rows(
        row('Bank', r.bank_name),
        row('Account', r.account_number_masked, 'Only the last four characters are kept.'),
        row('Routing number', r.routing_number),
        filled(r.account_type) ? { label: 'Account type', value: sentence(String(r.account_type)) } : row('Account type (provider’s label)', r.account_type_raw),
      ),
    },
    {
      title: 'How pay is split',
      rows: rows(
        how ? { label: 'Rule', value: how } : null,
        amount ? { label: 'Amount', value: amount, note: filled(r.percentage_basis_raw) ? `Percentage of: ${String(r.percentage_basis_raw)}` : undefined } : null,
        row('Upper limit', r.cap_amount),
        row('Order', r.sequence ?? r.source_position, 'Position in the provider’s own list'),
        statusRow('Status', r.status, r.status_raw),
        filled(r.active) ? { label: 'Active', value: text(r.active) } : null,
        row('Starts', r.effective_from),
        row('Ends', r.effective_to),
        row('For employee (ID)', r.employee_id),
      ),
    },
  ]
}

function deductionSections(r: CanonicalRecord): Section[] {
  const kinds: Record<string, string> = { recurring: 'Recurring', scheduled: 'Scheduled / temporary', configured: 'Configured for the employee' }
  const calc = Array.isArray(r.calc_params) ? (r.calc_params as Obj[]) : []
  return [
    {
      title: 'The deduction',
      rows: rows(
        row('Name', r.name),
        row('Code', r.deduction_code),
        filled(r.setup_kind) ? { label: 'Kind', value: kinds[String(r.setup_kind)] ?? sentence(String(r.setup_kind)) } : null,
        filled(r.amount) ? { label: 'Amount taken', value: String(r.amount) } : null,
        filled(r.percentage) ? { label: 'Percent of pay', value: `${String(r.percentage)}%` } : null,
        calc.length ? { label: 'Calculation settings', value: calc.map((c) => `${String(c.type)}: ${String(c.value)}`).join('; '), note: 'The provider does not document what these mean, so they are shown as received.' } : null,
        statusRow('Status', r.status, r.status_raw),
        row('Per-paycheck limit', r.per_period_limit),
        row('Yearly limit', r.annual_max_amount),
        row('Starts', r.start_date),
        row('Stops', r.stop_date),
        row('Paid to', r.payee_name ?? r.payee_code),
        row('For employee (ID)', r.employee_id),
      ),
    },
  ]
}

function runSections(r: CanonicalRecord): Section[] {
  return [
    {
      title: 'The pay run',
      rows: rows(
        row('Name', r.name),
        row('Paid on', r.pay_date, 'The day employees get paid'),
        r.period_start || r.period_end ? { label: 'Work period covered', value: `${String(r.period_start ?? '?')} to ${String(r.period_end ?? '?')}`, note: 'Not the same as the pay date' } : null,
        row('Direct deposit date', r.direct_deposit_date),
        row('Status (provider’s label)', r.status_raw),
        row('Run type (provider’s label)', r.run_type_raw),
        row('Run ID in the provider’s system', r.source_id),
      ),
    },
  ]
}

const lineRows = (label: string, lines: unknown, valueKey: string): Row[] => {
  if (!Array.isArray(lines) || !lines.length) return []
  return (lines as Obj[]).slice(0, 8).map((l) => ({
    label: `${label}: ${[l.code, l.description].filter(filled).join(' · ') || `line ${String(l.line_no)}`}`,
    value: filled(l[valueKey]) ? String(l[valueKey]) : '—',
    note: l.liability ? `Paid by the ${String(l.liability)}` : undefined,
  }))
}

function paycheckSections(r: CanonicalRecord): Section[] {
  return [
    {
      title: 'The paycheck',
      rows: rows(
        row('For employee (ID)', r.employee_id),
        row('Paid on', r.pay_date),
        r.period_start || r.period_end ? { label: 'Work period', value: `${String(r.period_start ?? '?')} to ${String(r.period_end ?? '?')}` } : null,
        row('Gross pay', r.gross_pay, Array.isArray(get(r, 'provenance.derived_fields')) && (get(r, 'provenance.derived_fields') as string[]).includes('gross_pay') ? 'Added up by this system from the earnings lines; the provider does not report it.' : undefined),
        row('Net pay', r.net_pay),
        row('Check amount', r.check_amount),
        row('Check number', r.check_number),
        row('Status (provider’s label)', r.status_raw),
      ),
    },
    {
      title: 'Line items',
      rows: [
        ...lineRows('Earned', r.earnings, 'amount'),
        ...lineRows('Tax', r.taxes, 'amount'),
        ...lineRows('Deducted', r.deductions, 'amount'),
        ...rows(Array.isArray(r.benefits) && r.benefits.length ? { label: 'Benefit contribution lines', value: String(r.benefits.length) } : null),
        ...rows(Array.isArray(r.payments) && r.payments.length ? { label: 'Payments actually made', value: String(r.payments.length), note: 'Bank accounts are masked.' } : null),
      ],
    },
  ]
}

/** Sections for one record, omitting empty rows and empty sections. */
export function describeRecord(r: CanonicalRecord): Section[] {
  const sections =
    r.record_type === 'employee' ? employeeSections(r)
    : r.record_type === 'company' ? companySections(r)
    : r.record_type === 'direct_deposit' ? depositSections(r)
    : r.record_type === 'deduction' ? deductionSections(r)
    : r.record_type === 'payroll_run' ? runSections(r)
    : r.record_type === 'paycheck' ? paycheckSections(r)
    : []
  return sections.filter((s) => s.rows.length > 0)
}

// --- plain labels for canonical field names ------------------------------------------------------
const LABELS: Record<string, string> = {
  first_name: 'First name', middle_name: 'Middle name', last_name: 'Last name', preferred_name: 'Goes by',
  employee_number: 'Employee number', birth_date: 'Date of birth', gender: 'Gender', gender_raw: 'Gender (provider’s code)',
  marital_status_raw: 'Marital status (provider’s code)', ssn_last4: 'SSN (last 4 only)', email: 'Email', work_email: 'Work email',
  personal_email: 'Personal email', home_phone: 'Home phone', mobile_phone: 'Mobile phone', work_phone: 'Work phone',
  employment_status: 'Employment status', employment_status_raw: 'Employment status (provider’s label)', status_date: 'Status effective date',
  hire_date: 'Hire date', original_hire_date: 'First hire date', rehire_date: 'Rehire date', termination_date: 'Termination date',
  job_code: 'Job code', job_title: 'Job title', department_code: 'Department', division_code: 'Division', work_location_code: 'Work location',
  manager_id: 'Manager (ID)', employee_type_raw: 'Employee type (provider’s code)', flsa_exempt: 'Exempt from overtime',
  company_id: 'Employer ID', source_id: 'ID in provider system', legal_name: 'Legal name', display_name: 'Also known as',
  federal_ein: 'Tax ID (EIN)', status: 'Status', status_raw: 'Status (provider’s label)', bank_name: 'Bank',
  account_number_masked: 'Account (masked)', routing_number: 'Routing number', account_type: 'Account type', account_type_raw: 'Account type (provider’s label)',
  allocation_type: 'How pay is split', allocation_raw: 'How pay is split (provider’s code)', amount: 'Amount', percentage: 'Percent',
  deduction_code: 'Code', name: 'Name', setup_kind: 'Kind', pay_date: 'Pay date', period_start: 'Period start', period_end: 'Period end',
  gross_pay: 'Gross pay', net_pay: 'Net pay', check_amount: 'Check amount', check_number: 'Check number', employee_id: 'For employee (ID)',
  'compensation.annual_salary': 'Pay: annual salary', 'compensation.hourly_rate': 'Pay: hourly rate',
  'compensation.pay_period_amount': 'Pay: per pay period', 'compensation.pay_type': 'Pay: type', 'compensation.pay_type_raw': 'Pay: type (provider’s code)',
  'compensation.pay_group_raw': 'Pay: group', 'compensation.effective_date': 'Pay: effective date',
  'compensation.standard_hours_per_pay_period': 'Pay: standard hours per period', 'compensation.standard_hours_per_week': 'Pay: standard hours per week',
  'compensation.last_pay_date': 'Pay: last paid', 'compensation.next_review_date': 'Pay: next pay review', 'compensation.compa_ratio': 'Pay: compa-ratio',
  'compensation.last_performance_review_date': 'Pay: last performance review', 'compensation.last_performance_review_rating_raw': 'Pay: last review rating (provider’s value)',
  'compensation.last_performance_review_job_title': 'Pay: job title at last review', 'compensation.next_performance_review_date': 'Pay: next performance review',
  'benefits.paid_through_date': 'Benefits: paid through', work_phone_extension: 'Work phone extension', employee_type_effective_date: 'Employee type effective',
  union_start_date: 'Union member since', aca_employment_status_raw: 'ACA employment status (provider’s label)',
}

export function fieldLabel(path: string): string {
  const known = LABELS[path]
  if (known) return known
  const parts = path.split('.')
  const last = (parts[parts.length - 1] ?? path).replace(/\[\d+\]/g, '')
  const words = last.replace(/_raw$/, ' (provider’s code)').replace(/_/g, ' ')
  const base = sentence(words)
  return parts.length > 1 ? `${sentence(parts[0]!.replace(/_/g, ' '))}: ${base.toLowerCase()}` : base
}
