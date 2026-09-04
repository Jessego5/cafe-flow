import React from 'react'
import { FEATURE, nameLines, title } from './catalog.js'
import { Price } from './Price.jsx'
import cafe from './cafe.webp'
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
            {wait == null ? '—' : depth === 0 ? 'No wait' : `${minutes(wait)} min`}
          </div>
          <div className="hint">
            {wait == null
              ? hours?.notice || 'Closed'
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
  const featured = FEATURE.items.map((name) => byName.get(name)).filter(Boolean)
  const [head, tail] = nameLines(config ? config.cafe.name : '')

  return (
    <div className="home">
      {/* The building, drawn. It is the one image on this screen that says
          where you are going to be standing. Multiply blending drops the
          sketch's white paper into the page's own, so there is no plate edge
          around it. */}
      <div className="banner">
        <img src={cafe} alt={`${config ? config.cafe.name : 'The cafe'}, drawn from the street`} />
        <div className="capsule">
          <span>{config ? config.cafe.name : 'Cafe'}</span>
          <span className="rule" />
          <span>{hours ? (hours.open ? 'Open' : 'Closed') : '·'}</span>
        </div>
      </div>

      <div className="hero">
        <h1>
          {head}
          <br />
          <em>{tail}</em>
        </h1>
        <span className="hint">{FEATURE.pouring}</span>
      </div>

      {/* The wait comes before the two doors, not after them. It is the whole
          reason this screen exists — someone who reads twelve minutes at noon
          and comes back at twenty past has moved themselves out of the peak —
          and a number placed below the buttons is read after the decision it
          was meant to inform. */}
      <Wait menu={menu} hours={hours} />

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


      {/* The four the bar makes most, by the drink shares in params. Nothing
          here is billed as new or returning: the boards say no such thing, and
          this app should not either. */}
      <div className="feature">
        <span className="eyebrow">Most ordered</span>
        {featured.map((item) => (
          <div className="line" key={item.name}>
            <span className="name">{title(item.name)}</span>
            <Price cents={item.price_cents} />
          </div>
        ))}
      </div>

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
