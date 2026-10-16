import {
  fetchFixtureText, getStatus, ingest, normalize,
} from '../api/client'
import type { IngestResponse, NormalizeResponse, Scope } from '../api/types'
import type { Scenario } from './scenarios'
import { otherProvider, scenarioCompany } from './scenarios'

export type StepState = 'pending' | 'running' | 'pass' | 'fail' | 'error' | 'skipped'

export interface StepResult {
  id: string
  label: string
  state: StepState
  /** Human-readable evidence taken from the real response. */
  detail: string
  http?: number
  ms?: number
}

export const STEP_DEFS: { id: string; label: string }[] = [
  { id: 'fixture', label: 'Load synthetic fixture' },
  { id: 'normalize', label: 'POST /normalize' },
  { id: 'ingest1', label: 'POST /ingest (first save)' },
  { id: 'ingest2', label: 'POST /ingest again (idempotency)' },
  { id: 'readback', label: 'GET stored data (read-back)' },
  { id: 'tenant', label: 'Tenant isolation' },
  { id: 'provider', label: 'Provider isolation' },
]

export const initialSteps = (): StepResult[] => STEP_DEFS.map((s) => ({ ...s, state: 'pending', detail: '' }))

const countsText = (c: Record<string, number>) => Object.entries(c).map(([k, v]) => `${v} ${k}`).join(', ') || 'no records'
const sameCounts = (a: Record<string, number>, b: Record<string, number>) => {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)])
  return [...keys].every((k) => (a[k] ?? 0) === (b[k] ?? 0))
}
const total = (c: Record<string, number>) => Object.values(c).reduce((n, v) => n + v, 0)

/** Number of items in a read response: array length, or 1 for a single object. */
function itemCount(body: unknown): number {
  return Array.isArray(body) ? body.length : body && typeof body === 'object' ? 1 : 0
}
/** A read that found nothing: HTTP 404, or an empty list. */
const foundNothing = (r: { status: number; body: unknown }) => r.status === 404 || (r.status === 200 && itemCount(r.body) === 0 && Array.isArray(r.body))

export interface RunHooks {
  onStep: (steps: StepResult[]) => void
}

/**
 * Runs one scenario against the live API. Every verdict is computed from a real response;
 * a step that cannot run (earlier failure) is `skipped`, never `pass`.
 */
export async function runScenario(sc: Scenario, tenant: string, hooks: RunHooks): Promise<StepResult[]> {
  const steps = initialSteps()
  const emit = () => hooks.onStep(steps.map((s) => ({ ...s })))
  const ctx = { tenant_id: tenant, ...sc.context }
  const scope: Scope = { provider: sc.provider, tenant, company: scenarioCompany(sc) }
  let broken = false
  let payload = ''

  const run = async (id: string, fn: () => Promise<Pick<StepResult, 'state' | 'detail' | 'http'>>) => {
    const step = steps.find((s) => s.id === id)!
    if (broken) {
      step.state = 'skipped'
      step.detail = 'Skipped because an earlier step did not pass.'
      emit()
      return
    }
    step.state = 'running'
    emit()
    const t0 = performance.now()
    try {
      Object.assign(step, await fn())
    } catch (e) {
      step.state = 'error'
      step.detail = e instanceof Error ? e.message : 'Unexpected failure'
    }
    step.ms = Math.round(performance.now() - t0)
    if ((step as StepResult).state !== 'pass') broken = true  // Object.assign above changed it
    emit()
  }

  await run('fixture', async () => {
    payload = await fetchFixtureText(sc.fixture)
    return { state: 'pass', detail: `${sc.fixture.dir}/${sc.fixture.name} (${payload.length.toLocaleString()} characters)` }
  })

  await run('normalize', async () => {
    const { status, body } = await normalize(sc.provider, sc.entity, ctx, payload)
    return check(status, body, sc, 'normalize')
  })

  const expectedTotal = total(sc.expectedCounts)
  await run('ingest1', async () => {
    const { status, body } = await ingest(sc.provider, sc.entity, ctx, payload)
    const stored = Object.values(body.stored ?? {})
    const sum = (k: 'inserted' | 'updated' | 'unchanged') => stored.reduce((n, c) => n + c[k], 0)
    const ok = status === 200 && body.ok && sum('inserted') + sum('unchanged') === expectedTotal && sum('updated') === 0
    return {
      http: status,
      state: ok ? 'pass' : 'fail',
      detail: `${sum('inserted')} inserted, ${sum('updated')} updated, ${sum('unchanged')} unchanged (expected ${expectedTotal} stored)` +
        (sum('unchanged') > 0 && sum('inserted') === 0 ? '. This tenant already held these records.' : ''),
    }
  })

  await run('ingest2', async () => {
    const { status, body } = await ingest(sc.provider, sc.entity, ctx, payload)
    const stored = Object.values((body as IngestResponse).stored ?? {})
    const unchanged = stored.reduce((n, c) => n + c.unchanged, 0)
    const written = stored.reduce((n, c) => n + c.inserted + c.updated, 0)
    return {
      http: status,
      state: status === 200 && written === 0 && unchanged === expectedTotal ? 'pass' : 'fail',
      detail: `${unchanged} unchanged, ${written} written (expected ${expectedTotal} unchanged, 0 written)`,
    }
  })

  await run('readback', async () => {
    const r = await getStatus(sc.readback.path(scope))
    const n = itemCount(r.body)
    return {
      http: r.status,
      state: r.status === 200 && n === sc.readback.expectItems ? 'pass' : 'fail',
      detail: `${sc.readback.label}: HTTP ${r.status}, ${n} item${n === 1 ? '' : 's'} (expected ${sc.readback.expectItems})`,
    }
  })

  await run('tenant', async () => {
    const other = `${tenant}-other`
    const r = await getStatus(sc.readback.path({ ...scope, tenant: other }))
    return {
      http: r.status,
      state: foundNothing(r) ? 'pass' : 'fail',
      detail: `Same read as tenant “${other}”: HTTP ${r.status}, ${itemCount(r.body)} items (expected 404 or none)`,
    }
  })

  await run('provider', async () => {
    const other = otherProvider(sc.provider)
    const r = await getStatus(sc.readback.path({ ...scope, provider: other }))
    return {
      http: r.status,
      state: foundNothing(r) ? 'pass' : 'fail',
      detail: `Same read as provider “${other}”: HTTP ${r.status}, ${itemCount(r.body)} items (expected 404 or none)`,
    }
  })

  return steps
}

function check(status: number, body: NormalizeResponse, sc: Scenario, _kind: 'normalize'): Pick<StepResult, 'state' | 'detail' | 'http'> {
  const ok = status === 200 && body.ok && sameCounts(body.counts, sc.expectedCounts)
  const errors = body.issues.filter((i) => i.severity === 'error').length
  return {
    http: status,
    state: ok ? 'pass' : 'fail',
    detail: `HTTP ${status}; produced ${countsText(body.counts)} (expected ${countsText(sc.expectedCounts)}); ${errors} error issue${errors === 1 ? '' : 's'}, ${body.issues.length} issues in total`,
  }
}
