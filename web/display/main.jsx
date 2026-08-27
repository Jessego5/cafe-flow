import React from 'react'
import { createRoot } from 'react-dom/client'
import { getDisplay, elapsed } from '../shared/api.js'
import { useLive, useTicker } from '../shared/useLive.js'
import '../shared/styles.css'

function App() {
  const { data, connected, fetchedAt } = useLive(getDisplay)
  const now = useTicker()
  const since = (now - fetchedAt) / 1000
  const ready = data ? data.ready : []

  return (
    <>
      <header className="bar">
        <h1>Ready for pickup</h1>
        <span className={connected ? 'pill live' : 'pill down'}>
          {connected ? 'live' : 'not connected'}
        </span>
      </header>
      {ready.length === 0 ? (
        <p className="empty">Nothing on the shelf.</p>
      ) : (
        <div className="display-grid">
          {ready.map((order) => (
            <div className="card" key={order.order_id}>
              <div className="number">{order.number}</div>
              <div className="muted" style={{ textAlign: 'center' }}>
                {elapsed(order.ready_for_s + since)}
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

createRoot(document.getElementById('root')).render(<App />)
