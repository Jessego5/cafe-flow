// `POST /held`, `GET /held/{id}`, `DELETE /held/{id}`.
//
// A held order is paid for and not yet in the queue. The server holds it and
// puts it in when the *live* line says ordering now lands by the time asked
// for, so there is no release loop here, and there must not be one: a loop in
// the browser only runs while a tab is open, which is the exact thing holding
// the order server-side exists to avoid.
//
// What the device keeps is a list of ids. There are no accounts, so this is how
// the phone knows which holds are its own, the same way `history.js` remembers
// which orders it placed.

import { request } from '../shared/api.js'

const KEY = 'cafe-flow.held.v1'

const read = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '[]')
    return Array.isArray(raw) ? raw : []
  } catch {
    return []
  }
}

const write = (ids) => {
  try {
    localStorage.setItem(KEY, JSON.stringify(ids))
  } catch {
    /* private browsing: the hold is still the server's, this phone just forgets */
  }
}

const remember = (heldId) => write([...new Set([...read(), heldId])])
const forget = (heldId) => write(read().filter((id) => id !== heldId))

/** Pay and hold. The server re-derives the timing; it does not take ours. */
export async function hold(lines, wantedAt, name = null) {
  const row = await request('/held', {
    method: 'POST',
    body: JSON.stringify({ lines, wanted_at: wantedAt, customer_name: name }),
  })
  remember(row.held_id)
  return row
}

/** Idempotent, like the endpoint. */
export async function cancel(heldId) {
  await fetch(`/held/${heldId}`, { method: 'DELETE' })
  forget(heldId)
}

/** This device's holds, as the server currently sees them.
 *
 * Released and cancelled ones are dropped from the device as they are found:
 * the order they became is in `history.js` by then, and a held order that has
 * already happened is not something anybody wants on their screen.
 */
export async function myHolds() {
  const ids = read()
  if (ids.length === 0) return []
  const rows = await Promise.all(
    ids.map((id) =>
      request(`/held/${id}`).catch(() => null),   // 404: gone from the server
    ),
  )
  const live = []
  rows.forEach((row, index) => {
    if (row === null) return forget(ids[index])
    if (row.state === 'held') return live.push(row)
    forget(row.held_id)
  })
  return live
}

/** Order ids the server made from this device's holds, so `history` can adopt
 *  them: the customer never placed these, the app did. */
export async function releasedOrderIds() {
  const rows = await Promise.all(read().map((id) => request(`/held/${id}`).catch(() => null)))
  return rows.filter((row) => row && row.state === 'released' && row.order_id).map((row) => row.order_id)
}
