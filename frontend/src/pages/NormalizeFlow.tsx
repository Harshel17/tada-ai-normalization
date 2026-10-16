import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState } from 'react'
import { ApiError, convertFiles, detectFiles, fetchFixtureIndex, fetchFixtureText, ingest } from '../api/client'
import type { ConvertResponse, DetectResponse, EntityId, FileDetection, IdSuggestion, ProviderId, SourceContext, UnitResult } from '../api/types'
import { PROVIDERS } from '../api/types'
import { ProviderPicker } from '../components/guided/Pickers'
import { describeSave } from '../components/guided/SavePanel'
import { JsonBlock } from '../components/Records'
import { documentedOwner } from '../api/exampleOwners'
import { MigrationPanel } from '../components/migration/MigrationPanel'
import { IntegrityPanel } from '../components/results/IntegrityPanel'
import { ConvertedGroupCard } from '../components/results/ConvertedGroupCard'
import { EntityIcon } from '../components/results/EntityIcon'
import { OwnershipNotice } from '../components/results/OwnershipNotice'
import { ResultsOverview } from '../components/results/ResultsOverview'
import { Badge, ErrorBanner, Field, Spinner } from '../components/ui'
import { ENTITY_PLAIN } from '../lib/examples'
import { convertibleUnits, countsText, FIELD_LABEL, filesToSend, makeFile, missingIds, requiredIds } from '../lib/flow'
import type { LoadedFile } from '../lib/flow'
import { maskObject } from '../lib/mask'
import { ownedWords, ownerExplanation, ownerUnits } from '../lib/ownership'
import { SCENARIOS } from '../lib/scenarios'
import { useConversion } from '../state/conversion'

const MAX_UPLOAD = 10 * 1024 * 1024
const DEFAULT_WORKSPACE = 'normalize-demo'

const STATUS_TONE = { ok: 'green', ambiguous: 'amber', unsupported: 'red', empty: 'slate' } as const
const STATUS_TEXT = { ok: 'Recognised', ambiguous: 'Needs your confirmation', unsupported: 'Not recognised', empty: 'Empty' } as const

/** Ids the bundled examples use, offered as suggestions only (the user still has to confirm). */
function exampleSuggestions(provider: ProviderId): IdSuggestion[] {
  const out: IdSuggestion[] = []
  const seen = new Set<string>()
  for (const sc of SCENARIOS.filter((s) => s.provider === provider)) {
    for (const field of ['company_id', 'employee_id'] as const) {
      const value = sc.context[field]
      if (value && !seen.has(`${field}:${value}`)) {
        seen.add(`${field}:${value}`)
        out.push({ field, value, source_file: 'the bundled examples', why: 'the id these examples were written with' })
      }
    }
  }
  return out
}

export function NormalizeFlow() {
  const queryClient = useQueryClient()
  const { setConversion } = useConversion()
  const [provider, setProvider] = useState<ProviderId | null>(null)
  const [files, setFiles] = useState<LoadedFile[]>([])
  const [overrides, setOverrides] = useState<Record<string, EntityId>>({})
  const [rev, setRev] = useState(0)
  const [ctx, setCtx] = useState<SourceContext>({ tenant_id: DEFAULT_WORKSPACE })
  const [loadError, setLoadError] = useState<string | null>(null)
  const [loadingSet, setLoadingSet] = useState(false)
  const [showJson, setShowJson] = useState(false)
  // The operator's acknowledgment that the chosen employee owns records the files do not assign. NOT proof of ownership.
  const [ownerAck, setOwnerAck] = useState<'documented' | 'confirmed' | null>(null)
  const [saved, setSaved] = useState<{ unit: UnitResult; ok: boolean; lines: string[] }[] | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  const sendable = filesToSend(files, overrides)
  const detect = useQuery<DetectResponse>({
    queryKey: ['detect', provider, rev],
    queryFn: () => detectFiles(provider!, filesToSend(files, overrides)),
    enabled: !!provider && sendable.length > 0,
    staleTime: Infinity,
    retry: false,
  })
  const detection = detect.data

  const convert = useMutation<ConvertResponse, Error, { provider: ProviderId; files: ReturnType<typeof filesToSend>; ctx: SourceContext } | void>({
    mutationFn: (v) => (v ? convertFiles(v.provider, v.files, v.ctx) : convertFiles(provider!, sendable, ctx)),
    onSuccess: (data) => {
      setConversion(data)
      setSaved(null)
      setShowJson(false)
    },
  })
  const save = useMutation({
    mutationFn: async () => {
      const out: { unit: UnitResult; ok: boolean; lines: string[] }[] = []
      for (const unit of convert.data?.results ?? []) {
        if (unit.status !== 'converted') continue
        const file = files.find((f) => f.name === unit.file)
        if (!file) continue
        try {
          const res = await ingest(provider!, unit.entity, ctx, file.text)
          const ok = res.status === 200 && res.body.ok
          out.push({ unit, ok, lines: ok ? describeSave(res.body.stored) : res.body.issues.filter((i) => i.severity === 'error').map((i) => i.message) })
        } catch (e) {
          out.push({ unit, ok: false, lines: [e instanceof Error ? e.message : 'Could not save.'] })
        }
      }
      return out
    },
    onSuccess: (out) => {
      setSaved(out)
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
    },
  })

  const touch = () => {
    setRev((r) => r + 1)
    convert.reset()
    save.reset()
    setSaved(null)
    setConversion(null)
    setOwnerAck(null)
  }
  const reset = () => {
    setProvider(null)
    setFiles([])
    setOverrides({})
    setCtx({ tenant_id: DEFAULT_WORKSPACE })
    setLoadError(null)
    touch()
  }
  const pickProvider = (p: ProviderId) => {
    if (p === provider) return
    setProvider(p)
    setFiles([])
    setOverrides({})
    setCtx((c) => ({ tenant_id: c.tenant_id }))
    setLoadError(null)
    touch()
  }
  const addFiles = (incoming: LoadedFile[]) => {
    setFiles((cur) => [...cur.filter((f) => !incoming.some((i) => i.name === f.name)), ...incoming])
    touch()
  }

  const loadExampleSet = async () => {
    if (!provider) return
    setLoadingSet(true)
    setLoadError(null)
    try {
      const dir = PROVIDERS.find((p) => p.id === provider)!.fixtureDir
      const index = (await fetchFixtureIndex()).filter((f) => f.dir === dir)
      const loaded = await Promise.all(index.map(async (f) => makeFile(f.name, await fetchFixtureText(f), 'example')))
      setFiles(loaded)
      setOverrides({})
      setCtx((c) => ({ tenant_id: c.tenant_id }))
      touch()
    } catch (e) {
      setLoadError(e instanceof ApiError ? `${e.message} You can still upload your own JSON files.` : 'The examples could not be loaded.')
    } finally {
      setLoadingSet(false)
    }
  }

  /** One click: load that provider's bundled example files, use the ids the examples were written with, and normalize. */
  const runExample = async (p: ProviderId) => {
    setLoadError(null)
    setLoadingSet(true)
    try {
      const dir = PROVIDERS.find((x) => x.id === p)!.fixtureDir
      const index = (await fetchFixtureIndex()).filter((f) => f.dir === dir)
      const loaded = await Promise.all(index.map(async (f) => makeFile(f.name, await fetchFixtureText(f), 'example')))
      setProvider(p)
      setFiles(loaded)
      setOverrides({})
      touch()
      const toSend = filesToSend(loaded, {})
      const det = await detectFiles(p, toSend)
      const wanted = requiredIds(det.files)
      const known = [...det.suggestions, ...exampleSuggestions(p)]
      const next: SourceContext = { tenant_id: DEFAULT_WORKSPACE }
      for (const k of wanted) {
        if (k === 'employee_id') continue // never taken from "the first employee found": see the owner below
        const hit = known.find((x) => x.field === k)
        if (hit) (next as unknown as Record<string, string>)[k] = hit.value
      }
      // The owner of records a file does not assign comes ONLY from the documented owner of the bundled example file.
      let documented = false
      if (wanted.includes('employee_id')) {
        const owners = new Set(
          ownerUnits(det.files).map((u) => documentedOwner(dir, u.file)).map((o) => o ?? ''),
        )
        if (owners.size === 1 && !owners.has('')) {
          next.employee_id = [...owners][0]
          documented = true
        }
      }
      setCtx(next)
      if (documented) setOwnerAck('documented')
      if (missingIds(det.files, next).length === 0) convert.mutate({ provider: p, files: toSend, ctx: next })
    } catch (e) {
      setLoadError(e instanceof ApiError ? `${e.message} You can still upload your own JSON files.` : 'The example could not be loaded.')
    } finally {
      setLoadingSet(false)
    }
  }

  const openUploads = async (list: FileList | null) => {
    if (!list || list.length === 0) return
    setLoadError(null)
    const loaded: LoadedFile[] = []
    for (const f of Array.from(list)) {
      if (f.size > MAX_UPLOAD) {
        setLoadError(`${f.name} is larger than 10 MB, so it was skipped.`)
        continue
      }
      loaded.push(makeFile(f.name, await f.text(), 'upload'))
    }
    if (loaded.length) addFiles(loaded)
  }

  const removeFile = (name: string) => {
    setFiles((cur) => cur.filter((f) => f.name !== name))
    setOverrides(({ [name]: _gone, ...rest }) => rest)
    touch()
  }

  const confirmEntity = (name: string, entity: EntityId) => {
    setOverrides((o) => ({ ...o, [name]: entity }))
    touch()
  }

  const setId = (k: string, v: string) => {
    setCtx((c) => ({ ...c, [k]: v }))
    if (k === 'employee_id') setOwnerAck(null) // a different employee needs its own confirmation
    convert.reset()
    save.reset()
    setSaved(null)
  }

  const fileDetections: FileDetection[] = detection?.files ?? []
  const needed = requiredIds(fileDetections)
  const owned = ownerUnits(fileDetections)
  const needsOwner = owned.length > 0
  const ownerReady = !needsOwner || (ownerAck !== null && (ctx.employee_id ?? '').trim() !== '')
  const missing = missingIds(fileDetections, ctx)
  const fromFiles = detection?.suggestions ?? []
  const fromExamples = provider && files.some((f) => f.origin === 'example') ? exampleSuggestions(provider) : []
  // The files' own ids win; the bundled examples' ids are added only when no file already stated that value.
  const suggestions = [...fromFiles, ...fromExamples.filter((e) => !fromFiles.some((f) => f.field === e.field && f.value === e.value))]
  const units = convertibleUnits(detection)
  const result = convert.data
  const converted = (result?.results ?? []).filter((r) => r.status === 'converted')
  const firstConverted = converted.find((r) => r.records.length > 0)
  const convertOk = converted.length > 0 && converted.every((r) => !r.issues.some((i) => i.severity === 'error'))
  const canConvert = !!provider && units > 0 && missing.length === 0 && ctx.tenant_id.trim() !== '' && ownerReady && !convert.isPending && !detect.isFetching
  const blocked = !provider ? 'Select a provider first.'
    : files.length === 0 ? 'Add some JSON files first.'
    : detect.isFetching ? 'Detecting what the files contain…'
    : units === 0 ? 'Nothing recognisable to convert yet.'
    : ctx.tenant_id.trim() === '' ? 'Give the workspace a name.'
    : missing.length ? `Confirm: ${missing.map((k) => FIELD_LABEL[k] ?? k).join(', ')}.`
    : !ownerReady ? `Confirm which employee owns the ${ownedWords(owned)} in ${[...new Set(owned.map((o) => o.file))].join(', ')} (see “Who owns these records?”).`
    : null

  return (
    <div data-testid="flow">
      <div className="mb-6 rounded-2xl border border-slate-200 bg-gradient-to-b from-white via-white to-brand-50/60 px-5 py-10 text-center shadow-card sm:px-10">
        <h1 className="text-3xl font-semibold tracking-tight text-slate-900 sm:text-[40px] sm:leading-[1.1]">Three payroll providers. One consistent data model.</h1>
        <p className="mx-auto mt-4 max-w-xl text-base leading-relaxed text-slate-500">
          PrismHR, isolved and UKG Ready describe the same people and pay in different shapes. Add a provider’s JSON and get it back in one
          standard format. Nothing is saved unless you choose to.
        </p>
        <div className="mt-7" role="group" aria-label="Try an example" data-testid="quick-examples">
          <p className="eyebrow mb-3">Try an example: one click, no setup</p>
          <div className="flex flex-wrap justify-center gap-2">
            {PROVIDERS.map((p) => (
              <button key={p.id} className="btn-secondary" disabled={loadingSet || convert.isPending} onClick={() => void runExample(p.id)} data-testid={`quick-${p.id}`}>
                <span className={`mr-0.5 h-2 w-2 rounded-full ${p.id === 'prismhr' ? 'bg-indigo-500' : p.id === 'isolved' ? 'bg-violet-500' : 'bg-sky-500'}`} aria-hidden />{p.label} example
              </button>
            ))}
          </div>
          {(loadingSet || convert.isPending) && <p className="mt-2"><Spinner label="Running the example" /></p>}
          {loadError && !provider && <div className="mx-auto mt-3 max-w-xl text-left"><ErrorBanner title="Could not load the example">{loadError}</ErrorBanner></div>}
        </div>
      </div>

      <div className="space-y-6">
        <section aria-labelledby="s1" data-testid="step-1" className="card p-5 sm:p-6">
          <h2 id="s1" className="section-title mb-4">1 · Select payroll provider</h2>
          <ProviderPicker value={provider} onChange={pickProvider} />
        </section>

        {provider && (
          <section aria-labelledby="s2" data-testid="step-2" className="card p-5 sm:p-6">
            <h2 id="s2" className="section-title mb-4">2 · Add your data</h2>
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <input ref={fileInput} type="file" multiple accept="application/json,.json" className="sr-only" aria-label="Choose JSON files" data-testid="upload"
                  onChange={(e) => { void openUploads(e.target.files); e.target.value = '' }} />
                <button className="btn-primary" onClick={() => fileInput.current?.click()}>Upload JSON</button>
                <button className="btn-secondary" disabled={loadingSet} onClick={() => void loadExampleSet()}>
                  {loadingSet ? 'Loading…' : 'Try example data'}
                </button>
                <span className="text-sm text-slate-500">Read in your browser. Nothing is stored.</span>
              </div>
              {loadError && <ErrorBanner title="Could not load the data">{loadError}</ErrorBanner>}
              {files.length > 0 && (
                <ul className="divide-y divide-slate-100 rounded-lg border border-slate-200 bg-white" aria-label="Added files" data-testid="file-list">
                  {files.map((f) => (
                    <li key={f.name} className="flex items-center justify-between gap-3 px-4 py-2.5 text-sm">
                      <span className="min-w-0 truncate font-medium text-slate-800">{f.name}</span>
                      <span className="flex items-center gap-2">
                        {!f.check.ok && <Badge tone="red">not valid JSON</Badge>}
                        <Badge>{f.origin === 'example' ? 'example' : 'uploaded'}</Badge>
                        <button className="text-xs text-slate-500 underline" onClick={() => removeFile(f.name)} aria-label={`Remove ${f.name}`}>Remove</button>
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </section>
        )}

        {files.length > 0 && (
        <section aria-labelledby="s3" data-testid="step-3" className="card p-5 sm:p-6">
          <h2 id="s3" className="section-title mb-4">3 · What we found</h2>
          {(
            <div className="space-y-3">
              {detect.isFetching && <Spinner label="Detecting contents" />}
              {detect.error && <ErrorBanner title="Detection could not run">{(detect.error as Error).message}</ErrorBanner>}
              {files.filter((f) => !f.check.ok).map((f) => (
                <ErrorBanner key={f.name} title={`${f.name} is not valid JSON`}>{f.check.ok ? '' : f.check.message} It was left out.</ErrorBanner>
              ))}
              {fileDetections.map((fd) => (
                <DetectionCard key={fd.name} fd={fd} confirmed={overrides[fd.name]} onConfirm={(e) => confirmEntity(fd.name, e)} />
              ))}
              {detection && needed.length > 0 && (
                <div className="rounded-xl border border-slate-200 bg-slate-50/70 p-4 sm:p-5" data-testid="context-box">
                  <p className="text-sm font-semibold text-slate-800">A few details we need</p>
                  <p className="mb-3 text-xs text-slate-500">
                    These are not in the files themselves. Matches from your other files are suggested; nothing is used until you pick it.
                  </p>
                  <div className="grid gap-4 md:grid-cols-2">
                    {needed.map((k) => k === 'employee_id' ? (
                      <OwnerField key={k} units={owned} value={ctx.employee_id ?? ''} candidates={suggestions.filter((s) => s.field === k)}
                        documented={documentedFor(files, owned, provider)} ack={ownerAck} onChange={(v) => setId(k, v)} onAck={(on) => setOwnerAck(on ? 'confirmed' : null)} />
                    ) : (
                      <IdField key={k} field={k} value={(ctx as unknown as Record<string, string | undefined>)[k] ?? ''} suggestions={suggestions.filter((s) => s.field === k)} onChange={(v) => setId(k, v)} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </section>
        )}

        {units > 0 && (
        <section aria-labelledby="s4" data-testid="step-4" className="card p-5 sm:p-6">
          <h2 id="s4" className="section-title mb-4">4 · Normalize</h2>
          {(
            <div className="space-y-3">
              <div className="grid gap-4 md:grid-cols-2">
                <Field label="Workspace name" required htmlFor="workspace" hint="A label you choose. It only matters if you later save the data.">
                  <input id="workspace" className="input" value={ctx.tenant_id} autoComplete="off" spellCheck={false} onChange={(e) => { setCtx((c) => ({ ...c, tenant_id: e.target.value })); convert.reset() }} />
                </Field>
              </div>
              <div className="flex flex-wrap items-center gap-3">
                <button className="btn-primary" disabled={!canConvert} onClick={() => { save.reset(); convert.mutate() }}>
                  {convert.isPending ? <Spinner label="Normalizing" /> : 'Normalize Data'}
                </button>
                <span className="text-sm text-slate-500">Shows the result only. Nothing is saved.</span>
              </div>
              {!canConvert && blocked && !convert.isPending && <p className="text-sm text-amber-800" data-testid="blocked">{blocked}</p>}
              {convert.error && <ErrorBanner title="The conversion could not run">{(convert.error as Error).message}</ErrorBanner>}
            </div>
          )}
        </section>
        )}

        {result && (
          <section aria-labelledby="s5" data-testid="results">
            <h2 id="s5" className="section-title mb-4">Results</h2>
            <div className="space-y-4">
              <div role="status" data-testid="headline" className={`rounded-xl border px-4 py-3.5 text-sm font-medium shadow-card ${convertOk ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-amber-200 bg-amber-50 text-amber-900'}`}>
                {headlineOf(result)}
              </div>
              <OwnershipNotice conversion={result} employeeId={ctx.employee_id ?? ''} basis={ownerAck === 'documented' ? 'documented' : 'confirmed'} />
              <ResultsOverview conversion={result} />
              <ul className="space-y-3" aria-label="Converted sections">
                {result.results.map((u) => (
                  <ConvertedGroupCard key={`${u.file}-${u.entity}`} unit={u} provider={result.provider} all={result.results} detections={result.files} defaultOpen={u === firstConverted}
                    assigned={ctx.employee_id && ownerAck ? { id: ctx.employee_id, basis: ownerAck } : null} />
                ))}
              </ul>
              {result.integrity && <IntegrityPanel report={result.integrity} />}
              <div className="flex flex-wrap items-center gap-3">
                <a className="btn-primary" href="#/pipeline">View Transformation Pipeline</a>
                <button className="btn-secondary" aria-pressed={showJson} onClick={() => setShowJson((v) => !v)}>View Normalized JSON</button>
                <button className="btn-secondary" disabled={!convertOk || save.isPending} onClick={() => save.mutate()}>
                  {save.isPending ? <Spinner label="Saving" /> : 'Save to Database'}
                </button>
                <button className="btn-secondary" onClick={reset}>Start New Conversion</button>
              </div>
              <p className="text-xs text-slate-500" data-testid="save-explainer">
                <strong>Normalizing does not save anything.</strong> Save to Database stores these records in the local PostgreSQL database under the
                workspace “{ctx.tenant_id}” so you can browse them in Data Explorer. Saving the same file again changes nothing. A record that two files both contain is stored once; saving the second file updates that row (its version and the file location it records).
                {!convertOk && ' Fix the problems above first: a file is only saved if every record converts without errors.'}
              </p>
              {save.error && <ErrorBanner title="Could not save">{(save.error as Error).message}</ErrorBanner>}
              {saved && (
                <div className="space-y-2" data-testid="save-outcome">
                  {saved.map((s) => (
                    <div key={`${s.unit.file}-${s.unit.entity}`} className="text-sm">
                      <Badge tone={s.ok ? 'green' : 'red'}>{s.ok ? 'saved' : 'not saved'}</Badge> <strong>{ENTITY_PLAIN[s.unit.entity].title}</strong> from {s.unit.file}
                      <ul className="ml-5 list-disc text-slate-700">{s.lines.map((l) => <li key={l}>{l}</li>)}</ul>
                    </div>
                  ))}
                  <a className="inline-block text-sm font-medium text-brand-700 underline" href="#/explorer">Browse saved data in Data Explorer →</a>
                </div>
              )}
              <MigrationPanel conversion={result} fileTexts={Object.fromEntries(files.map((f) => [f.name, f.text]))} />
              {showJson && (
                <div data-testid="normalized-json">
                  <JsonBlock value={maskObject(result.results.map((r) => ({ file: r.file, entity: r.entity, status: r.status, counts: r.counts, records: r.records })))} maxHeight={420} />
                </div>
              )}
            </div>
          </section>
        )}
      </div>

    </div>
  )
}

function headlineOf(r: ConvertResponse): string {
  const done = r.results.filter((u) => u.status === 'converted')
  const waiting = r.results.filter((u) => u.status === 'needs_context')
  const errors = r.results.reduce((n, u) => n + u.issues.filter((i) => i.severity === 'error').length, 0)
  const total: Record<string, number> = {}
  for (const u of done) for (const [k, n] of Object.entries(u.counts)) total[k] = (total[k] ?? 0) + n
  let text = !done.length ? 'Nothing was converted.' : Object.keys(total).length ? `Converted ${countsText(total)}.` : 'Recognised, but the file contains no records to convert.'
  if (errors) text += ` ${errors} problem${errors === 1 ? '' : 's'} need attention.`
  if (waiting.length) text += ` ${waiting.length} file section${waiting.length === 1 ? ' is' : 's are'} waiting for an identifier.`
  return text
}

function DetectionCard({ fd, confirmed, onConfirm }: { fd: FileDetection; confirmed: EntityId | undefined; onConfirm: (e: EntityId) => void }) {
  const d = fd.detection
  const [pick, setPick] = useState<EntityId | null>(null)
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-card" data-testid={`detect-${fd.name}`}>
      <p className="flex flex-wrap items-center gap-2 text-sm font-semibold text-slate-900">
        <span className="min-w-0 break-all">{fd.name}</span> <Badge tone={STATUS_TONE[d.status]}>{STATUS_TEXT[d.status]}</Badge>
        {confirmed && <Badge tone="indigo">confirmed by you</Badge>}
      </p>
      {d.status === 'ok' && (
        <ul className="mt-3 divide-y divide-slate-100 rounded-lg border border-slate-100">
          {d.units.map((u) => (
            <li key={`${u.entity}-${u.nested_in ?? ''}`} className="flex items-start gap-3 px-3 py-2.5 text-sm text-slate-800">
              <EntityIcon entity={u.entity} tone="slate" />
              <div className="min-w-0 flex-1">
                <p className="break-words">
                  <span className="num font-semibold">{countsText(u.counts)}</span> · {ENTITY_PLAIN[u.entity].title}
                  {u.nested_in && <span className="text-slate-500"> (inside the {u.nested_in} records)</span>}
                  {u.convention && <> <Badge tone="amber" title="Not a provider response: a wrapper this tool defines">Tada bundle</Badge></>}
                  {Object.keys(u.derived_counts).length > 0 && <span className="text-slate-500"> · also creates {countsText(u.derived_counts)}</span>}
                </p>
                <details className="mt-0.5 text-xs text-slate-600">
                  <summary className="cursor-pointer text-brand-700">Why we think so</summary>
                  <ul className="ml-4 mt-1 list-disc space-y-0.5">{[...u.evidence, ...u.notes].map((e) => <li key={e}>{e}</li>)}</ul>
                </details>
              </div>
            </li>
          ))}
        </ul>
      )}
      {d.warnings.length > 0 && <ul className="mt-2 list-disc pl-5 text-xs text-amber-800">{d.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
      {d.status === 'ambiguous' && (
        <fieldset className="mt-2">
          <legend className="text-sm text-slate-700">{d.message}</legend>
          {d.candidates.map((c) => (
            <label key={c.entity} className="mt-1 flex items-start gap-2 text-sm">
              <input type="radio" name={`pick-${fd.name}`} checked={pick === c.entity} onChange={() => setPick(c.entity)} className="mt-1" />
              <span><span className="font-medium">{ENTITY_PLAIN[c.entity].title}</span><span className="block text-xs text-slate-500">{c.reason}</span></span>
            </label>
          ))}
          <button className="btn-secondary mt-2" disabled={!pick} onClick={() => pick && onConfirm(pick)}>Confirm this is {pick ? ENTITY_PLAIN[pick].title.toLowerCase() : '…'}</button>
        </fieldset>
      )}
      {(d.status === 'unsupported' || d.status === 'empty') && (
        <div className="mt-2 text-sm text-slate-700">
          <p>{d.message}</p>
          {d.provider_hint && <p className="mt-1 text-amber-800">{d.provider_hint} Choose that provider above if it is right.</p>}
        </div>
      )}
    </div>
  )
}

function IdField({ field, value, suggestions, onChange }: { field: string; value: string; suggestions: IdSuggestion[]; onChange: (v: string) => void }) {
  const label = FIELD_LABEL[field] ?? field
  return (
    <div data-testid={`id-${field}`}>
      <Field label={label} required htmlFor={`id-input-${field}`}>
        <input id={`id-input-${field}`} className="input" value={value} autoComplete="off" spellCheck={false} onChange={(e) => onChange(e.target.value)} />
      </Field>
      {suggestions.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1" role="group" aria-label={`Suggestions for ${field.replace('_', ' ')}`}>
          {suggestions.map((s) => (
            <button key={`${s.value}-${s.source_file}`} type="button" onClick={() => onChange(s.value)} title={`${s.why} in ${s.source_file}`}
              className="rounded-full bg-white px-2 py-0.5 text-xs text-brand-700 ring-1 ring-inset ring-brand-300 hover:bg-brand-50">
              Use {s.value} <span className="text-slate-500">(from {s.source_file})</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

/** The documented owner of the bundled example files among those that need one (only for files that came from the example set). */
function documentedFor(files: LoadedFile[], owned: { file: string }[], provider: ProviderId | null): { file: string; id: string } | null {
  const dir = PROVIDERS.find((p) => p.id === provider)?.fixtureDir
  for (const o of owned) {
    const f = files.find((x) => x.name === o.file && x.origin === 'example')
    const id = f && dir ? documentedOwner(dir, f.name) : null
    if (id) return { file: o.file, id }
  }
  return null
}

function OwnerField({ units, value, candidates, documented, ack, onChange, onAck }: {
  units: { file: string; entity: EntityId }[]; value: string; candidates: IdSuggestion[]; documented: { file: string; id: string } | null
  ack: 'documented' | 'confirmed' | null; onChange: (v: string) => void; onAck: (on: boolean) => void
}) {
  const words = ownedWords(units)
  const where = [...new Set(units.map((u) => u.file))].join(', ')
  const ids = [...new Set(candidates.map((c) => c.value))]
  return (
    <div className="rounded-xl border border-amber-300/70 bg-amber-50/70 p-4 md:col-span-2" data-testid="owner-field">
      <p className="text-sm font-semibold text-slate-900">Who owns these records?</p>
      <p className="mb-3 text-sm text-slate-700" data-testid="owner-explanation">{ownerExplanation(words)}</p>
      <div data-testid="id-employee_id">
        <Field label={FIELD_LABEL.employee_id ?? 'Employee ID'} required htmlFor="id-input-employee_id">
          <input id="id-input-employee_id" className="input" value={value} autoComplete="off" spellCheck={false} onChange={(e) => onChange(e.target.value)} />
        </Field>
      </div>
      {documented && (
        <p className="mt-2 text-xs text-slate-600" data-testid="owner-documented">
          Example set’s convention for the bundled file {documented.file}: <strong>{documented.id}</strong>. The file itself does not name its owner; this is the id the examples and their tests use.{' '}
          {!ids.includes(documented.id) && <button type="button" className="text-brand-700 underline" onClick={() => onChange(documented.id)}>Use {documented.id}</button>}
        </p>
      )}
      {ids.length > 0 && (
        <div className="mt-2" role="group" aria-label="Employees found in your files">
          <p className="text-xs text-slate-600" data-testid="owner-candidates">
            {ids.length > 1
              ? `${ids.length} employees appear in your files. They are only candidates: nothing says which one owns the ${words} in ${where}, so none is chosen for you.`
              : `One employee appears in your files. That does not show they own the ${words} in ${where}, so it is not chosen for you.`}
          </p>
          <div className="mt-1 flex flex-wrap gap-1">
            {candidates.filter((c, i) => candidates.findIndex((x) => x.value === c.value) === i).map((s) => (
              <button key={`${s.value}-${s.source_file}`} type="button" onClick={() => onChange(s.value)} title={`${s.why} in ${s.source_file}`}
                className="rounded-full bg-white px-2 py-0.5 text-xs text-brand-700 ring-1 ring-inset ring-brand-300 hover:bg-brand-50">
                Use {s.value} <span className="text-slate-500">(from {s.source_file})</span>
              </button>
            ))}
          </div>
        </div>
      )}
      <label className="mt-3 flex items-start gap-2 text-sm text-slate-800">
        <input type="checkbox" className="mt-1" disabled={!value.trim()} checked={ack !== null} onChange={(e) => onAck(e.target.checked)} />
        <span>
          {ack === 'documented' ? (
            <>This example set’s convention, employee {value}, is assigned to the {words} in {where}.</>
          ) : (
            <>I confirm that employee {value.trim() || '…'} owns the {words} in {where}. <span className="text-slate-500">This is my confirmation: neither the files nor the checks can verify it.</span></>
          )}
        </span>
      </label>
    </div>
  )
}
