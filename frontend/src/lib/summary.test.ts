import { describe, expect, it } from 'vitest'
import { normalizeResponse, record } from '../test/utils'
import { filledFields, summarize } from './summary'

describe('summarize', () => {
  it('computes every figure from the response', () => {
    const res = normalizeResponse({
      records: [
        record({ extras: { a: 1, b: { c: 'x', d: '' } }, provenance: { source_endpoint: null, source_path: null, redacted_paths: ['$.ssn'], derived_fields: ['gross_pay'] } }),
        record({ source_id: 'E2', last_name: null, first_name: null }),
      ],
      issues: [
        { severity: 'error', code: 'x', message: 'm', path: null, source_id: null },
        { severity: 'warning', code: 'y', message: 'm', path: null, source_id: null },
        { severity: 'info', code: 'z', message: 'm', path: null, source_id: null },
        { severity: 'info', code: 'z', message: 'm', path: null, source_id: null },
      ],
    })
    expect(summarize(res)).toMatchObject({ records: 2, mappedFields: 2, unmappedValues: 2, redacted: 1, derived: 1, errors: 1, warnings: 1, infos: 2 })
  })
  it('ignores envelope fields, null, empty strings and empty lists when counting mapped fields', () => {
    const r = record({ first_name: 'A', last_name: '', middle_name: null, earnings: [], org_assignments: [{ kind: 'k' }] })
    expect(filledFields(r).map(([k]) => k).sort()).toEqual(['first_name', 'org_assignments'])
  })
})
