import { Suspense, lazy } from 'react'
import { loader } from '@monaco-editor/react'
import * as monaco from 'monaco-editor'

// BUG-4 fix — point @monaco-editor/react at the bundled Monaco instance
// instead of its default CDN loader (https://cdn.jsdelivr.net/...).
// Flint's README + home page promise "local-first, nothing leaves your
// machine"; the default CDN fetch silently broke that. This block ships
// Monaco inside the app bundle so the editor works fully offline.
loader.config({ monaco })

// Phase 4 T4.4 — lazy-load the Monaco wrapper (~1MB) so pages that never
// show the editor don't pay the cost. BacktestLab is the only page that
// mounts this component.
const Editor = lazy(() => import('@monaco-editor/react'))

interface Props {
  value: string
  onChange: (value: string) => void
  height?: string
}

function EditorFallback({ height }: { height: string }) {
  return (
    <div
      style={{
        height,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#1e1e1e',
        color: '#808080',
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        fontSize: 13,
      }}
    >
      Loading editor...
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
