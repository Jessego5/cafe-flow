import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { getConfig, getMenu, getOrder, placeOrder, money, elapsed, STATE_LABEL } from '../shared/api.js'
import { useTicker } from '../shared/useLive.js'
import '../shared/styles.css'

function MenuCard({ item, milks, onAdd }) {
  const [milk, setMilk] = useState(milks[0])
  return (
    <div className="card">
      <div className="spread">
        <strong>{item.name.replace(/_/g, ' ')}</strong>
        <span className="number">{money(item.price_cents)}</span>
      </div>
      <div className="muted">{item.stations.join(' → ')}</div>
      {item.requires_milk && (
        <div className="row" style={{ marginTop: '0.6rem' }}>
          <label className="muted" htmlFor={`milk-${item.name}`}>Milk</label>
          <select id={`milk-${item.name}`} value={milk} onChange={(e) => setMilk(e.target.value)}>
            {milks.map((option) => (
              <option key={option} value={option}>{option}</option>
            ))}
          </select>
        </div>
      )}
      <button
        className="primary wide"
        style={{ marginTop: '0.6rem' }}
        onClick={() => onAdd({ drink: item.name, milk_type: item.requires_milk ? milk : null })}
      >
        Add
      </button>
    </div>
  )
}

function Status({ orderId, onNew }) {
  const [order, setOrder] = useState(null)
  const [error, setError] = useState(null)
  const now = useTicker()

  useEffect(() => {
    let alive = true
    const refresh = () =>
      getOrder(orderId)
        .then((next) => alive && setOrder(next))
        .catch((err) => alive && setError(err.message))
    refresh()
    const source = new EventSource('/stream')
    source.addEventListener('state_change', refresh)
    source.onopen = refresh
    return () => {
      alive = false
      source.close()
    }
  }, [orderId])

  if (error) return <p className="error">{error}</p>
  if (!order) return <p className="muted">Loading…</p>

  const waiting = (now - Date.parse(order.placed_at)) / 1000
  return (
    <div className="card">
      <div className="spread">
        <span className="number" style={{ fontSize: '2.5rem' }}>#{order.number}</span>
        <span className="pill">{STATE_LABEL[order.state] || order.state}</span>
      </div>
      <ul className="items">
        {order.items.map((item) => (
          <li key={item.item_id}>
            {item.drink.replace(/_/g, ' ')}
            {item.milk_type ? ` · ${item.milk_type}` : ''}
          </li>
        ))}
      </ul>
      <div className="muted">
        {order.state === 'ready' ? 'Ready on the shelf' : `Waiting ${elapsed(waiting)}`} ·{' '}
        {money(order.price_cents)} · payment {order.payment.status}
      </div>
      <button className="ghost wide" style={{ marginTop: '0.6rem' }} onClick={onNew}>
        Start another order
      </button>
    </div>
  )
}

function App() {
  const [config, setConfig] = useState(null)
  const [menu, setMenu] = useState(null)
  const [cart, setCart] = useState([])
  const [orderId, setOrderId] = useState(null)
  const [error, setError] = useState(null)
  const [placing, setPlacing] = useState(false)

  useEffect(() => {
    Promise.all([getConfig(), getMenu()])
      .then(([c, m]) => {
        setConfig(c)
        setMenu(m)
      })
      .catch((err) => setError(err.message))
  }, [])

  const submit = () => {
    setPlacing(true)
    placeOrder(cart)
      .then((order) => {
        setOrderId(order.order_id)
        setCart([])
      })
      .catch((err) => setError(err.message))
      .finally(() => setPlacing(false))
  }

  const total = menu
    ? cart.reduce((sum, line) => sum + menu.items.find((i) => i.name === line.drink).price_cents, 0)
    : 0

  return (
    <>
      <header className="bar">
        <h1>{config ? config.cafe.name : 'Campus Cafe'} · order ahead</h1>
        <span className="muted">{config ? `open ${config.opens_at}–${config.closes_at}` : ''}</span>
      </header>
      <main>
        {error && <p className="error">{error}</p>}
        {orderId ? (
          <Status orderId={orderId} onNew={() => setOrderId(null)} />
        ) : (
          <>
            <h2>Menu</h2>
            {menu && (
              <div className="grid">
                {menu.items.map((item) => (
                  <MenuCard
                    key={item.name}
                    item={item}
                    milks={menu.milks}
                    onAdd={(line) => setCart((current) => [...current, line])}
                  />
                ))}
              </div>
            )}

            <h2>Cart</h2>
            {cart.length === 0 ? (
              <p className="muted">Nothing yet.</p>
            ) : (
              <div className="card">
                <ul className="items">
                  {cart.map((line, index) => (
                    <li key={index} className="spread">
                      <span>
                        {line.drink.replace(/_/g, ' ')}
                        {line.milk_type ? ` · ${line.milk_type}` : ''}
                      </span>
                      <button
                        className="ghost"
                        onClick={() => setCart(cart.filter((_, i) => i !== index))}
                      >
                        remove
                      </button>
                    </li>
                  ))}
                </ul>
                <div className="spread" style={{ marginTop: '0.6rem' }}>
                  <span className="number">{money(total)}</span>
                  <button className="primary big" disabled={placing} onClick={submit}>
                    {placing ? 'Placing…' : 'Place order'}
                  </button>
                </div>
                <p className="muted">Payment is stubbed — pay at the register.</p>
              </div>
            )}
          </>
        )}
      </main>
      <footer>{menu ? menu.provenance : ''}</footer>
    </>
  )
}

createRoot(document.getElementById('root')).render(<App />)
