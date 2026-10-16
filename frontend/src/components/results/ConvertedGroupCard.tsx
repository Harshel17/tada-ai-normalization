import type { FileDetection, UnitResult } from '../../api/types'
import { ENTITY_PLAIN } from '../../lib/examples'
import { countsText, FIELD_LABEL } from '../../lib/flow'
import { storyFor } from '../../lib/results'
import { summarizeUnit } from '../../lib/unitSummary'
import { IssuesTable } from '../Records'
import { Badge } from '../ui'
import { EntityIcon } from './EntityIcon'
import { RecordSummary } from './RecordSummary'
import { StoryPanel } from './StoryPanel'

export interface AssignedOwner {
  id: string
  basis: 'documented' | 'confirmed'
}

/**
 * One converted section: a header and compact statistics that are informative before anything is opened, and the existing
 * detailed view (record tabs, plain-language fields, what happened to the data) behind "View full details".
 * Statistics come from the returned records; the owner line says where an owner came from and never calls an assigned
 * owner verified.
 */
export function ConvertedGroupCard({ unit, provider, all, detections, defaultOpen, assigned }: {
  unit: UnitResult
  provider: string
  all: UnitResult[]
  detections: FileDetection[]
  defaultOpen: boolean
  assigned: AssignedOwner | null
}) {
  const converted = unit.status === 'converted'
  const hasRecords = converted && unit.records.length > 0
  const summary = hasRecords ? summarizeUnit(unit, all, detections) : null
  const tone = unit.status === 'converted' ? 'green' : unit.status === 'failed' ? 'red' : 'amber'
  return (
    <li className="card overflow-hidden" data-testid={`unit-${unit.file}-${unit.entity}`}>
      <div className="p-4 sm:p-5">
        <div className="flex items-start gap-3">
          <EntityIcon entity={unit.entity} tone={converted ? 'brand' : 'slate'} />
          <div className="min-w-0 flex-1">
            <p className="text-[15px] font-semibold tracking-tight text-slate-900">{ENTITY_PLAIN[unit.entity].title}</p>
            <p className="break-all text-xs text-slate-500"><span className="sr-only">from </span>Source: {unit.file}</p>
          </div>
          <Badge tone={tone}>{unit.status === 'converted' ? 'converted' : unit.status === 'failed' ? 'has errors' : 'needs an identifier'}</Badge>
        </div>
        <p className="sr-only">{countsText(unit.counts) || 'No records'}</p>
        {unit.status === 'needs_context' && <p className="mt-3 text-sm text-amber-800">Needs: {unit.missing.map((k) => FIELD_LABEL[k] ?? k).join(', ')}. Nothing was guessed.</p>}
        {summary && summary.tiles.length > 0 && (
          <dl className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-3" aria-label="Summary">
            {summary.tiles.map((t) => (
              <div key={t.label} className="rounded-lg border border-slate-100 bg-slate-50/70 px-3 py-2">
                <dd className="num text-lg font-semibold leading-tight tracking-tight text-slate-900">{t.value}</dd>
                <dt className="text-xs text-slate-500">{t.label}</dt>
              </div>
            ))}
          </dl>
        )}
        {unit.status !== 'needs_context' && !hasRecords && <p className="mt-3 text-sm text-slate-500">No records in this section.</p>}
        {summary?.preview.map((line) => <p key={line} className="mt-3 break-words text-sm text-slate-700" data-testid="unit-preview">{line}</p>)}
        {summary?.owner === 'assigned' && (
          <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-relaxed text-amber-900" data-testid="unit-owner">
            {assigned
              ? assigned.basis === 'confirmed'
                ? `Owner assigned through your confirmation (employee ${assigned.id}); not independently verified from source data.`
                : `Owner (employee ${assigned.id}) taken from the example set’s convention, which the file does not state; not independently verified from source data.`
              : 'Owner assigned from the Employee ID supplied; not independently verified from source data.'}
          </p>
        )}
        {summary?.owner === 'in-file' && <p className="mt-3 text-xs text-slate-500" data-testid="unit-owner">The owning employee is named in the source file itself.</p>}
        {unit.issues.length > 0 && (
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-slate-700">{unit.issues.length} note{unit.issues.length === 1 ? '' : 's'}</summary>
            <IssuesTable issues={unit.issues} />
          </details>
        )}
      </div>
      {hasRecords && (
        <details className="group border-t border-slate-200" open={defaultOpen} data-testid={`found-${unit.file}-${unit.entity}`}>
          <summary className="flex cursor-pointer list-none items-center justify-between gap-3 bg-slate-50/70 px-4 py-3 text-sm font-semibold text-slate-800 transition-colors hover:bg-slate-100 sm:px-5 [&::-webkit-details-marker]:hidden">
            <span>What we found <span className="font-normal text-slate-500">({countsText(unit.counts)})</span></span>
            <span className="flex shrink-0 items-center gap-1 text-xs font-medium text-brand-700" aria-hidden>
              <span className="group-open:hidden">View full details</span>
              <span className="hidden group-open:inline">Hide details</span>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" className="transition-transform group-open:rotate-180"><path d="m6 9 6 6 6-6" /></svg>
            </span>
          </summary>
          <div className="space-y-5 border-t border-slate-100 p-4 sm:p-5">
            <RecordSummary records={unit.records} />
            <div>
              <h4 className="mb-2 text-sm font-semibold text-slate-800">What happened to your data</h4>
              <StoryPanel story={storyFor(provider, unit)} />
            </div>
          </div>
        </details>
      )}
    </li>
  )
}
