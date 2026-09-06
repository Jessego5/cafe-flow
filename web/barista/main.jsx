import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { getMenu, getQueue, moveOrder, setAvailability, elapsed } from '../shared/api.js'
import { milkLabel, title } from '../student/catalog.js'
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

// A promise is at risk once there is less time left on it than the bar is
// currently quoting for a new order. That is the moment it stops being
// deliverable, and it moves with the queue rather than sitting at some fixed
// number of minutes. Flagging it before it is missed is the whole point.
const atRisk = (slack, quotedS) => slack != null && slack < (quotedS ?? 0)

const clock = (seconds) => {
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600) % 24
  const m = Math.floor((total % 3600) / 60)
  return `${h}:${String(m).padStart(2, '0')}`
}

// The same figure the customer is being shown, from the same endpoint. Staff
// should be able to see what the app is committing them to.
function Promised({ queue }) {
  if (!queue || queue.wait_estimate_s == null) return null
  const minutes = Math.max(1, Math.round(queue.wait_estimate_s / 60))
  return (
    <span className="pill">
      quoting {minutes} min · {queue.queue_depth} waiting
    </span>
  )
}

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
                <span className="number">#{item.number}</span> {title(item.drink)}
                {item.milk_type ? ` · ${milkLabel(item.milk_type)}` : ''}
              </span>
            ))}
          </div>
        </div>
      ))}
    </section>
  )
}

function Card({ order, since, onMove, busy, nowS, quotedS }) {
  const next = NEXT[order.state]
  const waiting = order.waiting_s + since
  const promised = order.promised_at_s
  const slack = promised == null ? null : promised - (nowS + since)
  const done = order.state === 'ready' || order.state === 'picked_up'
  const late = !done && atRisk(slack, quotedS)
  return (
    <div className={`card${late ? ' at-risk' : ''}`}>
      <div className="head">
        <span className="number">#{order.number}</span>
        {/* What gets called across the counter. The number is what the public
            display shows; this is not on it. */}
        {order.customer_name && <span className="called">{order.customer_name}</span>}
        <span className={waiting > LATE_S ? 'waiting warn' : 'waiting muted'}>{elapsed(waiting)}</span>
        <span className="pill">{order.state.replace(/_/g, ' ')}</span>
        {order.is_simulated && <span className="pill sim">simulated</span>}
        {promised != null && (
          <span className={`pill${late ? ' bad' : ''}`}>
            {done
              ? `promised ${clock(promised)}`
              : slack < 0
                ? `${elapsed(-slack)} past promise`
                : `${elapsed(slack)} to ${clock(promised)}`}
          </span>
        )}
      </div>
      <ul className="items">
        {order.items.map((item) => (
          <li key={item.item_id}>
            {item.variant && <strong className="variant">{item.variant} </strong>}
            {title(item.drink)}
            {item.milk_type ? <strong> · {milkLabel(item.milk_type)}</strong> : ''}
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

// What the cafe has run out of. The bar is the only surface that knows, and
// the student view greys the item out within one poll of being told.
function SoldOut({ onError }) {
  const [items, setItems] = useState(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(null)

  const load = () => getMenu().then((menu) => setItems(menu.items)).catch(() => {})
  useEffect(() => { load() }, [])

  const toggle = (item) => {
    setBusy(item.name)
    setAvailability(item.name, item.available === false)
      .then(load)
      .catch((err) => onError(err.message))
      .finally(() => setBusy(null))
  }

  const out = (items || []).filter((item) => item.available === false)

  return (
    <section className="sold-out">
      <button className="head" onClick={() => setOpen((v) => !v)}>
        <span className="eyebrow">Sold out</span>
        <span className="pill">{out.length}</span>
        <span className="chev">{open ? '▾' : '▸'}</span>
      </button>

      {/* Closed, the bar still sees what is off, because that is the thing
          somebody needs at a glance mid-rush. */}
      {!open && out.length > 0 && (
        <div className="row">
          {out.map((item) => (
            <button key={item.name} className="pill out" disabled={busy === item.name}
                    onClick={() => toggle(item)}>
              {title(item.name)} · put back
            </button>
          ))}
        </div>
      )}

      {open && (
        <div className="grid">
          {(items || []).map((item) => (
            <button
              key={item.name}
              className={item.available === false ? 'chip out' : 'chip'}
              disabled={busy === item.name}
              onClick={() => toggle(item)}
            >
              {title(item.name)}
            </button>
          ))}
        </div>
      )}
    </section>
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

  // A promised order is sorted by how little time is left on it; everything
  // else keeps the queue's own order behind them.
  const orders = data
    ? [...data.orders].sort((a, b) => {
        const left = (o) => (o.promised_at_s == null ? Infinity : o.promised_at_s)
        return left(a) - left(b)
      })
    : []
  const batches = data ? data.batches : []

  return (
    <>
      <header className="bar">
        <h1>Bar queue</h1>
        <div className="row">
          <Promised queue={data} />
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
        <SoldOut onError={setActionError} />
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
                nowS={data.now_s}
                quotedS={data.wait_estimate_s}
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
