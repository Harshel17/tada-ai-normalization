/**
 * Test double for `api/fixtures`: the unit tests script example files through the mocked network (`/__fixtures/...` routes),
 * exactly as before the examples were bundled. The real bundled loader has its own tests (api/fixtures.test.ts) and runs in
 * the browser tests against the production build.
 */
import { ApiError } from '../api/errors'
import type { FixtureRef } from '../api/fixtures'

export type { FixtureRef }

export async function fetchFixtureIndex(): Promise<FixtureRef[]> {
  const res = await fetch('/__fixtures/index')
  const ok = res.ok && (res.headers.get('content-type') ?? '').includes('json')
  if (!ok) throw new ApiError(res.status, 'Example files could not be loaded (only available while `npm run dev` is running).')
  return ((await res.json()) as { fixtures: FixtureRef[] }).fixtures
}

export async function fetchFixtureText(f: FixtureRef): Promise<string> {
  const res = await fetch(`/__fixtures/file?dir=${encodeURIComponent(f.dir)}&name=${encodeURIComponent(f.name)}`)
  if (!res.ok) throw new ApiError(res.status, 'Fixture not available.')
  return res.text()
}
