/**
 * MSW server for the UI test suite.
 *
 * The greenfield screens each register their own handlers per test via
 * `server.use(...)` with hand-authored fixtures (D26), so there are no shared
 * default handlers — this module exists only to own the single MSW server that
 * `src/test/setup.ts` starts/stops around the run. An unhandled request warns
 * (see setup.ts), which surfaces any screen that forgot to stub an endpoint.
 */
import { setupServer } from 'msw/node'

export const server = setupServer()
