import React from 'react'
import { FEATURE, blurb, title } from './catalog.js'
import { Price } from './Price.jsx'
import { SeasonalArt } from './marks.jsx'
import { Ticket } from './Ticket.jsx'

const minutes = (seconds) => Math.max(1, Math.round(seconds / 60))

// What the queue looks like before you commit to joining it.
//
// This is the whole point of the screen. A quoted twelve minutes at noon sends
// some people back at twenty past, and moving an arrival out of the peak is
// worth more than anything the bar can do about it once they are in the line.
function Wait({ menu, hours }) {
  if (!menu) return null
  const wait = menu.wait_estimate_s
  const depth = menu.queue_depth

  return (
    <div className="wait-card">
      <span className="eyebrow">The line right now</span>
      <div className="spread" style={{ marginTop: '0.35rem' }}>
        <div>
          <div className="big">
            {hours && !hours.open ? '—' : depth === 0 ? 'No wait' : `${minutes(wait)} min`}
          </div>
          <div className="hint">
            {hours && !hours.open
              ? hours.notice
              : depth === 0
                ? 'Nobody ahead of you'
                : `${depth} ${depth === 1 ? 'order' : 'orders'} ahead of you`}
          </div>
        </div>
        <div className="hint" style={{ textAlign: 'right', maxWidth: '9.5rem' }}>
          An estimate you could make yourself: people ahead, times how long each looks like taking.
        </div>
      </div>
    </div>
  )
}

export function Home({ config, menu, hours, live, since, onOrder }) {
  const byName = new Map((menu?.items || []).map((item) => [item.name, item]))
  const seasonal = byName.get(FEATURE.seasonal)
  const returning = byName.get(FEATURE.returning)

  return (
    <div className="home">
      <div className="chrome">
        <div className="capsule">
          <span>{config ? config.cafe.name : 'Cafe'}</span>
          <span className="rule" />
          <span>{hours ? (hours.open ? 'Open' : 'Closed') : '·'}</span>
        </div>
      </div>

      <div className="hero">
        <span className="eyebrow">{FEATURE.eyebrow}</span>
        <h1>
          {FEATURE.headline[0]}
          <br />
          <em>{FEATURE.headline[1]}</em>
        </h1>
        <div className="art">
          <SeasonalArt />
        </div>
      </div>

      <div className="feature">
        {seasonal && (
          <div className="line">
            <span className="name">{title(seasonal.name)}</span>
            <span className="tag">New</span>
            <Price cents={seasonal.price_cents} />
          </div>
        )}
        {returning && (
          <div className="line">
            <span className="name">{title(returning.name)}</span>
            <span className="tag back">Back</span>
            <Price cents={returning.price_cents} />
          </div>
        )}
        {seasonal && <p className="hint" style={{ margin: 0 }}>{blurb(seasonal.name)}</p>}
      </div>

      {/* The two front doors. Ordering ahead is the thing this cafe is trying
          to find out about, so it gets equal weight, not a link in a menu. */}
      <div className="doors">
        <div className="door">
          <button className="slab" onClick={() => onOrder('walkup')}>
            Order Now
          </button>
          <span className="under">join the line</span>
        </div>
        <div className="door">
          <button className="slab" onClick={() => onOrder('preorder')}>
            Order Ahead
          </button>
          <span className="under">
            {config?.slots_enabled ? 'pick a window' : 'skip the register'}
          </span>
        </div>
      </div>

      <div className="rule-row">
        <span className="on" />
        <span />
      </div>

      <Wait menu={menu} hours={hours} />

      {live.length > 0 && (
        <div style={{ padding: '1.4rem var(--pad) 0', display: 'grid', gap: '0.7rem' }}>
          <span className="eyebrow">In progress</span>
          {live.map((order) => (
            <Ticket key={order.order_id} order={order} since={since} />
          ))}
        </div>
      )}

      <p className="provenance">{menu ? menu.provenance : ''}</p>
    </div>
  )
}
