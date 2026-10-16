import type { FileToSend } from '../api/client'
import type { DetectResponse, EntityId, FileDetection, SourceContext } from '../api/types'
import { checkJson } from './json'
import type { JsonCheck } from './json'

export interface LoadedFile {
  name: string
  text: string
  origin: 'example' | 'upload'
  check: JsonCheck
}

export const makeFile = (name: string, text: string, origin: LoadedFile['origin']): LoadedFile => ({
  name,
  text,
  origin,
  check: checkJson(text),
})

/** Only files that are valid JSON can be sent: the backend would reject the whole request otherwise. */
export function filesToSend(files: LoadedFile[], overrides: Record<string, EntityId>): FileToSend[] {
  return files.filter((f) => f.check.ok).map((f) => ({ name: f.name, text: f.text, entity: overrides[f.name] }))
}

/** Context ids that at least one detected record type needs, in a stable order. */
export const CONTEXT_ORDER = ['company_id', 'employee_id', 'payroll_id'] as const

export function requiredIds(files: FileDetection[]): string[] {
  const needed = new Set<string>()
  for (const f of files) if (f.detection.status === 'ok') for (const u of f.detection.units) u.requires.forEach((r) => needed.add(r))
  return CONTEXT_ORDER.filter((k) => needed.has(k))
}

export function convertibleUnits(det: DetectResponse | undefined): number {
  return (det?.files ?? []).reduce((n, f) => n + (f.detection.status === 'ok' ? f.detection.units.length : 0), 0)
}

export function missingIds(files: FileDetection[], ctx: SourceContext): string[] {
  return requiredIds(files).filter((k) => !((ctx as unknown as Record<string, string | undefined>)[k] ?? '').trim())
}

export const FIELD_LABEL: Record<string, string> = {
  company_id: 'Employer ID',
  employee_id: 'Employee ID',
  payroll_id: 'Payroll run ID',
}

export function countsText(counts: Record<string, number>): string {
  const parts = Object.entries(counts).map(([k, n]) => `${n} ${k.replace(/_/g, ' ')}${n === 1 ? '' : 's'}`)
  return parts.join(', ')
}
