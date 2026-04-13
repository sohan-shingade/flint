import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './mocks/handlers'

// Start MSW server before all tests
beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))

// Reset handlers after each test (removes runtime overrides)
afterEach(() => {
  server.resetHandlers()
  cleanup()
})

// Close server after all tests
afterAll(() => server.close())

// --- DOM API mocks needed by chart libraries and Monaco ---

// ResizeObserver (used by recharts, lightweight-charts)
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = ResizeObserverMock as any

// IntersectionObserver (used by lazy-loading components)
class IntersectionObserverMock {
  readonly root = null
  readonly rootMargin = ''
  readonly thresholds: number[] = []
  observe() {}
  unobserve() {}
  disconnect() {}
  takeRecords() { return [] }
}
globalThis.IntersectionObserver = IntersectionObserverMock as any

// matchMedia (used by responsive components)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
})

// URL.createObjectURL / revokeObjectURL (used by export/download features)
URL.createObjectURL = () => 'blob:mock-url'
URL.revokeObjectURL = () => {}

// Mock scrollTo (jsdom doesn't support it)
window.scrollTo = () => {}
Element.prototype.scrollIntoView = () => {}
