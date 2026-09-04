import React, { useEffect, useRef, useState } from 'react'
import { getConfig, getMenu, placeOrder } from '../shared/api.js'
import { useTicker } from '../shared/useLive.js'
import { Home } from './Home.jsx'
import { OrderTab, lineKey } from './OrderTab.jsx'
import { OrdersTab } from './OrdersTab.jsx'
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
  const [channel, setChannel] = useState('walkup')
  const [placing, setPlacing] = useState(false)
  const [error, setError] = useState(null)

  const pane = useRef(null)
  const { menu, error: boardError } = useBoard()
  const { orders, fetchedAt, live, remember } = useMyOrders()
  const now = useTicker()

  useEffect(() => {
    getConfig().then(setConfig).catch((err) => setError(err.message))
  }, [])

  // One scroller serves all three tabs, so it goes back to the top when the
  // tab changes; otherwise the board opens halfway down where you left it.
  useEffect(() => {
    pane.current?.scrollTo({ top: 0 })
  }, [tab])

  const hours = serviceHours(config, new Date(now))
  const since = (now - fetchedAt) / 1000     // how stale the orders on screen are

  const openOrdering = (which) => {
    setChannel(which)
    setTab('order')
  }

  const place = (close) => {
    setPlacing(true)
    setError(null)
    placeOrder(cart, { channel })
      .then((order) => {
        remember(order.order_id)
        setCart([])
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
            since={since}
            onOrder={openOrdering}
          />
        )}
        {tab === 'order' && (
          <OrderTab
            config={config}
            menu={menu}
            hours={hours}
            cart={cart}
            channel={channel}
            onChannel={setChannel}
            onAdd={(lines) => setCart((current) => [...current, ...lines])}
            onRemove={(key) => setCart((current) => current.filter((line) => lineKey(line) !== key))}
            onPlace={place}
            placing={placing}
            error={error}
          />
        )}
        {tab === 'mine' && (
          <OrdersTab
            config={config}
            menu={menu}
            hours={hours}
            orders={orders}
            since={since}
            onOrder={() => setTab('order')}
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
