import { ApiError } from './errors'

/**
 * The bundled example files.
 *
 * The repository's SYNTHETIC fixtures (tests/fixtures/<provider>/*.json) are compiled into the app by Vite, so the same code works
 * under `npm run dev` and in a production build, with no dev-server middleware and no network request. Only these three fixed
 * folders are matched (never tests/fixtures/live_shapes), and every file is its own lazy chunk, loaded only when an example is used.
 */
export interface FixtureRef {
  dir: string
  name: string
}

const BUNDLED = import.meta.glob('../../../tests/fixtures/{prismhr,isolved,ukg_ready}/*.json', { query: '?raw', import: 'default' }) as Record<
  string,
  () => Promise<string>
>

const refOf = (path: string): FixtureRef => {
  const [dir, name] = path.split('/').slice(-2)
  return { dir: dir!, name: name! }
}

export async function fetchFixtureIndex(): Promise<FixtureRef[]> {
  return Object.keys(BUNDLED).map(refOf).sort((a, b) => a.dir.localeCompare(b.dir) || a.name.localeCompare(b.name))
}

export async function fetchFixtureText(f: FixtureRef): Promise<string> {
  const key = Object.keys(BUNDLED).find((k) => {
    const r = refOf(k)
    return r.dir === f.dir && r.name === f.name
  })
  if (!key) throw new ApiError(404, 'Example file not available.')
  return BUNDLED[key]!()
}
