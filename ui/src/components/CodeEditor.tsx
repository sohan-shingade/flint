// Monaco wrapper for the Lab's EDITOR mode (§13.2). Lazy-loaded so pages that
// never open the editor don't pay the ~1MB cost; @monaco-editor/react fetches the
// Monaco runtime from its CDN on first mount (fine for local dev). The editor is a
// pure authoring surface — the OS sandbox, not the browser, is the security
// boundary (§8.3, D25); nothing here executes user code.

import { Suspense, lazy } from 'react'

const Editor = lazy(() => import('@monaco-editor/react'))

interface Props {
  value: string
  onChange: (value: string) => void
  height?: string
}

function EditorFallback({ height }: { height: string }) {
  return (
    <div
      className="flex items-center justify-center font-mono text-sm text-ghost"
      style={{ height, background: '#141418' }}
    >
      loading editor…
    </div>
  )
}

export default function CodeEditor({ value, onChange, height = '100%' }: Props) {
  return (
    <Suspense fallback={<EditorFallback height={height} />}>
      <Editor
        height={height}
        defaultLanguage="python"
        theme="vs-dark"
        value={value}
        onChange={(v) => onChange(v || '')}
        options={{
          fontSize: 13,
          fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
          minimap: { enabled: false },
          scrollBeyondLastLine: false,
          lineNumbers: 'on',
          renderLineHighlight: 'line',
          tabSize: 4,
          insertSpaces: true,
          wordWrap: 'on',
          padding: { top: 12 },
        }}
      />
    </Suspense>
  )
}
