import type { EntityId, FileDetection } from '../api/types'

/** Plain words for the records an operator-supplied employee id would own. */
export const OWNED_NOUN: Partial<Record<EntityId, string>> = { direct_deposit: 'direct deposits', deduction: 'deductions' }

export interface OwnerUnit {
  file: string
  entity: EntityId
}

/** The detected sections whose records do not name their employee, so the Employee ID field decides it. */
export function ownerUnits(files: FileDetection[]): OwnerUnit[] {
  return files.flatMap((f) => (f.detection.status === 'ok' ? f.detection.units.filter((u) => u.requires.includes('employee_id')).map((u) => ({ file: f.name, entity: u.entity })) : []))
}

/** "deposits", "deductions" or "records", for the question being asked. */
export function ownedWords(units: OwnerUnit[]): string {
  const words = [...new Set(units.map((u) => OWNED_NOUN[u.entity]).filter(Boolean))]
  return words.length === 1 ? words[0]! : 'records'
}

export const ownerExplanation = (words: string): string =>
  `This file does not identify the employee who owns these ${words}. The employee ID you select will be assigned to records that do not provide their own owner. Confirm the correct employee before normalizing.`
