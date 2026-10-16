import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useRef, useState } from 'react'
import { ApiError, fetchFixtureIndex, fetchFixtureText, ingest, normalize } from '../api/client'
import type { EntityId, IngestResponse, NormalizeResponse, ProviderId, SourceContext } from '../api/types'
import { ENTITIES, PROVIDERS, providerLabel } from '../api/types'
import { JsonEditor } from '../components/JsonEditor'
import { IssuesTable, JsonBlock, RecordCard, StoredOutcome } from '../components/Records'
import { Badge, EmptyState, ErrorBanner, Field, InfoBanner, PageHeader, Spinner, Stat, Tabs } from '../components/ui'
import { CONTEXT_FIELDS, contextNeeds, missingContext } from '../lib/context'
import { checkJson, prettyJson } from '../lib/json'
import { maskJsonText, maskObject } from '../lib/mask'
import { SCENARIOS } from '../lib/scenarios'
import { summarize } from '../lib/summary'

type ResultTab = 'summary' | 'records' | 'compare' | 'issues' | 'raw'
const MAX_UPLOAD = 10 * 1024 * 1024

export function NormalizeAdvanced() {
  const queryClient = useQueryClient()
  const [provider, setProvider] = useState<ProviderId>('prismhr')
  const [entity, setEntity] = useState<EntityId>('employee')
  const [ctx, setCtx] = useState<SourceContext>({ tenant_id: 'console-demo', company_id: 'ACME' })
  const [source, setSource] = useState('')
  const [editing, setEditing] = useState(false)
  const [fixtureKey, setFixtureKey] = useState('')
  const [fixtureError, setFixtureError] = useState<string | null>(null)
  const [tab, setTab] = useState<ResultTab>('summary')
  const fileInput = useRef<HTMLInputElement>(null)

  const fixtures = useQuery({ queryKey: ['fixtures'], queryFn: fetchFixtureIndex })
  const dir = PROVIDERS.find((p) => p.id === provider)?.fixtureDir
  const providerFixtures = (fixtures.data ?? []).filter((f) => f.dir === dir)

  const preview = useMutation({ mutationFn: () => normalize(provider, entity, ctx, source) })
  const save = useMutation({
    mutationFn: () => ingest(provider, entity, ctx, source),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['stats'] }),
  })

  const check = useMemo(() => checkJson(source), [source])
  const missing = missingContext(provider, entity, ctx)
  const needs = contextNeeds(provider, entity)
  const canRun = check.ok && missing.length === 0 && !preview.isPending && !save.isPending

  const resetResults = () => {
    preview.reset()
    save.reset()
  }
  const changeSource = (text: string) => {
    setSource(text)
    resetResults()
  }

  const loadFixture = async (key: string) => {
    setFixtureKey(key)
    setFixtureError(null)
    if (!key) return
    const [fdir, name] = key.split('/')
    if (!fdir || !name) return
    try {
      changeSource(await fetchFixtureText({ dir: fdir, name }))
      setEditing(false)
      // If this fixture backs a known scenario for the chosen entity, pre-fill its context.
      const match = SCENARIOS.find((s) => s.provider === provider && s.entity === entity && s.fixture.dir === fdir && s.fixture.name === name)
      if (match) setCtx((c) => ({ tenant_id: c.tenant_id, ...match.context }))
    } catch (e) {
      setFixtureError(e instanceof ApiError ? e.message : 'Could not load the fixture.')
    }
  }

  const onUpload = async (file: File | undefined) => {
    if (!file) return
    setFixtureError(null)
    if (file.size > MAX_UPLOAD) {
      setFixtureError('File is larger than 10 MB.')
      return
    }
    setFixtureKey('')
    changeSource(await file.text())
    setEditing(false)
  }

  const switchProvider = (p: ProviderId) => {
    setProvider(p)
    setFixtureKey('')
    resetResults()
  }

  const result: NormalizeResponse | undefined = preview.data?.body
  const saved: IngestResponse | undefined = save.data?.body
  const summary = useMemo(() => (result ? summarize(result) : null), [result])
  const maskedSource = useMemo(() => maskJsonText(source), [source])
  const resultTabs = [
    { id: 'summary' as const, label: 'Summary' },
    { id: 'records' as const, label: 'Records', badge: result?.records.length },
    { id: 'compare' as const, label: 'Source ↔ canonical' },
    { id: 'issues' as const, label: 'Issues', badge: result?.issues.length },
    { id: 'raw' as const, label: 'Raw response' },
  ]

  return (
    <div>
      <PageHeader
        title="Advanced mode"
        subtitle="The full technical view: tenant context, raw JSON, provenance and every validation issue. Load a synthetic provider response, normalize it through the real API, inspect how each source field became a canonical field, and optionally save it to PostgreSQL."
      />

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        {/* ------------------------------------------------------------ input column */}
        <section className="space-y-4" aria-label="Input">
          <div className="card space-y-4 p-4">
            <div>
              <span className="label">Provider</span>
              <div role="radiogroup" aria-label="Provider selection" className="flex flex-wrap gap-2">
                {PROVIDERS.map((p) => (
                  <button key={p.id} role="radio" aria-checked={provider === p.id} onClick={() => switchProvider(p.id)}
                    className={`rounded-lg px-3 py-1.5 text-sm font-medium ring-1 ring-inset ${provider === p.id ? 'bg-brand-600 text-white ring-brand-600' : 'bg-white text-slate-700 ring-slate-300 hover:bg-slate-50'}`}>
                    {p.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Entity type" htmlFor="entity">
                <select id="entity" className="input" value={entity} onChange={(e) => { setEntity(e.target.value as EntityId); resetResults() }}>
                  {ENTITIES.map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
                </select>
              </Field>
              <Field label="Synthetic fixture" htmlFor="fixture" hint={fixtures.isError ? undefined : 'From tests/fixtures in this repository'}>
                <select id="fixture" className="input" value={fixtureKey} onChange={(e) => void loadFixture(e.target.value)} disabled={fixtures.isPending || fixtures.isError}>
                  <option value="">{fixtures.isPending ? 'Loading fixtures…' : 'Choose a fixture…'}</option>
                  {providerFixtures.map((f) => <option key={`${f.dir}/${f.name}`} value={`${f.dir}/${f.name}`}>{f.name}</option>)}
                </select>
              </Field>
            </div>
            {fixtures.isError && <InfoBanner tone="amber">{(fixtures.error as Error).message} You can still paste or upload JSON.</InfoBanner>}
            {fixtureError && <ErrorBanner title="Could not load input">{fixtureError}</ErrorBanner>}
            <div className="flex flex-wrap items-center gap-2">
              <input ref={fileInput} type="file" accept="application/json,.json" className="sr-only" aria-label="Upload JSON file" data-testid="upload" onChange={(e) => { void onUpload(e.target.files?.[0]); e.target.value = '' }} />
              <button className="btn-secondary" onClick={() => fileInput.current?.click()}>Upload JSON…</button>
              <span className="text-xs text-slate-500">Files are read in your browser and never stored there.</span>
            </div>
          </div>

          <div className="card flex flex-wrap items-center gap-3 p-4">
            <button className="btn-primary" disabled={!canRun} onClick={() => { save.reset(); preview.mutate() }}>
              {preview.isPending ? <Spinner label="Normalizing" /> : 'Normalize (preview)'}
            </button>
            <button className="btn-secondary" disabled={!canRun} onClick={() => save.mutate()} title="Writes to the local PostgreSQL database under this tenant">
              {save.isPending ? <Spinner label="Saving" /> : 'Save to PostgreSQL'}
            </button>
            {missing.length > 0 && <span className="text-xs text-amber-700">Required context missing: {missing.join(', ')}</span>}
            {!check.ok && source.trim() !== '' && <span className="text-xs text-amber-700">Fix the JSON first.</span>}
          </div>

          <div className="card space-y-3 p-4">
            <h2 className="text-sm font-semibold text-slate-800">Context <span className="font-normal text-slate-500">(ids the provider response does not contain)</span></h2>
            <div className="grid gap-3 sm:grid-cols-2">
              {CONTEXT_FIELDS.map((f) => {
                const isRequired = needs.required.includes(f.key)
                const isConditional = needs.conditional.includes(f.key)
                return (
                  <Field key={f.key} label={f.label} required={isRequired} hint={isConditional ? `Needed for some ${providerLabel(provider)} response shapes. ${f.hint}` : f.hint} htmlFor={`ctx-${f.key}`}>
                    <input id={`ctx-${f.key}`} className="input" value={ctx[f.key] ?? ''} autoComplete="off" spellCheck={false}
                      onChange={(e) => { setCtx({ ...ctx, [f.key]: e.target.value }); resetResults() }} />
                  </Field>
                )
              })}
            </div>
          </div>

          <div className="card p-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-sm font-semibold text-slate-800">Source JSON</h2>
              <div className="flex items-center gap-2">
                {source.trim() === '' ? null : check.ok ? <Badge tone="green">valid JSON</Badge> : <Badge tone="red">invalid JSON</Badge>}
                <button className="btn-secondary !py-1 text-xs" onClick={() => setEditing(!editing)} aria-pressed={editing}>
                  {editing ? 'Done editing' : 'Edit input'}
                </button>
              </div>
            </div>
            {editing ? (
              <InfoBanner tone="amber">Editing the raw input, which may contain synthetic sensitive values. Nothing here is saved in the browser.</InfoBanner>
            ) : (
              <p className="mb-2 text-xs text-slate-500">Sensitive values (SSNs, account numbers, document contents) are masked in this view. The request still sends the original text.</p>
            )}
            <div className="mt-2">
              <JsonEditor key={editing ? 'edit' : 'view'} ariaLabel="Source JSON" height={380} readOnly={!editing} value={editing ? source : maskedSource} onChange={editing ? changeSource : undefined} />
            </div>
            {source.trim() !== '' && !check.ok && (
              <p role="alert" className="mt-2 text-sm text-rose-700" data-testid="json-error">JSON error: {check.message}</p>
            )}
            {source.trim() === '' && <p className="mt-2 text-sm text-slate-500">Pick a fixture, upload a file, or choose “Edit input” and paste JSON.</p>}
          </div>

        </section>

        {/* ------------------------------------------------------------ result column */}
        <section className={`min-w-0 space-y-4 ${tab === 'compare' && result ? 'xl:col-span-2' : ''}`} aria-label="Result" aria-live="polite">
          {saved && (
            <div className="card space-y-3 p-4" data-testid="save-result">
              <div className="flex items-center gap-2">
                <h2 className="text-sm font-semibold text-slate-800">Database result</h2>
                {save.data?.status === 200 ? <Badge tone="green">saved</Badge> : <Badge tone="red">not saved</Badge>}
                <span className="text-xs text-slate-500">tenant <code>{ctx.tenant_id}</code> · provider <code>{providerLabel(provider)}</code></span>
              </div>
              {save.data?.status === 200 ? <StoredOutcome stored={saved.stored} /> : (
                <>
                  <ErrorBanner title="Nothing was written">The payload has errors. The whole call is rejected so a payload is never half-loaded.</ErrorBanner>
                  <IssuesTable issues={saved.issues} />
                </>
              )}
            </div>
          )}
          {save.error && <ErrorBanner title="Save failed">{(save.error as Error).message}</ErrorBanner>}
          {preview.error && <ErrorBanner title="Normalization request failed">{(preview.error as Error).message}</ErrorBanner>}

          {preview.isPending && <div className="card p-6"><Spinner label="Calling POST /normalize" /></div>}

          {!result && !preview.isPending && !preview.error && !saved && (
            <EmptyState title="No result yet">Choose a fixture and press “Normalize (preview)”. Nothing is stored until you press “Save to PostgreSQL”.</EmptyState>
          )}

          {result && summary && (
            <div className="space-y-4">
              <Tabs tabs={resultTabs} value={tab} onChange={setTab} label="Result views" />
              {tab === 'summary' && (
                <div className="space-y-4" data-testid="summary">
                  <div className="flex items-center gap-2">
                    {result.ok ? <Badge tone="green">normalized without errors</Badge> : <Badge tone="red">{summary.errors} error{summary.errors === 1 ? '' : 's'}: this payload cannot be saved</Badge>}
                    <span className="text-sm text-slate-500">HTTP {preview.data?.status}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                    <Stat label="Canonical records" value={summary.records} sub={Object.entries(summary.byType).map(([k, v]) => `${v} ${k.replace('_', ' ')}`).join(', ') || 'none'} />
                    <Stat label="Mapped fields" value={summary.mappedFields} sub="non-empty canonical fields" />
                    <Stat label="Kept unmapped" value={summary.unmappedValues} sub="values preserved in extras" />
                    <Stat label="Withheld" value={summary.redacted} sub="sensitive, not stored" />
                    <Stat label="Derived" value={summary.derived} sub="computed by the adapter" />
                    <Stat label="Issues" value={`${summary.errors}/${summary.warnings}/${summary.infos}`} sub="errors / warnings / info" />
                  </div>
                  <p className="text-xs text-slate-500">All figures are computed from the API response. “Mapped” counts fields that hold a value; “unmapped” counts source values with no dedicated canonical field.</p>
                  {result.issues.length > 0 && <IssuesTable issues={result.issues.slice(0, 8)} />}
                </div>
              )}
              {tab === 'records' && (
                <div className="space-y-3" data-testid="records">
                  {result.records.length === 0 ? <EmptyState title="No records produced">See the Issues tab for the reason.</EmptyState> : result.records.map((r, i) => <RecordCard key={`${r.record_type}-${r.source_id}-${i}`} record={r} defaultOpen={i === 0} />)}
                </div>
              )}
              {tab === 'compare' && (
                <div className="grid gap-3 lg:grid-cols-2" data-testid="compare">
                  <div><p className="label">Source (masked)</p><JsonEditor ariaLabel="Compare source" readOnly height={520} value={maskedSource} /></div>
                  <div><p className="label">Canonical records</p><JsonEditor ariaLabel="Compare canonical" readOnly height={520} value={prettyJson(maskObject(result.records))} /></div>
                </div>
              )}
              {tab === 'issues' && <IssuesTable issues={result.issues} />}
              {tab === 'raw' && <JsonBlock value={result} maxHeight={560} />}
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
