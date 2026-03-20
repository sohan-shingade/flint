import { useState, useCallback } from 'react'
import { docsContent } from '../data/docs-content'
import type { DocTopic } from '../data/docs-content'
import DocsSidebar from '../components/DocsSidebar'
import DocsContent from '../components/DocsContent'

function findTopic(id: string): DocTopic | null {
  for (const section of docsContent) {
    for (const topic of section.topics) {
      if (topic.id === id) return topic
    }
  }
  return null
}

// Default to the first topic in the first section
const defaultTopicId = docsContent[0]?.topics[0]?.id ?? ''

export default function Docs() {
  const [activeTopic, setActiveTopic] = useState(defaultTopicId)

  const handleSelect = useCallback((topicId: string) => {
    setActiveTopic(topicId)
    // Scroll to top of content area when switching topics
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }, [])

  const topic = findTopic(activeTopic)

  return (
    <div className="flex min-h-[calc(100vh-12rem)]">
      <DocsSidebar
        sections={docsContent}
        activeTopic={activeTopic}
        onSelect={handleSelect}
      />
      <DocsContent topic={topic} />
    </div>
  )
}
