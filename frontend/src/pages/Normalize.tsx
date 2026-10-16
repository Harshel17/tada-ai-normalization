import { useState } from 'react'
import { NormalizeFlow } from './NormalizeFlow'
import { NormalizeAdvanced } from './NormalizeAdvanced'

type Mode = 'guided' | 'advanced'

/**
 * The Convert page: a guided walkthrough by default, with the full technical tool one click away.
 * Both stay mounted once used, so switching modes never loses your work.
 */
export function Normalize() {
  const [mode, setMode] = useState<Mode>('guided')
  const [advancedUsed, setAdvancedUsed] = useState(false)
  const choose = (m: Mode) => {
    setMode(m)
    if (m === 'advanced') setAdvancedUsed(true)
  }
  return (
    <div>
      <div role="radiogroup" aria-label="View mode" className="mb-5 ml-auto flex w-fit gap-0.5 rounded-lg bg-slate-200/60 p-0.5 text-xs">
        {([['guided', 'Normalize'], ['advanced', 'Advanced mode']] as const).map(([id, label]) => (
          <button key={id} role="radio" aria-checked={mode === id} onClick={() => choose(id)}
            className={`rounded-md px-3 py-1 transition-colors ${mode === id ? 'bg-white font-medium text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-800'}`}>
            {label}
          </button>
        ))}
      </div>
      <div hidden={mode !== 'guided'}><NormalizeFlow /></div>
      {advancedUsed && <div hidden={mode !== 'advanced'} data-testid="advanced"><NormalizeAdvanced /></div>}
    </div>
  )
}
