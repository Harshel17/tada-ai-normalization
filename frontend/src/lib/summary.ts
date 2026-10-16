import type { CanonicalRecord, NormalizeResponse } from '../api/types'
import { leafCount } from './json'

const ENVELOPE = new Set(['record_type', 'provider', 'tenant_id', 'company_id', 'source_id', 'extras', 'provenance'])

const isFilled = (v: unknown): boolean => {
  if (v === null || v === undefined || v === '') return false
  if (Array.isArray(v)) return v.length > 0
  if (typeof v === 'object') return Object.values(v as object).some(isFilled)
  return true
}

/** Canonical fields (beyond the envelope) that actually hold a value on this record. */
export function filledFields(record: CanonicalRecord): [string, unknown][] {
  return Object.entries(record).filter(([k, v]) => !ENVELOPE.has(k) && isFilled(v))
}

export function emptyFieldCount(record: CanonicalRecord): number {
  return Object.entries(record).filter(([k, v]) => !ENVELOPE.has(k) && !isFilled(v)).length
}

export interface Summary {
  records: number
  byType: Record<string, number>
  mappedFields: number
  unmappedValues: number
  redacted: number
  derived: number
  errors: number
  warnings: number
  infos: number
}

/** Everything is computed from the response; nothing is estimated. */
export function summarize(res: NormalizeResponse): Summary {
  const s: Summary = {
    records: res.records.length,
    byType: res.counts,
    mappedFields: 0,
    unmappedValues: 0,
    redacted: 0,
    derived: 0,
    errors: 0,
    warnings: 0,
    infos: 0,
  }
  for (const r of res.records) {
    s.mappedFields += filledFields(r).length
    s.unmappedValues += leafCount(r.extras)
    s.redacted += r.provenance.redacted_paths.length
    s.derived += r.provenance.derived_fields.length
  }
  for (const i of res.issues) {
    if (i.severity === 'error') s.errors++
    else if (i.severity === 'warning') s.warnings++
    else s.infos++
  }
  return s
}
