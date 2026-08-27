import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { getQueue, moveOrder, elapsed } from '../shared/api.js'
import { useLive, useTicker } from '../shared/useLive.js'
import '../shared/styles.css'

// One tap per transition. The button is the only affordance on a card, so a
// barista mid-rush never has to choose.
const NEXT = {
  placed: { to: 'accepted', label: 'Accept' },
  accepted: { to: 'in_progress', label: 'Start' },
  in_progress: { to: 'ready', label: 'Ready' },
  ready: { to: 'picked_up', label: 'Picked up' },
}

const LATE_S = 300

// The scheduler's advice, from the same policy the experiments measured.
// Advisory: the barista decides, this says what it would be worth.
function Together({ batches }) {
  if (!batches || batches.length === 0) return null
  return (
    <section className="together">
      {batches.map((batch, index) => (
        <div className="card run-together" key={`${batch.station}-${index}`}>
          <div className="spread">
            <strong>Run together · {batch.station.replace(/_/g, ' ')}</strong>
            <span className="muted">saves {Math.round(batch.saving_s)}s</span>
          </div>
          <div className="row wrap">
            {batch.items.map((item) => (
              <span className="chip" key={item.order_id + item.drink}>
                <span className="number">#{item.number}</span> {item.drink.replace(/_/g, ' ')}
                {item.milk_type ? ` · ${item.milk_type}` : ''}
              </span>
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}

function Card({ order, since, onMove, busy }) {
  const next = NEXT[order.state]
  const waiting = order.waiting_s + since
  return (
    <div className="card">
      <div className="head">
        <span className="number">#{order.number}</span>
        <span className={waiting > LATE_S ? 'waiting warn' : 'waiting muted'}>{elapsed(waiting)}</span>
        <span className="pill">{order.state.replace(/_/g, ' ')}</span>
        {order.is_simulated && <span className="pill sim">simulated</span>}
      </div>
      <ul className="items">
        {order.items.map((item) => (
          <li key={item.item_id}>
            {item.variant && <strong className="variant">{item.variant} </strong>}
            {item.drink.replace(/_/g, ' ')}
            {item.milk_type ? <strong> · {item.milk_type}</strong> : ''}
          </li>
        ))}
      </ul>
      <div className="row">
        {next && (
          <button className="primary big" disabled={busy} onClick={() => onMove(order.order_id, next.to)}>
            {next.label}
          </button>
        )}
        <button className="ghost" disabled={busy} onClick={() => onMove(order.order_id, 'cancelled')}>
          Cancel
        </button>
      </div>
    </div>
  )
}

function App() {
  const { data, connected, error, fetchedAt, refresh } = useLive(getQueue)
  const now = useTicker()
  const [busy, setBusy] = useState(null)
  const [actionError, setActionError] = useState(null)

  const since = (now - fetchedAt) / 1000

  const move = (orderId, to) => {
    setBusy(orderId)
    // idempotent by (order, target): a double tap on a laggy connection cannot
    // skip a state
    moveOrder(orderId, to)
      .then(refresh)
      .catch((err) => setActionError(err.message))
      .finally(() => setBusy(null))
  }

  const orders = data ? data.orders : []
  const batches = data ? data.batches : []

  return (
    <>
      <header className="bar">
        <h1>Bar queue</h1>
        <div className="row">
          {data && <span className="pill">{data.policy.replace(/_/g, ' ')}</span>}
          {data && data.showing_simulated && <span className="pill sim">{data.env}</span>}
          <span className={connected ? 'pill live' : 'pill down'}>
            {connected ? 'live' : 'not connected'}
          </span>
        </div>
      </header>
      <main>
        {error && <p className="error">{error}</p>}
        {actionError && <p className="error">{actionError}</p>}
        <Together batches={batches} />
        {orders.length === 0 ? (
          <p className="empty">{data ? 'Queue is clear.' : 'Loading…'}</p>
        ) : (
          <div className="queue">
            {orders.map((order) => (
              <Card
                key={order.order_id}
                order={order}
                since={since}
                onMove={move}
                busy={busy === order.order_id}
              />
            ))}
          </div>
        )}
      </main>
    </>
  )
}

createRoot(document.getElementById('root')).render(<App />)
