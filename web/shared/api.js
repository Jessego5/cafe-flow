// Thin fetch helpers. No domain logic lives in the browser: the views render
// what the API says and post transitions back.

async function request(path, options = {}) {
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

export const placeOrder = (lines) =>
  request('/orders', { method: 'POST', body: JSON.stringify({ lines, channel: 'walkup' }) })

export const moveOrder = (id, to, actor = 'barista') =>
  request(`/orders/${id}/transition`, { method: 'POST', body: JSON.stringify({ to, actor }) })

export const money = (cents) => `$${(cents / 100).toFixed(2)}`

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
