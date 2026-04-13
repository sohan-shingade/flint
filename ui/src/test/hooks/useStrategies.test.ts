import { describe, it, expect } from 'vitest'
import { renderHook, act, waitFor } from '@testing-library/react'
import { useStrategies } from '../../hooks/useStrategies'
import { server } from '../mocks/handlers'
import { http, HttpResponse } from 'msw'

describe('useStrategies', () => {
  it('fetches strategy list on mount', async () => {
    const { result } = renderHook(() => useStrategies())

    await waitFor(() => {
      expect(result.current.strategies.length).toBeGreaterThan(0)
    })

    expect(result.current.strategies[0]).toHaveProperty('name')
    expect(result.current.strategies[0]).toHaveProperty('file')
  })

  it('save() POSTs name and code then refreshes list', async () => {
    let postBody: any = null
    server.use(
      http.post('/api/v1/user-strategies', async ({ request }) => {
        postBody = await request.json()
        return HttpResponse.json({ ok: true })
      })
    )

    const { result } = renderHook(() => useStrategies())

    await act(async () => {
      await result.current.save('test_strat', 'class MyStrat: pass')
    })

    expect(postBody).toEqual({ name: 'test_strat', code: 'class MyStrat: pass' })
  })

  it('load() fetches strategy code by name', async () => {
    const { result } = renderHook(() => useStrategies())

    let code = ''
    await act(async () => {
      code = await result.current.load('my_strategy')
    })

    expect(code).toContain('Strategy: my_strategy')
  })

  it('remove() calls DELETE and refreshes', async () => {
    let deletedName = ''
    server.use(
      http.delete('/api/v1/user-strategies/:name', ({ params }) => {
        deletedName = params.name as string
        return HttpResponse.json({ deleted: true })
      })
    )

    const { result } = renderHook(() => useStrategies())

    await act(async () => {
      await result.current.remove('my_strategy')
    })

    expect(deletedName).toBe('my_strategy')
  })

  it('validate() POSTs code and returns validation result', async () => {
    const { result } = renderHook(() => useStrategies())

    let validationResult: any
    await act(async () => {
      validationResult = await result.current.validate('class MyStrat: pass')
    })

    expect(validationResult.valid).toBe(true)
  })

  it('validate() returns error for invalid code', async () => {
    server.use(
      http.post('/api/v1/user-strategies/validate', () => {
        return HttpResponse.json({ valid: false, error: 'Missing on_candle method' })
      })
    )

    const { result } = renderHook(() => useStrategies())

    let validationResult: any
    await act(async () => {
      validationResult = await result.current.validate('invalid code')
    })

    expect(validationResult.valid).toBe(false)
    expect(validationResult.error).toContain('on_candle')
  })
})
