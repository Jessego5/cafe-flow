import React from 'react'
import { STATE_LABEL, elapsed, money, parseUtc } from '../shared/api.js'
import { Price } from './Price.jsx'
import { Held } from './Held.jsx'
import { Ticket, lineText, stateClass } from './Ticket.jsx'
import { isLive } from './useMyOrders.js'

// How long that order actually took, measured rather than estimated: the
// event log carries the moment it reached the shelf, so the wait is that
// timestamp minus the moment it was placed. Orders that never got there
// simply do not have one.
function servedIn(order) {
  const ready = (order.events || []).find((event) => event.to_state === 'ready')
  if (!ready) return null
  return Math.max(0, ready.t_s - order.placed_at_s)
}

const median = (values) => {
  if (values.length === 0) return null
  const sorted = [...values].sort((a, b) => a - b)
  const mid = Math.floor(sorted.length / 2)
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2
}

const when = (iso) =>
  parseUtc(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })

function Receipt({ order }) {
  const wait = servedIn(order)
  return (
    <div className="receipt">
      <div className="head">
        <span className="no">#{order.number}</span>
        <span className="when">{when(order.placed_at)}</span>
      </div>
      <ul>
        {order.items.map((item) => (
          <li key={item.item_id}>{lineText(item)}</li>
        ))}
      </ul>
      <div className="meta">
        <span className={stateClass(order.state)}>{STATE_LABEL[order.state] || order.state}</span>
        <Price cents={order.price_cents} />
        <span className="hint">
          {[wait != null ? `ready in ${elapsed(wait)}` : null,
            order.channel === 'preorder' ? 'ordered ahead' : null]
            .filter(Boolean)
            .join(' · ')}
        </span>
      </div>
    </div>
  )
}

export function OrdersTab({ config, menu, hours, orders, holds = [], since, onOrder, onCancelHold, onChange }) {
  const done = (orders || []).filter((order) => !isLive(order))
  const live = (orders || []).filter(isLive)
  const items = (orders || []).reduce((sum, order) => sum + order.items.length, 0)
  // An order that was cancelled or walked away from was never paid for, so it
  // does not count against what this phone has spent.
  const spent = (orders || [])
    .filter((order) => !['cancelled', 'abandoned', 'balked'].includes(order.state))
    .reduce((sum, order) => sum + order.price_cents, 0)
  const waits = (orders || []).map(servedIn).filter((wait) => wait != null)
  const typical = median(waits)

  return (
    <div className="mine">
      <div className="who">
        <h1>Guest</h1>
      </div>
      {/* Accounts are stubbed, so there is nobody to greet. Saying so beats
          inventing a loyalty tier. */}
      <div className="since">
        No account. This phone remembers the orders it placed
      </div>

      <div className="member">
        <div className="title">{config ? config.cafe.name : 'Cafe'}</div>
        <div className="perks">
          {hours ? `Open ${hours.label}` : ''} · Pay at the register
        </div>
      </div>

      <div className="stats">
        <div>
          <div className="n">{(orders || []).length}</div>
          <div className="k">Orders</div>
        </div>
        <div>
          <div className="n">{items}</div>
          <div className="k">Items</div>
        </div>
        <div>
          <div className="n">{money(spent)}</div>
          <div className="k">Spent</div>
        </div>
        <div>
          <div className="n">{typical != null ? elapsed(typical) : '-'}</div>
          <div className="k">Typical wait</div>
        </div>
      </div>

      {holds.length > 0 && (
        <>
          <h2>Held for later</h2>
          <div style={{ display: 'grid', gap: '0.7rem', marginTop: '0.6rem' }}>
            {holds.map((row) => (
              <Held key={row.held_id} held={row} onCancel={onCancelHold} />
            ))}
          </div>
        </>
      )}

      {live.length > 0 && (
        <>
          <h2>Right now</h2>
          <div style={{ display: 'grid', gap: '0.7rem', marginTop: '0.6rem' }}>
            {live.map((order) => (
              <Ticket key={order.order_id} order={order} since={since} onChange={onChange} />
            ))}
          </div>
        </>
      )}

      <h2>Past orders</h2>
      {orders === null ? (
        <p className="empty">Loading…</p>
      ) : done.length === 0 ? (
        <div className="empty">
          <p style={{ margin: '0 0 1rem' }}>Nothing here yet.</p>
          <button className="slab" style={{ maxWidth: '12rem', margin: '0 auto' }} onClick={onOrder}>
            See the menu
          </button>
        </div>
      ) : (
        done.map((order) => <Receipt key={order.order_id} order={order} />)
      )}

      <p className="provenance">{menu ? menu.provenance : ''}</p>
    </div>
  )
}
