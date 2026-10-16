import type { CanonicalRecord, ConvertResponse, MigrationOperator, MigrationRequestBody, NotMigratedReason } from '../api/types'
import { maskJsonText } from './mask'

/** An employee found in the current normalization result, with the file it came from. */
export interface EmployeeChoice {
  key: string
  file: string
  record: CanonicalRecord
}

export function employeesIn(conversion: ConvertResponse | null): EmployeeChoice[] {
  const out: EmployeeChoice[] = []
  for (const unit of conversion?.results ?? []) {
    if (unit.status !== 'converted') continue
    unit.records.forEach((record, i) => {
      if (record.record_type === 'employee') out.push({ key: `${unit.file}#${i}`, file: unit.file, record })
    })
  }
  return out
}

const str = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')

/** What the form may offer as a SUGGESTION. Nothing here is ever sent unless the reviewer confirms it. */
export function sourceSuggestions(record: CanonicalRecord) {
  return { username: str(record.username), employeeNumber: str(record.employee_number), sourceId: str(record.source_id) }
}

export const COMPANY_ID = /^[A-Za-z0-9_-]{1,40}$/

export interface FormState {
  companyId: string
  username: string
  confirmSourceUsername: boolean
  useEmployeeNumber: boolean
  useSourceId: boolean
  addressCountry: string
  einId: string
}

/** Why the preview cannot be requested yet, or null. Only things that would make the request invalid. */
export function formProblem(f: FormState, record: CanonicalRecord): string | null {
  if (!f.companyId.trim()) return 'Enter the destination company ID.'
  if (!COMPANY_ID.test(f.companyId.trim())) return 'The destination company ID may use letters, digits, “-” and “_” only (up to 40 characters).'
  const suggested = sourceSuggestions(record).username
  if (f.username.trim() && suggested && f.username.trim() === suggested && !f.confirmSourceUsername) {
    return 'This is the username from the source system. Confirm it is also right in UKG Ready, or type a different one.'
  }
  if (f.addressCountry.trim() && !/^[A-Za-z]{3}$/.test(f.addressCountry.trim())) return 'The address country must be a three-letter ISO code such as USA.'
  if (f.einId.trim() && !/^\d+$/.test(f.einId.trim())) return 'The EIN id must be a whole number.'
  return null
}

/** Builds the request body from the form. A username equal to the source's is sent only once confirmed. */
export function buildRequest(record: CanonicalRecord, f: FormState): MigrationRequestBody {
  const operator: MigrationOperator = {}
  const username = f.username.trim()
  const suggested = sourceSuggestions(record).username
  if (username && !(username === suggested && !f.confirmSourceUsername)) operator.username = username
  if (f.useEmployeeNumber) operator.use_source_employee_number = true
  if (f.useSourceId) operator.use_source_id_as_external_id = true
  if (f.addressCountry.trim()) operator.address_country = f.addressCountry.trim().toUpperCase()
  const destination: MigrationRequestBody['destination'] = { company_id: f.companyId.trim() }
  if (f.einId.trim()) destination.ein_id = Number(f.einId.trim())
  return { employee: record, destination, operator }
}

/** Follows a provenance path such as `$.employee[0]` or `$.results[1].directDeposits[0]` inside parsed JSON. */
export function resolvePath(root: unknown, path: string | null): unknown {
  if (!path || !path.startsWith('$')) return undefined
  const tokens = path.slice(1).match(/\.[^.[\]]+|\[\d+\]/g)
  if (tokens === null && path !== '$') return undefined
  let cur: unknown = root
  for (const t of tokens ?? []) {
    if (cur === null || typeof cur !== 'object') return undefined
    cur = t.startsWith('[') ? (cur as unknown[])[Number(t.slice(1, -1))] : (cur as Record<string, unknown>)[t.slice(1)]
  }
  return cur
}

const SSN_FORMAT = /\b\d{3}-\d{2}-\d{4}\b/g

/** The source record as shown to the reviewer: located by its provenance path, masked as text. null if it cannot be found. */
export function sourceView(fileText: string | undefined, path: string | null): string | null {
  if (!fileText) return null
  let root: unknown
  try {
    root = JSON.parse(fileText)
  } catch {
    return null
  }
  const found = resolvePath(root, path)
  if (found === undefined) return null
  return maskJsonText(JSON.stringify(found, null, 2)).replace(SSN_FORMAT, '•••-••-••••')
}

export const REASON_TEXT: Record<NotMigratedReason, { title: string; help: string }> = {
  sensitive: { title: 'Withheld: sensitive', help: 'Never part of a preview.' },
  separate_endpoint: { title: 'Needs a separate UKG call', help: 'Not in the create body; the documented schema puts it elsewhere.' },
  unsupported: { title: 'No field in the create request', help: 'Kept in the canonical record; the documented create body has nowhere to put it.' },
  not_confirmed: { title: 'Waiting for your confirmation', help: 'Not sent unless you confirm it above.' },
  uncertain: { title: 'Left for a person to decide', help: 'Mapping it would mean guessing.' },
}
