import React, { useEffect, useMemo, useRef, useState } from 'react'
import { badges, blurb, milkLabel, milkOrder, sections, title } from './catalog.js'
import { Planner } from './Planner.jsx'
import { Price } from './Price.jsx'
import { ItemMark } from './marks.jsx'

const minutes = (seconds) => Math.max(1, Math.round(seconds / 60))

// Where the wait is worth planning around rather than just reading.
const BUSY_MIN = 12

/* ------------------------------------------------------------------ options */

// One item, opened. Everything the API says is configurable about it is here
// and nothing else: the board's hot-or-iced, and the milk if it takes milk.
function ItemSheet({ item, onClose, onAdd, milks: offered }) {
  const milks = milkOrder(offered)
  const variants = item.variants || []
  const [variant, setVariant] = useState(item.default_variant || variants[0] || null)
  const [milk, setMilk] = useState(item.requires_milk ? milks[0] : null)
  const [qty, setQty] = useState(1)

  return (
    <>
      <button className="scrim" aria-label="Close" onClick={onClose} />
      <div className="sheet" role="dialog" aria-label={title(item.name)}>
        <div className="top">
          <div className="shot">
            <ItemMark item={item} variant={variant} />
          </div>
          <div>
            <h3>{title(item.name)}</h3>
            <div className="badges" style={{ display: 'flex', gap: '0.3rem', flexWrap: 'wrap', marginTop: '0.4rem' }}>
              {badges(item).map((badge) => (
                <span key={badge} className="badge">{badge}</span>
              ))}
            </div>
          </div>
          <button className="close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="body">
          <p className="hint" style={{ marginTop: '0.6rem' }}>{blurb(item.name)}</p>

          {variants.length > 0 && (
            <div className="field">
              <span className="eyebrow">Hot or iced</span>
              <div className="choices">
                {variants.map((option) => (
                  <button
                    key={option}
                    className={option === variant ? 'on' : ''}
                    onClick={() => setVariant(option)}
                  >
                    {title(option)}
                  </button>
                ))}
              </div>
            </div>
          )}

          {item.requires_milk && (
            <div className="field">
              <span className="eyebrow">Milk</span>
              <div className="choices">
                {milks.map((option) => (
                  <button
                    key={option}
                    className={option === milk ? 'on' : ''}
                    onClick={() => setMilk(option)}
                  >
                    {milkLabel(option)}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="field">
            <span className="eyebrow">How many</span>
            <div className="stepper">
              <button onClick={() => setQty((n) => Math.max(1, n - 1))} aria-label="One fewer">−</button>
              <span className="n">{qty}</span>
              <button onClick={() => setQty((n) => Math.min(9, n + 1))} aria-label="One more">+</button>
            </div>
          </div>

          {item.calories != null && (
            <p className="hint" style={{ marginTop: '1.2rem' }}>
              The board's figure: {item.calories} cal, medium
              {item.requires_milk ? ', calculated with 2% milk' : ''}
            </p>
          )}

          {item.service_s != null && (
            <p className="hint" style={{ marginTop: item.calories != null ? '0.3rem' : '1.2rem' }}>
              About {Math.round(item.service_s)}s of hands-on work at the bar
              {item.stations?.length ? ` · ${item.stations.map((s) => s.replace(/_/g, ' ')).join(' → ')}` : ''}
            </p>
          )}
        </div>

        <div className="foot">
          <Price cents={item.price_cents * qty} />
          <button
            className="slab filled"
            onClick={() => {
              onAdd(
                Array.from({ length: qty }, () => ({
                  drink: item.name,
                  milk_type: item.requires_milk ? milk : null,
                  variant: variants.length > 0 ? variant : null,
                })),
              )
              onClose()
            }}
          >
            Add to order
          </button>
        </div>
      </div>
    </>
  )
}

/* --------------------------------------------------------------------- cart */

const lineKey = (line) => `${line.drink}|${line.variant || ''}|${line.milk_type || ''}`

// Identical lines are one row with a count; the API still receives them as the
// separate items they are.
function grouped(cart) {
  const rows = new Map()
  for (const line of cart) {
    const key = lineKey(line)
    const row = rows.get(key) || { key, line, count: 0 }
    row.count += 1
    rows.set(key, row)
  }
  return [...rows.values()]
}

function CartSheet({ cart, menu, total, config, nowMinutes, mode, onClose, onRemove, onPlace, onHold, placing, error }) {
  const byName = new Map(menu.items.map((item) => [item.name, item]))
  return (
    <>
      <button className="scrim" aria-label="Close" onClick={onClose} />
      <div className="sheet" role="dialog" aria-label="Your order">
        <div className="top">
          <h3>Your order</h3>
          <button className="close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="body">
          <div className="lines">
            {grouped(cart).map(({ key, line, count }) => {
              const item = byName.get(line.drink)
              return (
                <div className="line" key={key}>
                  <div>
                    <div>
                      {count > 1 ? `${count} × ` : ''}
                      {line.variant ? `${title(line.variant)} ` : ''}
                      {title(line.drink)}
                    </div>
                    {line.milk_type && <div className="opts">{milkLabel(line.milk_type)}</div>}
                  </div>
                  <Price cents={(item?.price_cents || 0) * count} />
                  <button className="drop" onClick={() => onRemove(key)}>remove</button>
                </div>
              )
            })}
          </div>

          {mode === 'ahead' ? (
            <Planner
              lines={cart}
              config={config}
              nowMinutes={nowMinutes}
              onHold={onHold}
              onOrderNow={onPlace}
              placing={placing}
              error={error}
            />
          ) : (
            <>
              {error && <p className="strike">{error}</p>}
              <button
                className="slab filled wide"
                style={{ marginTop: '1.2rem' }}
                disabled={placing}
                onClick={onPlace}
              >
                {placing ? 'Placing…' : 'Place order'}
              </button>
            </>
          )}

          <p className="hint" style={{ marginTop: '1.2rem' }}>
            Payment is stubbed in this demo. The board price is shown here; what the bar records
            is whatever the server prices the order at.
          </p>
        </div>

        <div className="foot">
          <Price cents={total} />
          <span className="hint">{cart.length} item{cart.length === 1 ? '' : 's'}</span>
        </div>
      </div>
    </>
  )
}

/* ----------------------------------------------------------------- the menu */

export function OrderTab({
  config,
  menu,
  hours,
  cart,
  mode,
  onMode,
  onHold,
  onAdd,
  onRemove,
  onPlace,
  placing,
  error,
}) {
  const groups = useMemo(() => (menu ? sections(menu.items) : []), [menu])
  const [active, setActive] = useState(null)
  const [opened, setOpened] = useState(null)
  const [showCart, setShowCart] = useState(false)
  const listRef = useRef(null)
  const dockRef = useRef(null)

  // The rail follows the scroll rather than only driving it, so the two never
  // disagree about which section you are looking at.
  useEffect(() => {
    const list = listRef.current
    if (!list || groups.length === 0) return undefined
    const root = list.closest('.pane')
    const seen = new Set()
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) seen.add(entry.target.dataset.key)
          else seen.delete(entry.target.dataset.key)
        }
        const first = groups.find((group) => seen.has(group.key))
        if (first) setActive(first.key)
      },
      { root, rootMargin: '-45% 0px -50% 0px', threshold: 0 },
    )
    list.querySelectorAll('section[data-key]').forEach((node) => observer.observe(node))
    return () => observer.disconnect()
  }, [groups])

  // The dock grows a second line once there is a cart to review, so the list
  // is told how much room to leave rather than guessing at a constant and
  // hiding the last item behind it.
  useEffect(() => {
    const dock = dockRef.current
    const shell = dock?.closest('.phone')
    if (!dock || !shell) return undefined
    const sync = () => shell.style.setProperty('--dock-h', `${dock.offsetHeight}px`)
    sync()
    const observer = new ResizeObserver(sync)
    observer.observe(dock)
    return () => {
      observer.disconnect()
      shell.style.removeProperty('--dock-h')
    }
  }, [])

  const byName = new Map((menu?.items || []).map((item) => [item.name, item]))
  const total = cart.reduce((sum, line) => sum + (byName.get(line.drink)?.price_cents || 0), 0)
  const current = active || groups[0]?.key

  const jump = (key) => {
    setActive(key)
    document.getElementById(`sec-${key}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <>
      <div className="shop-head">
        <div className="modes">
          <button className={mode === 'now' ? 'on' : ''} onClick={() => onMode('now')}>
            Order now
          </button>
          <span className="rule" />
          <button className={mode === 'ahead' ? 'on' : ''} onClick={() => onMode('ahead')}>
            Order ahead
          </button>
        </div>
        <div className="store">
          <span className="name">{config ? config.cafe.name : 'Cafe'}</span>
          <span className="chev">›</span>
        </div>
        <div className="sub">{hours ? `Today ${hours.label}` : ''}</div>

        {/* The live wait, at the top of the menu and before anything is in the
            cart. It is the one number that lets someone choose a cheaper moment. An
            empty queue still has a drink to make, so it quotes one order
            rather than claiming no wait at all. */}
        {menu && (
          <div className="strip">
            {menu.wait_estimate_s == null ? (
              <span>Closed. The board is still here</span>
            ) : (
              <>
                <strong>About {minutes(menu.wait_estimate_s || menu.seconds_per_order)} min</strong>
                <span>
                  {menu.queue_depth === 0
                    ? 'nobody waiting'
                    : `${menu.queue_depth} ahead of you`}
                </span>
                {menu.wait_estimate_s / 60 >= BUSY_MIN && cart.length > 0 && (
                  <button className="plan-link" onClick={() => setShowCart(true)}>
                    plan a time →
                  </button>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {!menu ? (
        <p className="empty">Loading the board…</p>
      ) : (
        <div className="board">
          <nav className="rail">
            {groups.map((group) => (
              <button
                key={group.key}
                className={group.key === current ? 'on' : ''}
                onClick={() => jump(group.key)}
              >
                {group.rail || group.label}
              </button>
            ))}
          </nav>

          <div className="list" ref={listRef}>
            {groups.map((group) => (
              <section key={group.key} id={`sec-${group.key}`} data-key={group.key}>
                <h2>
                  {group.heading}
                  <span className="pill">{group.items.length}</span>
                </h2>
                {group.note && <p className="note">{group.note}</p>}
                {group.items.map((item) => (
                  /* Sold out is shown, not hidden. Somebody who came for a
                     bagel needs to know the cafe is out of them, not to wonder
                     whether they misremembered the menu. */
                  <button
                    className={item.available === false ? 'item out' : 'item'}
                    key={`${group.key}-${item.name}`}
                    disabled={item.available === false}
                    onClick={() => setOpened(item)}
                  >
                    <div className="thumb">
                      <ItemMark item={item} />
                    </div>
                    <div>
                      <div className="name">{title(item.name)}</div>
                      <div className="badges">
                        {item.available === false && <span className="badge out">Sold out</span>}
                        {badges(item).slice(0, item.available === false ? 1 : 2).map((badge) => (
                          <span key={badge} className="badge">{badge}</span>
                        ))}
                      </div>
                      <p className="blurb">{blurb(item.name)}</p>
                      <div className="foot">
                        <Price cents={item.price_cents} />
                        <span className="add" aria-hidden="true" />
                      </div>
                    </div>
                  </button>
                ))}
              </section>
            ))}
          </div>
        </div>
      )}

      <div className="dock" ref={dockRef}>
        {cart.length > 0 ? (
          <>
            <button className="cart" onClick={() => setShowCart(true)}>
              <span className="count">{cart.length}</span>
              <span className="total">
                <Price cents={total} />
                <span className="hint" style={{ display: 'block' }}>
                  {mode === 'ahead' ? 'pick a time next' : `${cart.length} item${cart.length === 1 ? '' : 's'}`}
                </span>
              </span>
              <span className="go">Review</span>
            </button>
          </>
        ) : hours && !hours.open ? (
          <div className="closed">
            <span>{hours.notice}</span>
            <span className="chev">˄</span>
          </div>
        ) : null}
      </div>

      {opened && (
        <ItemSheet
          item={opened}
          milks={menu.milks}
          onClose={() => setOpened(null)}
          onAdd={onAdd}
        />
      )}

      {showCart && cart.length > 0 && (
        <CartSheet
          cart={cart}
          menu={menu}
          total={total}
          config={config}
          nowMinutes={hours?.now ?? 0}
          mode={mode}
          onClose={() => setShowCart(false)}
          onRemove={onRemove}
          onPlace={() => onPlace(() => setShowCart(false))}
          onHold={(quote) => { setShowCart(false); onHold(quote) }}
          placing={placing}
          error={error}
        />
      )}
    </>
  )
}

export { lineKey }
