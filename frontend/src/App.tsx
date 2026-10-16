import { useEffect, useState } from 'react'
import { Explorer } from './pages/Explorer'
import { Normalize } from './pages/Normalize'
import { Overview } from './pages/Overview'
import { PipelineExplorer } from './pages/PipelineExplorer'
import { ConversionProvider } from './state/conversion'
import { TestRuns } from './pages/TestRuns'

const SECTIONS = [
  { id: 'normalize', label: 'Normalize', hint: 'Convert a provider’s JSON and preview a migration' },
  { id: 'pipeline', label: 'Pipeline Explorer', hint: 'See how each source field became a standard field' },
  { id: 'explorer', label: 'Data Explorer', hint: 'Browse records you saved to the database' },
  { id: 'runs', label: 'Test Runs', hint: 'Check the whole system against bundled examples' },
  { id: 'overview', label: 'System status', hint: 'API and database status' }, // reachable by link and URL, not part of the main navigation
] as const

type SectionId = (typeof SECTIONS)[number]['id']

const PAGES: { id: SectionId; Page: () => JSX.Element }[] = [
  { id: 'overview', Page: Overview },
  { id: 'normalize', Page: Normalize },
  { id: 'pipeline', Page: PipelineExplorer },
  { id: 'explorer', Page: Explorer },
  { id: 'runs', Page: TestRuns },
]

function currentSection(): SectionId {
  const hash = window.location.hash.replace(/^#\/?/, '')
  return SECTIONS.find((s) => s.id === hash)?.id ?? 'normalize'
}

export default function App() {
  const [section, setSection] = useState<SectionId>(currentSection)
  // A section stays mounted once visited, so editor contents and results survive switching tabs.
  const [visited, setVisited] = useState<Set<SectionId>>(() => new Set([currentSection()]))

  useEffect(() => {
    const onHash = () => {
      const next = currentSection()
      setSection(next)
      setVisited((v) => (v.has(next) ? v : new Set(v).add(next)))
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  return (
    <ConversionProvider>
      <div className="min-h-screen md:pl-60">
        {/* desktop: a fixed 240px sidebar; below md: a compact top bar with the same four links */}
        <header className="border-b border-slate-200 bg-white px-4 py-3 md:fixed md:inset-y-0 md:left-0 md:z-20 md:flex md:w-60 md:flex-col md:border-b-0 md:border-r md:px-3 md:py-5">
          <a href="#/normalize" className="flex items-center gap-2.5 md:px-2" aria-label="Tada Normalize home">
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand-500 to-brand-700 text-sm font-bold text-white shadow-sm" aria-hidden>T</span>
            <span className="text-[15px] font-semibold tracking-tight text-slate-900">Tada Normalize</span>
          </a>
          <nav aria-label="Sections" className="mt-3 flex flex-wrap items-center gap-1 text-sm md:mt-8 md:flex-col md:items-stretch md:gap-0.5">
            <p className="eyebrow hidden px-2 pb-1 md:block" aria-hidden>Workspace</p>
            {SECTIONS.filter((s) => s.id !== 'overview').map((s) => (
              <a
                key={s.id}
                href={`#/${s.id}`}
                title={s.hint}
                aria-current={section === s.id ? 'page' : undefined}
                className={`group flex items-center gap-2.5 whitespace-nowrap rounded-lg px-2.5 py-2 transition-colors ${
                  section === s.id ? 'bg-brand-50 font-medium text-brand-700' : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900'
                }`}
              >
                <NavIcon id={s.id} active={section === s.id} />
                {s.label}
              </a>
            ))}
          </nav>
        </header>
        <main className="px-4 py-8 sm:px-6 md:px-10 md:py-10">
          <div className="mx-auto max-w-5xl">
            {PAGES.map(({ id, Page }) =>
              visited.has(id) ? (
                <div key={id} data-view={id} hidden={section !== id}>
                  <Page />
                </div>
              ) : null,
            )}
          </div>
        </main>
        <footer className="px-4 pb-8 text-xs text-slate-500 sm:px-6 md:fixed md:bottom-0 md:left-0 md:z-20 md:w-60 md:border-t md:border-slate-200 md:bg-white md:px-5 md:py-4">
          <div className="mx-auto max-w-5xl md:max-w-none">Local tool for synthetic data. <a className="underline hover:text-slate-600" href="#/overview">System status</a></div>
        </footer>
      </div>
    </ConversionProvider>
  )
}

/** Small inline icons (no icon library): decorative, so they are hidden from assistive technology. */
function NavIcon({ id, active }: { id: SectionId; active: boolean }) {
  const common = { width: 18, height: 18, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.7, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true, className: active ? 'text-brand-600' : 'text-slate-400 group-hover:text-slate-600' }
  switch (id) {
    case 'normalize':
      return <svg {...common}><path d="M4 7h10M4 12h16M4 17h10" /><path d="m17 5 3 2-3 2" /></svg>
    case 'pipeline':
      return <svg {...common}><circle cx="5" cy="6" r="2" /><circle cx="19" cy="12" r="2" /><circle cx="5" cy="18" r="2" /><path d="M7 6h5a3 3 0 0 1 3 3v0M7 18h5a3 3 0 0 0 3-3v0" /></svg>
    case 'explorer':
      return <svg {...common}><ellipse cx="12" cy="6" rx="7" ry="3" /><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" /></svg>
    case 'runs':
      return <svg {...common}><path d="m5 12 4 4 10-10" /></svg>
    default:
      return <svg {...common}><circle cx="12" cy="12" r="8" /></svg>
  }
}
