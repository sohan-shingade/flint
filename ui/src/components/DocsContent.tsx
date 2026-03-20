import type { DocTopic } from '../data/docs-content'

interface Props {
  topic: DocTopic | null
}

export default function DocsContent({ topic }: Props) {
  if (!topic) {
    return (
      <div className="flex-1 flex items-center justify-center text-ghost text-sm">
        <span className="text-[11px] tracking-[0.2em]">// SELECT A TOPIC</span>
      </div>
    )
  }

  return (
    <div className="flex-1 min-w-0 py-4 pl-8">
      {/* Topic header */}
      <div className="mb-6">
        <div className="text-[10px] text-ghost tracking-[0.3em] mb-1">// {topic.id.toUpperCase().replace(/-/g, '_')}</div>
        <h2 className="text-lg text-amber font-semibold tracking-wide">{topic.title}</h2>
        <div className="mt-2 h-px bg-gradient-to-r from-amber/30 to-transparent" />
      </div>

      {/* HTML content */}
      <div
        className="docs-prose text-[13px] text-terminal leading-relaxed"
        dangerouslySetInnerHTML={{ __html: topic.content }}
      />

      {/* Code blocks */}
      {topic.codeBlocks && topic.codeBlocks.length > 0 && (
        <div className="mt-6 space-y-4">
          {topic.codeBlocks.map((block, idx) => (
            <div key={idx} className="border border-border rounded overflow-hidden">
              {/* Language label header */}
              <div className="flex items-center justify-between px-3 py-1.5 bg-surface border-b border-border">
                <span className="text-[10px] text-amber tracking-[0.2em] uppercase">
                  {block.language}
                </span>
                <span className="text-[9px] text-ghost tracking-wider">
                  {block.code.split('\n').length} LINES
                </span>
              </div>

              {/* Code body */}
              <pre className="bg-void p-4 overflow-x-auto">
                <code className="text-[12px] text-phosphor leading-relaxed whitespace-pre">
                  {block.code}
                </code>
              </pre>
            </div>
          ))}
        </div>
      )}

      {/* Bottom spacer */}
      <div className="h-16" />

      {/* Inline styles for docs prose HTML content */}
      <style>{`
        .docs-prose h4 {
          color: var(--color-amber-dim);
          font-size: 12px;
          font-weight: 600;
          letter-spacing: 0.1em;
          text-transform: uppercase;
          margin-top: 1.5rem;
          margin-bottom: 0.5rem;
        }
        .docs-prose p {
          margin-bottom: 0.75rem;
        }
        .docs-prose ul, .docs-prose ol {
          margin-bottom: 0.75rem;
          padding-left: 1.25rem;
        }
        .docs-prose ul {
          list-style-type: disc;
        }
        .docs-prose ol {
          list-style-type: decimal;
        }
        .docs-prose li {
          margin-bottom: 0.35rem;
          color: var(--color-terminal);
        }
        .docs-prose li strong {
          color: var(--color-amber-dim);
        }
        .docs-prose code {
          background: var(--color-surface);
          border: 1px solid var(--color-border);
          padding: 0.1em 0.35em;
          border-radius: 2px;
          font-size: 0.92em;
          color: var(--color-phosphor);
        }
        .docs-prose strong {
          color: var(--color-terminal);
          font-weight: 600;
        }
        .docs-prose em {
          color: var(--color-ghost);
          font-style: italic;
        }
      `}</style>
    </div>
  )
}
