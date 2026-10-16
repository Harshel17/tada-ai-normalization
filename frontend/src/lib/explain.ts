import type { Issue, NormalizeResponse } from '../api/types'
import { leafCount } from './json'
import { filledFields } from './summary'

export type IssueKind = 'problem' | 'not-understood' | 'provider-gap' | 'note'

interface Explained {
  kind: IssueKind
  text: string
}

const INVALID = 'A value did not look like a valid {x}, so it was not converted. It is kept exactly as received.'

/** Plain-English meaning of each issue code the API can return (see the adapters). */
const CODES: Record<string, Explained> = {
  unmapped_code: { kind: 'not-understood', text: 'The provider sent a code its documentation does not explain. It is kept exactly as received instead of being guessed.' },
  invalid_decimal: { kind: 'not-understood', text: INVALID.replace('{x}', 'number') },
  invalid_integer: { kind: 'not-understood', text: INVALID.replace('{x}', 'whole number') },
  invalid_date: { kind: 'not-understood', text: INVALID.replace('{x}', 'date') },
  invalid_datetime: { kind: 'not-understood', text: INVALID.replace('{x}', 'date and time') },
  invalid_boolean: { kind: 'not-understood', text: INVALID.replace('{x}', 'yes/no value') },
  unexpected_type: { kind: 'not-understood', text: 'A field had a different shape than the provider documents, so it was left out of the conversion.' },
  conflicting_fields: { kind: 'note', text: 'Two fields that should agree do not. One was used and the other is kept so nothing is lost.' },
  earnings_total_mismatch: { kind: 'note', text: 'The earnings lines do not add up to the total the provider reports. Worth checking in the source.' },
  relationship_mismatch: { kind: 'note', text: 'This record appears to belong to a different employee than the one it was filed under.' },
  multiple_managers: { kind: 'note', text: 'Several managers were listed. The first is used; the others are kept as extra data.' },
  run_dates_inconsistent: { kind: 'note', text: 'Paychecks in the same batch disagree on a date, so that date was left blank.' },
  runs_derived: { kind: 'provider-gap', text: 'This provider does not send a pay-run record. One was created from the paychecks’ batch id.' },
  net_pay_not_exposed: { kind: 'provider-gap', text: 'This provider does not report net pay on its pay statements, so it is left blank rather than calculated.' },
  company_master_unavailable: { kind: 'provider-gap', text: 'This provider only exposes the employer’s tax entities, not its name, address or status.' },
  no_direct_deposit_details: { kind: 'provider-gap', text: 'The employee record did not include bank deposit details.' },
  no_run_link: { kind: 'note', text: 'No payroll run ID was given, so the paycheck is not linked to a pay run.' },
  provider_error_message: { kind: 'note', text: 'The provider’s response contained an error message.' },
  unrecognized_shape: { kind: 'problem', text: 'This entry is not in a shape this converter recognises.' },
  no_records: { kind: 'problem', text: 'The file contained no records to convert.' },
  missing_id: { kind: 'problem', text: 'A record has no ID, so it cannot be converted or stored safely.' },
  missing_context: { kind: 'problem', text: 'An ID that the provider’s file does not contain was not supplied.' },
  invalid_item: { kind: 'problem', text: 'An entry in a list was not a record.' },
  invalid_payload: { kind: 'problem', text: 'The file is not the kind of response this converter expects.' },
  unsupported_entity: { kind: 'problem', text: 'This kind of data is not supported for this provider.' },
  canonical_validation: { kind: 'problem', text: 'A converted record failed the common format’s own validation.' },
}

export function explainIssue(i: Issue): Explained {
  return CODES[i.code] ?? { kind: i.severity === 'error' ? 'problem' : 'note', text: i.message }
}

export interface DataStory {
  records: number
  /** Values placed into a dedicated field of the common format. */
  convertedFields: number
  /** Original values with no dedicated field, kept exactly as received. */
  preservedValues: number
  /** Sensitive values deliberately not stored. */
  withheld: number
  notUnderstood: Issue[]
  providerGaps: Issue[]
  problems: Issue[]
  notes: Issue[]
}

/** Everything is counted from the response; nothing is estimated. */
export function dataStory(res: NormalizeResponse): DataStory {
  const story: DataStory = {
    records: res.records.length, convertedFields: 0, preservedValues: 0, withheld: 0,
    notUnderstood: [], providerGaps: [], problems: [], notes: [],
  }
  for (const r of res.records) {
    story.convertedFields += filledFields(r).length
    story.preservedValues += leafCount(r.extras)
    story.withheld += r.provenance.redacted_paths.length
  }
  for (const i of res.issues) {
    const k = explainIssue(i).kind
    if (k === 'not-understood') story.notUnderstood.push(i)
    else if (k === 'provider-gap') story.providerGaps.push(i)
    else if (k === 'problem' || i.severity === 'error') story.problems.push(i)
    else story.notes.push(i)
  }
  return story
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`

/** One-sentence headline after a conversion, from the response. */
export function headline(res: NormalizeResponse, noun: string): string {
  if (!res.ok) return 'We could not convert this data. See what needs fixing below.'
  const n = res.records.length
  return n === 0 ? 'Nothing was converted.' : `Converted ${plural(n, noun)} into one common format.`
}

export { plural }
