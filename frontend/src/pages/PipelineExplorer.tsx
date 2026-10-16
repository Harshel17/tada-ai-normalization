import { useMemo, useState } from 'react'
import type { TraceStep, UnitResult } from '../api/types'
import { providerLabel } from '../api/types'
import { Badge, EmptyState } from '../components/ui'
import { recordHeading } from '../lib/describe'
import { ENTITY_PLAIN } from '../lib/examples'
import {
  coverageOf, defaultSelection, describeStep, displayValue, filterSteps, groupSteps, isMeaningful, OP_INFO, opCounts, relativeSource, safeStep, unmappedLeaves, untracedExplanation,
} from '../lib/pipeline'
import { useConversion } from '../state/conversion'

const GRID = 'md:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(0,1.2fr)]'

export function PipelineExplorer() {
  const { conversion } = useConversion()
  const units = useMemo(() => (conversion?.results ?? []).filter((u) => u.status === 'converted' && u.records.length > 0), [conversion])
  const initial = useMemo(() => defaultSelection(units), [units])
  // null = "not chosen yet": the default is derived while rendering, so it follows a new conversion.
  const [chosen, setChosen] = useState<{ unit: number; record: number; for: unknown } | null>(null)
  const sel = chosen && chosen.for === conversion ? chosen : { ...initial, for: conversion }
  const [query, setQuery] = useState('')
  const [op, setOp] = useState<TraceStep['op'] | 'all'>('all')
  const [onlyMeaningful, setOnlyMeaningful] = useState(false)

  const unit: UnitResult | undefined = units[Math.min(sel.unit, units.length - 1)]
  const recIdx = Math.min(sel.record, (unit?.records.length ?? 1) - 1)
  const record = unit?.records[recIdx]
  const steps = useMemo<TraceStep[]>(() => (unit?.traces[recIdx] ?? []).map(safeStep), [unit, recIdx])
  const visible = useMemo(() => filterSteps(steps, query, op).filter((s) => !onlyMeaningful || isMeaningful(s)), [steps, query, op, onlyMeaningful])
  const groups = useMemo(() => groupSteps(visible, record?.record_type), [visible, record])
  const unmapped = useMemo(() => (record ? unmappedLeaves(record, steps) : []), [record, steps])
  const coverage = useMemo(() => (record ? coverageOf(record, steps) : { traced: 0, total: 0 }), [record, steps])

  if (!conversion || units.length === 0 || !unit || !record) {
    return (
      <div data-testid="pipeline">
        <h1 className="mb-6 text-2xl font-semibold tracking-tight text-slate-900">Pipeline Explorer</h1>
        <EmptyState title="Nothing to explore yet" action={<a className="btn-primary" href="#/normalize">Go to Normalize</a>}>
          Normalize some data first. This page then shows, field by field, where each value came from and what was done to it.
        </EmptyState>
      </div>
    )
  }

  const q = query.trim().toLowerCase()
  const shownUnmapped = unmapped.filter((l) => !q || `${l.path} ${String(l.value)}`.toLowerCase().includes(q))
  const redacted = record.provenance.redacted_paths ?? []
  const preserved = Object.keys(record.extras ?? {})
  const pct = coverage.total ? Math.round((coverage.traced / coverage.total) * 100) : 0
  const pick = (u: number, r: number) => setChosen({ unit: u, record: r, for: conversion })

  return (
    <div data-testid="pipeline" className="space-y-6">
      <header className="space-y-4" data-testid="pipeline-header">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">Pipeline Explorer</h1>
          <p className="mt-1 text-sm text-slate-500">How each original field became a standard field. Only mappings recorded during the conversion are shown.</p>
        </div>
        <dl className="grid gap-x-8 gap-y-3 rounded-xl border border-slate-200 bg-slate-50 p-4 sm:grid-cols-2 lg:grid-cols-5" aria-label="Conversion summary">
          <Fact label="Provider">{providerLabel(unit.records[0]?.provider ?? conversion.provider)}</Fact>
          <Fact label="Data">{ENTITY_PLAIN[unit.entity].title}</Fact>
          <Fact label="Record"><span data-testid="selected-record">{recordHeading(record)}</span></Fact>
          <Fact label="Status"><Badge tone={unit.issues.some((i) => i.severity === 'error') ? 'red' : 'green'}>{unit.issues.some((i) => i.severity === 'error') ? 'has errors' : 'converted'}</Badge></Fact>
          <Fact label="Mapping recorded">
            <span data-testid="coverage">{coverage.traced} of {coverage.total} fields{coverage.total ? ` (${pct}%)` : ''}</span>
            <span className="mt-1 block h-1.5 overflow-hidden rounded-full bg-slate-200" aria-hidden><span className="block h-full bg-brand-500" style={{ width: `${pct}%` }} /></span>
          </Fact>
        </dl>

        <div className="flex flex-wrap items-center gap-3">
          {units.length > 1 && (
            <label className="text-sm text-slate-600">
              Data{' '}
              <select className="input inline-block w-auto" aria-label="Choose converted data" value={Math.min(sel.unit, units.length - 1)}
                onChange={(e) => { const u = Number(e.target.value); setOp('all'); setChosen({ ...defaultSelection([units[u]!]), unit: u, for: conversion }) }}>
                {units.map((u, i) => <option key={`${u.file}-${u.entity}`} value={i}>{ENTITY_PLAIN[u.entity].title} · {u.file}</option>)}
              </select>
            </label>
          )}
          <div role="tablist" aria-label="Records" className="flex flex-wrap gap-1.5">
            {unit.records.map((r, i) => {
              const has = (unit.traces[i]?.length ?? 0) > 0
              return (
                <button key={`${r.source_id}-${i}`} role="tab" aria-selected={i === recIdx} onClick={() => pick(sel.unit, i)} title={has ? undefined : 'No source mapping recorded for this record'}
                  className={`rounded-lg px-3 py-1.5 text-sm font-medium ring-1 ring-inset ${i === recIdx ? 'bg-brand-600 text-white ring-brand-600' : 'bg-white text-slate-700 ring-slate-300 hover:bg-slate-50'} ${has ? '' : 'opacity-70'}`}>
                  {recordHeading(r)}
                </button>
              )
            })}
          </div>
        </div>
      </header>

      <section aria-label="Filters" className="flex flex-wrap items-center gap-2">
        <input className="input max-w-xs" type="search" placeholder="Search fields or values" aria-label="Search the pipeline" value={query} onChange={(e) => setQuery(e.target.value)} />
        <div role="group" aria-label="Filter by what was done" className="flex flex-wrap gap-1">
          <Chip active={op === 'all'} onClick={() => setOp('all')}>All ({steps.length})</Chip>
          {opCounts(steps).map(([k, n]) => <Chip key={k} active={op === k} onClick={() => setOp(k)}>{OP_INFO[k].title} ({n})</Chip>)}
        </div>
        <label className="ml-auto flex items-center gap-2 text-sm text-slate-600">
          <input type="checkbox" checked={onlyMeaningful} onChange={(e) => setOnlyMeaningful(e.target.checked)} /> Only real conversions
        </label>
      </section>

      {steps.length === 0 && (
        <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900" data-testid="no-trace">
          Source mapping is not available for this kind of record yet, so no original-to-standard links are shown. The converted values are still correct.
        </p>
      )}

      {steps.length > 0 && (
        <div>
          <div className={`hidden gap-3 px-3 pb-2 text-xs font-semibold uppercase tracking-wide text-slate-500 md:grid ${GRID}`} aria-hidden>
            <span>Original source</span><span>Transformation</span><span>Standard output</span>
          </div>
          <div className="space-y-4" data-testid="pipeline-rows">
            {groups.map(({ group, steps: gs }) => (
              <details key={group} open className="rounded-xl border border-slate-200 bg-white" data-testid={`group-${group}`}>
                <summary className="flex cursor-pointer items-center justify-between rounded-xl px-4 py-2.5 text-sm font-semibold text-slate-800">
                  <span>{group}</span>
                  <span className="flex items-center gap-2 font-normal text-slate-500">
                    {gs.filter(isMeaningful).length > 0 && <Badge tone="indigo">{gs.filter(isMeaningful).length} converted</Badge>}
                    {gs.length} field{gs.length === 1 ? '' : 's'}
                  </span>
                </summary>
                <ol className="divide-y divide-slate-100 border-t border-slate-100" aria-label={`${group} mappings`}>
                  {gs.map((s) => <Row key={s.dest} step={s} recordPath={record.provenance.source_path} />)}
                </ol>
              </details>
            ))}
            {groups.length === 0 && <p className="text-sm text-slate-500">No fields match your search or filters.</p>}
          </div>
        </div>
      )}

      <details className="rounded-xl border border-slate-200 bg-white p-4" data-testid="unmapped">
        <summary className="cursor-pointer text-sm font-semibold text-slate-800">Source mapping not available ({shownUnmapped.length})</summary>
        <p className="my-2 text-xs text-slate-500" data-testid="untraced-why">{untracedExplanation(unmapped)}</p>
        <dl className="grid grid-cols-1 gap-x-6 gap-y-1 text-sm sm:grid-cols-[minmax(12rem,20rem)_1fr]">
          {shownUnmapped.map((l) => (
            <div key={l.path} className="contents"><dt className="font-mono text-xs text-slate-500">{l.path}</dt><dd className="break-words font-mono text-slate-900">{displayValue(l.value)}</dd></div>
          ))}
        </dl>
      </details>

      <div className="grid gap-3 md:grid-cols-2">
        <section className="rounded-xl border border-slate-200 bg-white p-4 text-sm" data-testid="withheld">
          <h3 className="font-semibold text-slate-800">Withheld for safety ({redacted.length})</h3>
          {redacted.length ? <ul className="mt-1 list-disc pl-5 font-mono text-xs text-slate-600">{redacted.map((p) => <li key={p}>{relativeSource(p, record.provenance.source_path)}</li>)}</ul>
            : <p className="text-slate-500">No sensitive fields in this record.</p>}
        </section>
        <section className="rounded-xl border border-slate-200 bg-white p-4 text-sm" data-testid="preserved">
          <h3 className="font-semibold text-slate-800">Kept as received, not mapped ({preserved.length})</h3>
          {preserved.length ? <p className="mt-1 break-words font-mono text-xs text-slate-600">{preserved.join(', ')}</p>
            : <p className="text-slate-500">Every original field has a standard field.</p>}
        </section>
      </div>
    </div>
  )
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs font-semibold uppercase tracking-wide text-slate-500">{label}</dt>
      <dd className="mt-0.5 truncate text-sm font-medium text-slate-900">{children}</dd>
    </div>
  )
}

function Row({ step, recordPath }: { step: TraceStep; recordPath: string | null }) {
  const [open, setOpen] = useState(false)
  const notable = isMeaningful(step)
  const id = `row-${step.dest.replace(/\W/g, '-')}`
  return (
    <li data-testid="pipeline-row" className={notable ? 'border-l-4 border-l-indigo-300 bg-indigo-50/30' : ''}>
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}
        className={`grid w-full gap-1 px-3 py-2 text-left hover:bg-slate-50 md:items-center md:gap-3 ${GRID}`}>
        <span className="min-w-0" data-testid="origin">
          {step.sources.length === 0 && <span className="block text-xs italic text-slate-500" data-testid="no-source">No source field</span>}
          {step.sources.map((x) => (
            <span key={x.path} className="block min-w-0">
              <span className="block truncate font-mono text-xs text-slate-500" title={x.path}>{relativeSource(x.path, recordPath)}</span>
              <span className={`block truncate font-mono ${notable ? 'text-sm text-slate-900' : 'text-xs text-slate-600'}`}>{displayValue(x.raw)}</span>
            </span>
          ))}
        </span>
        <span className="min-w-0">
          <Badge tone={notable ? OP_INFO[step.op].tone : 'slate'}>{OP_INFO[step.op].title}</Badge>
          {notable && (step.table || step.note) && <span className="mt-0.5 block truncate text-xs text-slate-500" title={step.note ?? step.table}>{step.table ?? step.note}</span>}
        </span>
        <span className="min-w-0" data-testid="destination">
          <span className="block truncate font-mono text-xs text-slate-500">{step.dest}</span>
          <span className={`block truncate font-mono ${notable ? 'text-sm font-semibold text-slate-900' : 'text-xs text-slate-700'}`}>{displayValue(step.value)}</span>
        </span>
      </button>
      {open && (
        <div id={id} className="space-y-1 bg-slate-50 px-4 py-3 text-xs text-slate-700" data-testid="row-details">
          <p><strong>What was done:</strong> {describeStep(step)}</p>
          {step.table && <p><strong>Lookup table:</strong> {step.table}</p>}
          {step.sources.map((x) => <p key={x.path} className="break-all"><strong>Source:</strong> <span className="font-mono">{x.path}</span> = <span className="font-mono">{displayValue(x.raw)}</span></p>)}
          <p className="break-all"><strong>Standard field:</strong> <span className="font-mono">{step.dest}</span> = <span className="font-mono">{displayValue(step.value)}</span></p>
        </div>
      )}
    </li>
  )
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button onClick={onClick} aria-pressed={active} className={`rounded-full px-3 py-1 text-xs font-medium ring-1 ring-inset ${active ? 'bg-slate-900 text-white ring-slate-900' : 'bg-white text-slate-600 ring-slate-300'}`}>
      {children}
    </button>
  )
}
