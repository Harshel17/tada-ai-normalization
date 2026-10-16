import { createContext, useContext, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { ConvertResponse } from '../api/types'

/** The most recent preview conversion, shared so the Pipeline Explorer can show it. Memory only. */
interface Store {
  conversion: ConvertResponse | null
  setConversion: (c: ConvertResponse | null) => void
}

const Ctx = createContext<Store>({ conversion: null, setConversion: () => {} })

export function ConversionProvider({ children }: { children: ReactNode }) {
  const [conversion, setConversion] = useState<ConvertResponse | null>(null)
  const value = useMemo(() => ({ conversion, setConversion }), [conversion])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useConversion = () => useContext(Ctx)
