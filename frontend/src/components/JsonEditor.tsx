import { lazy, Suspense } from 'react'
import type { MonacoJsonProps } from './MonacoJson'
import { Spinner } from './ui'

// Monaco is large; load it only when an editor is first shown.
const MonacoJson = lazy(() => import('./MonacoJson'))

export function JsonEditor(props: MonacoJsonProps) {
  return (
    <Suspense fallback={<div className="flex h-40 items-center justify-center rounded-lg border border-slate-200 bg-white"><Spinner label="Loading editor" /></div>}>
      <MonacoJson {...props} />
    </Suspense>
  )
}
