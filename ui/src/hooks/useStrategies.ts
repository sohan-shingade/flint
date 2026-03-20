import { useState, useEffect, useCallback } from 'react'

interface UserStrategy {
  name: string
  file: string
}

export function useStrategies() {
  const [strategies, setStrategies] = useState<UserStrategy[]>([])
  const [loading, setLoading] = useState(false)

  const fetchStrategies = useCallback(async () => {
    try {
      const res = await fetch('/api/v1/user-strategies')
      const data = await res.json()
      setStrategies(data.strategies || [])
    } catch {
      setStrategies([])
    }
  }, [])

  useEffect(() => { fetchStrategies() }, [fetchStrategies])

  const save = async (name: string, code: string) => {
    setLoading(true)
    try {
      await fetch('/api/v1/user-strategies', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, code }),
      })
      await fetchStrategies()
    } finally {
      setLoading(false)
    }
  }

  const load = async (name: string): Promise<string> => {
    const res = await fetch(`/api/v1/user-strategies/${name}`)
    const data = await res.json()
    return data.code || ''
  }

  const remove = async (name: string) => {
    await fetch(`/api/v1/user-strategies/${name}`, { method: 'DELETE' })
    await fetchStrategies()
  }

  const validate = async (code: string) => {
    const res = await fetch('/api/v1/user-strategies/validate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    })
    return res.json()
  }

  return { strategies, save, load, remove, validate, loading, refresh: fetchStrategies }
}
