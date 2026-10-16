import { describe, expect, it } from 'vitest'
import { normalizeResponse, record } from '../test/utils'
import type { Issue } from '../api/types'
import { dataStory, explainIssue, headline } from './explain'

const issue = (code: string, severity: Issue['severity'] = 'info', message = 'm'): Issue => ({ severity, code, message, path: '$.x', source_id: null })

describe('explainIssue', () => {
  it.each([
    ['unmapped_code', 'not-understood', /documentation does not explain/],
    ['invalid_date', 'not-understood', /valid date/],
    ['invalid_decimal', 'not-understood', /valid number/],
    ['runs_derived', 'provider-gap', /does not send a pay-run/],
    ['net_pay_not_exposed', 'provider-gap', /does not report net pay/],
    ['conflicting_fields', 'note', /should agree/],
    ['missing_id', 'problem', /no ID/],
  ])('%s is explained in plain English as %s', (code, kind, text) => {
    expect(explainIssue(issue(code))).toMatchObject({ kind })
    expect(explainIssue(issue(code)).text).toMatch(text)
  })
  it('falls back to the server’s own message for a code it does not know, and treats errors as problems', () => {
    expect(explainIssue(issue('brand_new', 'warning', 'Something odd'))).toEqual({ kind: 'note', text: 'Something odd' })
    expect(explainIssue(issue('brand_new', 'error', 'Broken')).kind).toBe('problem')
  })
})

describe('dataStory', () => {
  it('counts converted, preserved, withheld and uninterpretable items from the response only', () => {
    const res = normalizeResponse({
      records: [
        record({ extras: { a: 1, b: { c: 2 } }, provenance: { source_endpoint: null, source_path: null, redacted_paths: ['$.ssn'], derived_fields: [] } }),
        record({ source_id: 'E2', first_name: null, last_name: null }),
      ],
      issues: [issue('unmapped_code'), issue('invalid_date', 'warning'), issue('runs_derived'), issue('missing_id', 'error'), issue('conflicting_fields', 'warning')],
    })
    const s = dataStory(res)
    expect(s).toMatchObject({ records: 2, convertedFields: 2, preservedValues: 2, withheld: 1 })
    expect(s.notUnderstood).toHaveLength(2)
    expect(s.providerGaps).toHaveLength(1)
    expect(s.problems).toHaveLength(1)
    expect(s.notes).toHaveLength(1)
  })
  it('reports zeros for an empty result rather than inventing figures', () => {
    expect(dataStory(normalizeResponse({ records: [], counts: {} }))).toMatchObject({ records: 0, convertedFields: 0, preservedValues: 0, withheld: 0 })
  })
})

describe('headline', () => {
  it('reflects the real outcome', () => {
    expect(headline(normalizeResponse(), 'employee')).toBe('Converted 1 employee into one common format.')
    expect(headline(normalizeResponse({ records: [record(), record({ source_id: 'E2' })] }), 'employee')).toBe('Converted 2 employees into one common format.')
    expect(headline(normalizeResponse({ ok: false }), 'employee')).toMatch(/could not convert/)
    expect(headline(normalizeResponse({ records: [], counts: {} }), 'employee')).toBe('Nothing was converted.')
  })
})
