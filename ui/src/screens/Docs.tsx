// Screen — Docs (§12). v1's sidebar-nav docs layout with new v2 content. The sidebar
// is the recovered DocsSidebar; content is rendered from the structured blocks in
// data/docs.ts (no dangerouslySetInnerHTML — every block is a typed node).

import { useMemo, useState } from 'react'
import { docs, type DocBlock, type DocTopic } from '../data/docs'
import DocsSidebar from '../components/DocsSidebar'

function Block({ block }: { block: DocBlock }) {
  switch (block.t) {
    case 'h':
      return <h4 className="mt-4 mb-1 font-mono text-xs uppercase tracking-widest text-amber">{block.text}</h4>
    case 'p':
      return <p className="mb-3 text-[13px] leading-relaxed text-terminal">{block.text}</p>
    case 'ul':
      return (
        <ul className="mb-3 list-disc space-y-1 pl-5 text-[13px] leading-relaxed text-terminal">
          {block.items.map((it, i) => (
            <li key={i}>{it}</li>
          ))}
        </ul>
      )
    case 'code':
      return (
        <pre className="mb-3 overflow-x-auto rounded border border-border bg-void p-3 font-mono text-[12px] text-terminal">
          <code>{block.code}</code>
        </pre>
      )
  }
}

function DocsContent({ topic }: { topic: DocTopic | null }) {
  if (!topic) return <div className="p-6 font-mono text-sm text-ghost">select a topic.</div>
  return (
    <article className="min-w-0 flex-1 px-6 py-4">
      <h2 className="mb-4 font-display text-2xl italic text-terminal">{topic.title}</h2>
      {topic.blocks.map((b, i) => (
        <Block key={i} block={b} />
      ))}
    </article>
  )
}

function findTopic(id: string): DocTopic | null {
  for (const s of docs) for (const t of s.topics) if (t.id === id) return t
  return null
}

export default function Docs() {
  const defaultId = useMemo(() => docs[0]?.topics[0]?.id ?? '', [])
  const [active, setActive] = useState(defaultId)
  const topic = findTopic(active)

  return (
    <div className="flex min-h-[calc(100vh-12rem)]">
      <DocsSidebar
        sections={docs}
        activeTopic={active}
        onSelect={(id) => {
          setActive(id)
          window.scrollTo({ top: 0, behavior: 'smooth' })
        }}
      />
      <DocsContent topic={topic} />
    </div>
  )
}
