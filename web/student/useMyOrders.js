import { useCallback, useEffect, useState } from 'react'
import { getOrder } from '../shared/api.js'
import { forgetOrder, placedOrderIds, rememberOrder } from './history.js'
import { onLive } from './stream.js'

// The receipts this phone is holding, kept in step with the bar.
//
// Same discipline as the staff views: the event stream is a signal, not the
// state. Anything arriving on it triggers a refetch of every order this phone
// knows about, and a reconnect refetches too, so a dropped connection cannot
// leave a stale "being made" on the screen. An id the server has forgotten is
// pruned instead of surfaced.
const SAFETY_REFRESH_MS = 15000

const TERMINAL = new Set(['picked_up', 'cancelled', 'abandoned', 'balked'])
export const isLive = (order) => !TERMINAL.has(order.state)

export function useMyOrders() {
  const [ids, setIds] = useState(placedOrderIds)
  const [orders, setOrders] = useState(null)
  const [fetchedAt, setFetchedAt] = useState(() => Date.now())

  const load = useCallback(async (wanted) => {
    const settled = await Promise.all(
      wanted.map((id) =>
        getOrder(id).then(
          (order) => order,
          (error) => ({ id, missing: /^no order|404/i.test(error.message) }),
        ),
      ),
    )
    const gone = settled.filter((entry) => entry.missing).map((entry) => entry.id)
    if (gone.length) {
      gone.forEach(forgetOrder)
      setIds(placedOrderIds())
    }
    // newest first, which is the order the phone remembered them in
    return settled.filter((entry) => entry.order_id)
  }, [])

  useEffect(() => {
    if (ids.length === 0) {
      setOrders([])
      return undefined
    }
    let alive = true
    const refresh = () =>
      load(ids).then((next) => {
        if (!alive) return
        setOrders(next)
        setFetchedAt(Date.now())
      })

    refresh()
    const stop = onLive(refresh)
    const safety = setInterval(refresh, SAFETY_REFRESH_MS)

    return () => {
      alive = false
      clearInterval(safety)
      stop()
    }
  }, [ids, load])

  const remember = useCallback((id) => setIds(rememberOrder(id)), [])

  return { orders, fetchedAt, remember, live: (orders || []).filter(isLive) }
}
