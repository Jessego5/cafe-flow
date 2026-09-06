import React from 'react'
import { STATE_LABEL, elapsed, money } from '../shared/api.js'
import { milkLabel, title } from './catalog.js'

// One live order, as the person who placed it sees it: the number they will be
// called by, how far along it is, and how long they have been standing there.

const TRACK = ['placed', 'accepted', 'in_progress', 'ready']

export const lineText = (item) =>
  [item.variant ? title(item.variant) : null, title(item.drink)]
    .filter(Boolean)
    .join(' ') + (item.milk_type ? ` · ${milkLabel(item.milk_type)}` : '')

export function stateClass(state) {
  if (state === 'ready') return 'state ready'
  if (state === 'picked_up') return 'state'
  if (TRACK.includes(state)) return 'state live'
  return 'state gone'
}

export function Ticket({ order, since, onChange }) {
  const reached = TRACK.indexOf(order.state)
  // `waiting_s` is what the server measured when it answered; `since` is how
  // long ago that was. Same arithmetic the bar view does, and it never has to
  // trust the phone's clock to agree with the cafe's.
  const waiting = order.waiting_s + since

  return (
    <div className="ticket">
      <div className="head">
        <div>
          <span className="eyebrow">Order</span>
          <div className="no">#{order.number}</div>
        </div>
        <span className={stateClass(order.state)}>{STATE_LABEL[order.state] || order.state}</span>
      </div>

      <div className="track">
        {TRACK.map((state, index) => (
          <span key={state} className={reached >= index ? 'done' : ''} />
        ))}
      </div>

      <div className="hint">
        {order.state === 'ready'
          ? 'On the pickup shelf now'
          : `Waiting ${elapsed(waiting)} · ${order.channel === 'preorder' ? 'ordered ahead' : 'walked up'}`}
      </div>

      <ul>
        {order.items.map((item) => (
          <li key={item.item_id}>{lineText(item)}</li>
        ))}
      </ul>

      <div className="hint" style={{ marginTop: '0.5rem' }}>
        {money(order.price_cents)} · payment {order.payment.status}. Pay at the register
      </div>

      {/* Only while it is still `placed`. Once a barista has accepted it they
          are holding the cup, and the server refuses -- so the button goes
          rather than failing when tapped. */}
      {onChange && order.state === 'placed' && (
        <button className="drop" onClick={() => onChange(order)}>
          Change this order
        </button>
      )}
    </div>
  )
}
