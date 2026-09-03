// The order ids this phone has placed.
//
// There is no per-customer endpoint — the API keys orders by id and the cafe
// has no accounts (auth is stubbed, non-goals). So the phone remembers its own
// receipts and re-fetches each one. An id the server no longer knows is
// dropped rather than shown as an error: resetting the demo database should not
// leave a broken row on the screen forever.

const KEY = 'cafe-flow.orders.v1'
const LIMIT = 50

const read = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(raw) ? raw.filter((id) => typeof id === 'string') : []
  } catch {
    return []
  }
}

const write = (ids) => {
  try {
    localStorage.setItem(KEY, JSON.stringify(ids.slice(0, LIMIT)))
  } catch {
    /* private browsing, full quota: the app works, it just forgets */
  }
}

export const placedOrderIds = read

export function rememberOrder(id) {
  const ids = [id, ...read().filter((existing) => existing !== id)]
  write(ids)
  return ids.slice(0, LIMIT)
}

export function forgetOrder(id) {
  const ids = read().filter((existing) => existing !== id)
  write(ids)
  return ids
}
