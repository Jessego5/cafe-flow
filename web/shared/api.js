// Thin fetch helpers. No domain logic lives in the browser: the views render
// what the API says and post transitions back.

export async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || `${response.status} ${response.statusText}`)
  }
  return response.json()
}

export const getConfig = () => request('/config')
export const getMenu = () => request('/menu')
export const getQueue = () => request('/queue')
export const getDisplay = () => request('/display')
export const getOrder = (id) => request(`/orders/${id}`)

// channel decides which queue the order joins, so the caller says which of the
// two front doors it came through; slot_id stays null while windows are off.
// `quoted` tells the server a ready time was shown before the customer
// committed, so it records what it promised and `promise_error` can be run over
// the log afterwards. The student view always shows one: the planner is the
// screen the order is placed from.
export const placeOrder = (lines, { channel = 'walkup', slotId = null, quoted = false } = {}) =>
  request('/orders', {
    method: 'POST',
    body: JSON.stringify({ lines, channel, slot_id: slotId, quoted }),
  })

// When it will be ready, or when to order for a time you have in mind. The
// quote is for a specific basket, so it is re-requested whenever the cart
// changes; `wanted_at` ("HH:MM", cafe local) picks the second direction.
export const plan = (lines, wantedAt = null) =>
  request('/plan', {
    method: 'POST',
    body: JSON.stringify(wantedAt ? { lines, wanted_at: wantedAt } : { lines }),
  })

export const moveOrder = (id, to, actor = 'barista') =>
  request(`/orders/${id}/transition`, { method: 'POST', body: JSON.stringify({ to, actor }) })

export const money = (cents) => `$${(cents / 100).toFixed(2)}`

// The API's timestamps are UTC, but they serialize without an offset. The
// tzinfo is dropped on the way into SQLite, and a naive ISO string is read as
// *local* time by the browser. Anywhere but UTC that silently shifts every
// clock on the screen, so the Z goes back on before parsing.
export const parseUtc = (iso) =>
  new Date(/(?:Z|[+-]\d{2}:?\d{2})$/.test(iso) ? iso : `${iso}Z`)

export function elapsed(seconds) {
  const total = Math.max(0, Math.round(seconds))
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

export const STATE_LABEL = {
  placed: 'Placed',
  accepted: 'Accepted',
  in_progress: 'Being made',
  ready: 'Ready',
  picked_up: 'Picked up',
  cancelled: 'Cancelled',
  abandoned: 'Abandoned',
  balked: 'Left the queue',
}
