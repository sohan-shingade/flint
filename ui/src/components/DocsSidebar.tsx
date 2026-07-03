import { useState } from 'react'
import type { DocSection } from '../data/docs'

interface Props {
  sections: DocSection[]
  activeTopic: string
  onSelect: (topicId: string) => void
}

export default function DocsSidebar({ sections, activeTopic, onSelect }: Props) {
  const [expanded, setExpanded] = useState<Record<string, boolean>>(() => {
    // Start with all sections expanded
    const init: Record<string, boolean> = {}
    for (const s of sections) {
      init[s.id] = true
    }
    return init
  })

  const toggle = (sectionId: string) => {
    setExpanded((prev) => ({ ...prev, [sectionId]: !prev[sectionId] }))
  }

  return (
    <aside className="w-56 shrink-0 sticky top-16 h-[calc(100vh-8rem)] overflow-y-auto pr-4 border-r border-border">
      <div className="py-4">
        <div className="text-[10px] text-ghost tracking-[0.3em] mb-4">// DOCUMENTATION</div>

        {sections.map((section) => (
          <div key={section.id} className="mb-3">
            {/* Section header — collapsible toggle */}
            <button
              onClick={() => toggle(section.id)}
              className="w-full flex items-center gap-2 text-[10px] tracking-[0.2em] text-ghost hover:text-terminal transition-colors duration-150 py-1.5 group"
            >
              <span
                className={`text-[8px] text-ghost/60 transition-transform duration-200 ${
                  expanded[section.id] ? 'rotate-90' : ''
                }`}
              >
                &#9654;
              </span>
              <span className="group-hover:text-amber/80">{section.title}</span>
            </button>

            {/* Topic list — collapsible */}
            {expanded[section.id] && (
              <div className="ml-4 border-l border-border/60 pl-3">
                {section.topics.map((topic) => {
                  const isActive = topic.id === activeTopic
                  return (
                    <button
                      key={topic.id}
                      onClick={() => onSelect(topic.id)}
                      className={`block w-full text-left text-[11px] py-1 px-2 transition-all duration-150 ${
                        isActive
                          ? 'text-amber bg-amber-glow border-l border-amber -ml-px'
                          : 'text-ghost hover:text-terminal'
                      }`}
                    >
                      {topic.title}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        ))}
      </div>
    </aside>
  )
}
