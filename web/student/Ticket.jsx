import React, { useState } from 'react'
import { STATE_LABEL, elapsed, money } from '../shared/api.js'
import { milkLabel, title } from './catalog.js'

// One live order, as the person who placed it sees it: the number they will be
// called by, how far along it is, and how long they have been standing there.

const TRACK = ['placed', 'accepted', 'in_progress', 'ready']

// What `core.states` allows a customer to cancel from. Once it is on the shelf
// the cup exists and the only honest moves are collecting it or walking away,
// so the button is gone rather than present and refused.
const CANCELLABLE = ['placed', 'accepted', 'in_progress']

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

export function Ticket({ order, since, onChange, onCancel }) {
  const reached = TRACK.indexOf(order.state)
  // Two taps, because this one cannot be undone: `cancelled` is terminal, and
  // a re-order is a new number and a new place in the line.
  const [confirming, setConfirming] = useState(false)
  const [failed, setFailed] = useState(null)
  const changeable = Boolean(onChange) && order.state === 'placed'
  const cancellable = Boolean(onCancel) && CANCELLABLE.includes(order.state)
  // `waiting_s` is what the server measured when it answered; `since` is how
  // long ago that was. Same arithmetic the bar view does, and it never has to
  // trust the phone's clock to agree with the cafe's.
  const waiting = order.waiting_s + since

  return (
    <div className="ticket">
      <div className="head">
        <div>
          <span className="eyebrow">{order.customer_name || 'Order'}</span>
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

      {failed && <p className="strike">{failed}</p>}

      {confirming ? (
        <div className="ask">
          <span className="hint">
            {order.state === 'placed'
              ? 'Cancel this order? A new one goes to the back of the line.'
              : 'The bar has started this one. Cancel it anyway?'}
          </span>
          <div className="actions">
            <button onClick={() => setConfirming(false)}>Keep it</button>
            <button
              className="drop"
              onClick={() => {
                setFailed(null)
                // The bar can move it between this render and this tap, and
                // then the server is right and the screen was stale.
                onCancel(order).catch((err) => {
                  setConfirming(false)
                  setFailed(err.message)
                })
              }}
            >
              Yes, cancel
            </button>
          </div>
        </div>
      ) : (
        (changeable || cancellable) && (
          <div className="actions">
            {/* Only while it is still `placed`. Once a barista has accepted it
                they are holding the cup and the server refuses, so the button
                goes rather than failing when tapped. */}
            {changeable && <button onClick={() => onChange(order)}>Change this order</button>}
            {cancellable && (
              <button
                className="drop"
                onClick={() => {
                  setFailed(null)
                  setConfirming(true)
                }}
              >
                Cancel order
              </button>
            )}
          </div>
        )
      )}
    </div>
  )
}
