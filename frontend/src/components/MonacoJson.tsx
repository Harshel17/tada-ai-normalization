import Editor, { loader } from '@monaco-editor/react'
import { useRef } from 'react'
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import jsonWorker from 'monaco-editor/esm/vs/language/json/json.worker?worker'

// Bundle Monaco locally (no CDN request): the console works offline and sends nothing to a third party.
self.MonacoEnvironment = {
  getWorker: (_id: string, label: string) => (label === 'json' ? new jsonWorker() : new editorWorker()),
}
loader.config({ monaco })

export interface MonacoJsonProps {
  value: string
  onChange?: (value: string) => void
  readOnly?: boolean
  height?: number | string
  ariaLabel: string
}

export default function MonacoJson({ value, onChange, readOnly = false, height = 420, ariaLabel }: MonacoJsonProps) {
  // @monaco-editor/react keeps its first change-listener even when `onChange` later becomes undefined,
  // and a programmatic value swap (editable -> masked view) fires it. A stable handler that reads the
  // LATEST props makes sure a read-only editor can never write back into the caller's state.
  const live = useRef({ readOnly, onChange })
  live.current = { readOnly, onChange }
  const handleChange = useRef((v: string | undefined) => {
    if (!live.current.readOnly) live.current.onChange?.(v ?? '')
  }).current
  return (
    <div className="overflow-hidden rounded-lg border border-slate-300" data-testid={`editor-${ariaLabel.toLowerCase().replace(/\W+/g, '-')}`}>
      <Editor
        height={height}
        language="json"
        value={value}
        theme="vs"
        onChange={handleChange}
        options={{
          readOnly,
          ariaLabel,
          minimap: { enabled: false },
          fontSize: 12.5,
          scrollBeyondLastLine: false,
          wordWrap: 'on',
          automaticLayout: true,
          autoClosingBrackets: 'never',
          autoClosingQuotes: 'never',
          autoIndent: 'none',
          formatOnPaste: false,
          formatOnType: false,
          tabSize: 2,
          renderLineHighlight: readOnly ? 'none' : 'line',
          stickyScroll: { enabled: false },
        }}
      />
    </div>
  )
}
