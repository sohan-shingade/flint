import type { ReactElement } from 'react'
import { render, type RenderOptions } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

/**
 * Custom render that wraps in MemoryRouter.
 * Use this for any page/component that uses Link, NavLink, useNavigate, etc.
 */
export function renderWithRouter(
  ui: ReactElement,
  options?: Omit<RenderOptions, 'wrapper'> & { route?: string }
) {
  const { route, ...renderOptions } = options || {}
  return render(ui, {
    wrapper: ({ children }) => (
      <MemoryRouter initialEntries={route ? [route] : undefined}>
        {children}
      </MemoryRouter>
    ),
    ...renderOptions,
  })
}

export { render }
