import Editor from '@monaco-editor/react'

interface Props {
  value: string
  onChange: (value: string) => void
  height?: string
}

export default function CodeEditor({ value, onChange, height = '100%' }: Props) {
  return (
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
  )
}
