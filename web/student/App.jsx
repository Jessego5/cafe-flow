import React, { useEffect, useRef, useState } from 'react'
import { amendOrder, getConfig, getMenu, moveOrder, placeOrder } from '../shared/api.js'
import { useTicker } from '../shared/useLive.js'
import { title } from './catalog.js'
import { Home } from './Home.jsx'
import { OrderTab, lineKey } from './OrderTab.jsx'
import { OrdersTab } from './OrdersTab.jsx'
import { cancel as cancelHold, hold, myHolds, releasedOrderIds } from './held.js'
import { announceReady, ask } from './notify.js'
import { serviceHours } from './hours.js'
import { onLive } from './stream.js'
import { useMyOrders } from './useMyOrders.js'
import './student.css'

const TABS = [
  { key: 'home', label: 'Home' },
  { key: 'order', label: 'Order' },
  { key: 'mine', label: 'My Orders' },
]

// `/menu` carries the queue depth and the wait estimate along with the board,
// so it is refetched on every event rather than loaded once: the number a
// customer decides on has to be the number the bar is actually running.
function useBoard() {
  const [menu, setMenu] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let alive = true
    const refresh = () =>
      getMenu()
        .then((next) => alive && (setMenu(next), setError(null)))
        .catch((err) => alive && setError(err.message))
    refresh()
    const stop = onLive(refresh)
    const safety = setInterval(refresh, 20000)
    return () => {
      alive = false
      clearInterval(safety)
      stop()
    }
  }, [])

  return { menu, error }
}

export function App() {
  const [tab, setTab] = useState('home')
  const [config, setConfig] = useState(null)
  const [cart, setCart] = useState([])
  // Which of the two front doors they came through. 'ahead' ends in the
  // planner and a held order; 'now' goes straight to the queue.
  const [mode, setMode] = useState('now')
  const [holds, setHolds] = useState([])
  const [placing, setPlacing] = useState(false)
  // The order being changed, if any. A cart with this set is an amendment
  // rather than a new order, which is the only thing that makes `place` two
  // different verbs.
  const [editing, setEditing] = useState(null)
  // Remembered on the device, like the order ids: somebody who buys a coffee
  // every morning should not retype their own name every morning. It is a
  // label, not an identity, and nothing is looked up by it.
  const [name, setName] = useState(() => {
    try {
      return localStorage.getItem('cafe-flow.name.v1') || ''
    } catch {
      return ''
    }
  })

  const rename = (value) => {
    setName(value)
    try {
      localStorage.setItem('cafe-flow.name.v1', value)
    } catch {
      /* private browsing: it lasts the session */
    }
  }
  const [error, setError] = useState(null)

  const pane = useRef(null)
  const { menu, error: boardError } = useBoard()
  const { orders, fetchedAt, live, remember } = useMyOrders()
  const now = useTicker()

  useEffect(() => {
    getConfig().then(setConfig).catch((err) => setError(err.message))
  }, [])

  // The shelf announcement, from the stream the page is already reading. Only
  // on the edge into `ready`: a refetch that returns the same state is not an
  // event, and the announcement must not repeat on every poll.
  const seen = useRef({})
  useEffect(() => {
    if (!orders) return
    for (const order of orders) {
      const before = seen.current[order.order_id]
      seen.current[order.order_id] = order.state
      if (before && before !== 'ready' && order.state === 'ready') {
        announceReady(order, (o) =>
          o.items.map((item) => title(item.drink)).join(', ') + '.',
        )
      }
    }
  }, [orders])

  // One scroller serves all three tabs, so it goes back to the top when the
  // tab changes; otherwise the board opens halfway down where you left it.
  useEffect(() => {
    pane.current?.scrollTo({ top: 0 })
  }, [tab])

  const hours = serviceHours(config, new Date(now))
  const since = (now - fetchedAt) / 1000     // how stale the orders on screen are

  const openOrdering = (which) => {
    setMode(which)
    setTab('order')
  }

  // Pay and hold. The server re-derives the timing and runs the release loop,
  // so nothing here waits for anything. Permission for the ready notification
  // is asked at the moment somebody commits to a time, which is the moment it
  // means something.
  const placeHold = (quote) => {
    setError(null)
    hold(cart, quote.wanted_at, name.trim() || null)
      .then((row) => {
        setHolds((current) => [...current, row])
        setCart([])
        ask()
        setTab('mine')
      })
      .catch((err) => setError(err.message))
  }

  // Cancelling is the customer's own move: `POST /orders/{id}/transition` lets
  // it through without a staff session, on the same reasoning as the rest of
  // the customer side, that holding the unguessable id is what proves the
  // order is yours. Nothing is set here, because the transition broadcasts and
  // `useMyOrders` refetches on the event like every other change.
  const cancelOrder = (order) =>
    moveOrder(order.order_id, 'cancelled', 'customer')

  const dropHold = (heldId) => {
    cancelHold(heldId)
      .then(() => setHolds((current) => current.filter((row) => row.held_id !== heldId)))
      .catch((err) => setError(err.message))
  }

  // There is no release loop in here on purpose. The server decides, against a
  // queue this tab may not be open to see. All this does is ask what happened,
  // and adopt any order the app placed on the customer's behalf so it shows up
  // beside the ones they placed themselves.
  useEffect(() => {
    let live = true
    const sync = () => {
      myHolds()
        .then((rows) => live && setHolds(rows))
        .catch(() => {})
      releasedOrderIds()
        .then((ids) => ids.forEach(remember))
        .catch(() => {})
    }
    sync()
    const timer = setInterval(sync, 20000)
    return () => {
      live = false
      clearInterval(timer)
    }
  }, [remember])

  // Load a placed order back into the cart. The server refuses an amendment
  // once the order leaves `placed`, so the button that got here is already gone
  // by then, but the request can still lose the race, and says so.
  const startEditing = (order) => {
    setEditing(order.order_id)
    setCart(
      order.items.map((item) => ({
        drink: item.drink,
        milk_type: item.milk_type ?? null,
        variant: item.variant ?? null,
      })),
    )
    setMode('now')
    setError(null)
    setTab('order')
  }

  const stopEditing = () => {
    setEditing(null)
    setCart([])
    setTab('mine')
  }

  const place = (close) => {
    setPlacing(true)
    setError(null)
    const sent = editing
      ? amendOrder(editing, cart)
      : placeOrder(cart, { channel: 'walkup', quoted: true, name: name.trim() || null })
    sent
      .then((order) => {
        remember(order.order_id)
        setCart([])
        setEditing(null)
        close?.()
        setTab('mine')
      })
      .catch((err) => setError(err.message))
      .finally(() => setPlacing(false))
  }

  return (
    <div className="phone">
      <div className={`pane${tab === 'order' ? ' has-dock' : ''}`} ref={pane}>
        {tab === 'home' && (
          <Home
            config={config}
            menu={menu}
            hours={hours}
            live={live}
            holds={holds}
            since={since}
            onOrder={openOrdering}
            onCancelHold={dropHold}
            onCancelOrder={cancelOrder}
            onChange={startEditing}
          />
        )}
        {tab === 'order' && (
          <OrderTab
            config={config}
            menu={menu}
            hours={hours}
            cart={cart}
            mode={mode}
            onMode={setMode}
            onAdd={(lines) => setCart((current) => [...current, ...lines])}
            onRemove={(key) => setCart((current) => current.filter((line) => lineKey(line) !== key))}
            onPlace={place}
            onHold={placeHold}
            editing={editing}
            onStopEditing={stopEditing}
            name={name}
            onName={rename}
            placing={placing}
            error={error}
          />
        )}
        {tab === 'mine' && (
          <OrdersTab
            config={config}
            hours={hours}
            orders={orders}
            holds={holds}
            since={since}
            onOrder={() => setTab('order')}
            onCancelHold={dropHold}
            onCancelOrder={cancelOrder}
            onChange={startEditing}
          />
        )}

        {(error || boardError) && tab !== 'order' && (
          <p className="strike" style={{ padding: '0 var(--pad)' }}>{error || boardError}</p>
        )}
      </div>

      <nav className="tabbar">
        {TABS.map(({ key, label }) => (
          <button key={key} className={key === tab ? 'on' : ''} onClick={() => setTab(key)}>
            {label}
            {key === 'mine' && live.length > 0 && <span className="dot" />}
          </button>
        ))}
      </nav>
    </div>
  )
}

export default App
