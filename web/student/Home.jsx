import React from 'react'
import { FEATURE, nameLines, title } from './catalog.js'
import { Price } from './Price.jsx'
import cafe from './cafe.webp'

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
            {wait == null ? '-' : `${minutes(wait || menu.seconds_per_order)} min`}
          </div>
          <div className="hint">
            {wait == null
              ? hours?.notice || 'Closed'
              : depth === 0
                ? 'Nobody waiting, one drink to make'
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

// Above this, ordering ahead is the better answer and the screen should say so.
const PROMOTE_MIN = 8

export function Home({ config, menu, hours, onOrder }) {
  const byName = new Map((menu?.items || []).map((item) => [item.name, item]))
  const featured = FEATURE.items.map((name) => byName.get(name)).filter(Boolean)
  const [head, tail] = nameLines(config ? config.cafe.name : '')
  const busy = menu?.wait_estimate_s != null && menu.wait_estimate_s / 60 >= PROMOTE_MIN

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
          reason this screen exists. Someone who reads twelve minutes at noon
          and comes back at twenty past has moved themselves out of the peak,
          and a number placed below the buttons is read after the decision it
          was meant to inform. */}
      <Wait menu={menu} hours={hours} />

      {/* Two doors. Ordering ahead is what the project is trying to find out
          about, so it gets equal weight, and above a busy line it gets more,
          because that is the moment it is worth the most. */}
      <div className={`doors${busy ? ' promote' : ''}`}>
        <div className="door">
          <button className="slab" onClick={() => onOrder('now')}>
            Order now
          </button>
          <span className="under">join the line</span>
        </div>
        <div className="door">
          <button className={`slab${busy ? ' filled' : ''}`} onClick={() => onOrder('ahead')}>
            Order ahead
          </button>
          <span className="under">pick a time, we'll queue it</span>
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
    </div>
  )
}
