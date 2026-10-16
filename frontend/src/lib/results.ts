import type { CanonicalRecord, NormalizeResponse, UnitResult } from '../api/types'
import { describeRecord } from './describe'
import type { Section } from './describe'
import { dataStory } from './explain'
import type { DataStory } from './explain'
import { maskObject } from './mask'

/**
 * A converted section of a file in the shape the existing helpers (`dataStory`, `headline`) expect.
 * Everything comes from the API response; `ok` means "no error issue", exactly like the page's own check.
 */
export function unitAsResponse(provider: string, unit: UnitResult): NormalizeResponse {
  return {
    provider,
    entity_type: unit.entity,
    ok: unit.status === 'converted' && !unit.issues.some((i) => i.severity === 'error'),
    counts: unit.counts,
    issues: unit.issues,
    records: unit.records,
  }
}

/** Readable sections for one record. The record is masked first as a second line of defence. */
export function readableSections(record: CanonicalRecord): Section[] {
  return describeRecord(maskObject(record))
}

export const storyFor = (provider: string, unit: UnitResult): DataStory => dataStory(unitAsResponse(provider, unit))
