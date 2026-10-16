import type { ConvertResponse } from '../../api/types'
import { OWNED_NOUN } from '../../lib/ownership'

/**
 * Says, after normalizing, which records got their owner from the Employee ID supplied here, and why that is not verified.
 * It states what happened; it never presents the assignment as checked.
 */
export function OwnershipNotice({ conversion, employeeId, basis }: { conversion: ConvertResponse; employeeId: string; basis: 'documented' | 'confirmed' }) {
  const sections = conversion.results.filter((u) => u.status === 'converted' && u.records.length > 0)
  const needed = conversion.files.flatMap((f) => f.detection.units.filter((u) => u.requires.includes('employee_id')).map((u) => ({ file: f.name, entity: u.entity })))
  const assigned = needed
    .map((n) => ({ ...n, count: sections.find((u) => u.file === n.file && u.entity === n.entity)?.records.length ?? 0 }))
    .filter((n) => n.count > 0)
  if (assigned.length === 0 || !employeeId) return null
  return (
    <div role="note" data-testid="ownership-notice" className="rounded-xl border border-amber-300/80 bg-amber-50 p-4 text-sm leading-relaxed text-amber-950 shadow-card">
      <p className="font-semibold">Owner assigned from the Employee ID {basis === 'documented' ? 'used by this example set' : 'you confirmed'}</p>
      <ul className="mt-1 list-disc pl-5">
        {assigned.map((a) => (
          <li key={`${a.file}-${a.entity}`}>
            {a.count} {OWNED_NOUN[a.entity] ?? 'records'} in {a.file} {a.count === 1 ? 'was' : 'were'} assigned to employee {employeeId}, because the file does not name its owner.
          </li>
        ))}
      </ul>
      <p className="mt-2">
        {basis === 'documented'
          ? 'This is the example set’s own convention, not something the files state. '
          : 'This is your confirmation. '}
        It is an acknowledgment, not something the files or the integrity checks can verify: a check only shows that the employee exists in this data.
        Records that carry their own owner, such as deposits inside an employee record, keep it.
      </p>
    </div>
  )
}
