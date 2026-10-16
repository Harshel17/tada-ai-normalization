import type { EntityId } from '../../api/types'

const PATHS: Record<EntityId, JSX.Element> = {
  company: <path d="M4 20V6l8-3v17M12 9h8v11M4 20h16M8 9h.01M8 13h.01M8 17h.01M16 13h.01M16 17h.01" />,
  employee: <><circle cx="12" cy="8" r="3.5" /><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5" /></>,
  direct_deposit: <path d="M3 10 12 4l9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18" />,
  deduction: <><circle cx="12" cy="12" r="8.5" /><path d="M8 12h8" /></>,
  payroll: <><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M4 10h16M8 3v4M16 3v4M10 15h4" /></>,
}

/** Small decorative entity icon (inline SVG, hidden from assistive technology: the title next to it carries the meaning). */
export function EntityIcon({ entity, tone = 'brand' }: { entity: EntityId; tone?: 'brand' | 'slate' }) {
  const color = tone === 'brand' ? 'bg-brand-50 text-brand-600' : 'bg-slate-100 text-slate-500'
  return (
    <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${color}`} aria-hidden>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
        {PATHS[entity] ?? PATHS.company}
      </svg>
    </span>
  )
}
