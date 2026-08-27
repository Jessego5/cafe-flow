import { useEffect, useRef, useState } from 'react'

// One live-data hook for the staff views.
//
// The stream is a signal, not the state: every event triggers a full refetch of
// the view's own endpoint, and reconnecting refetches before resuming. A view
// that applied events incrementally would show a quietly wrong queue after any
// dropped connection, which on cafe wifi is a matter of when, not if.
const EVENT_TYPES = ['state_change', 'station_start', 'station_end', 'batch_formed']
const SAFETY_REFRESH_MS = 15000

export function useLive(fetcher) {
  const [data, setData] = useState(null)
  const [connected, setConnected] = useState(false)
  const [error, setError] = useState(null)
  const [fetchedAt, setFetchedAt] = useState(() => Date.now())
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    let alive = true

    const refresh = () =>
      fetcherRef
        .current()
        .then((next) => {
          if (!alive) return
          setData(next)
          setFetchedAt(Date.now())
          setError(null)
        })
        .catch((err) => alive && setError(err.message))

    refresh()
    const source = new EventSource('/stream')
    source.onopen = () => {
      setConnected(true)
      refresh()
    }
    source.onerror = () => setConnected(false)
    EVENT_TYPES.forEach((type) => source.addEventListener(type, refresh))
    const safety = setInterval(refresh, SAFETY_REFRESH_MS)

    return () => {
      alive = false
      clearInterval(safety)
      source.close()
    }
  }, [])

  return { data, connected, error, fetchedAt, refresh: () => fetcherRef.current().then(setData) }
}

// Wall-clock ticker so waiting times keep counting between refreshes.
export function useTicker(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(id)
  }, [intervalMs])
  return now
}
