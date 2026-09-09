import React, { useEffect, useMemo, useRef, useState } from 'react'
import { plan } from '../shared/api.js'

// Order now, or ready by: the two directions, and the difference between them.
//
// The number that moves someone to come back later is not the wait; it is the
// saving against ordering now. So both directions are quoted for the same
// basket and the gap is what the screen leads with. Every figure here is the
// forecast's 80th percentile, quoted as it comes: rounding it down to look
// friendlier would make a promise that misses a third of the time.

const STEP_MIN = 15          // the forecast's own resolution; a finer picker would imply precision it lacks
const MIN = 60

const minutes = (seconds) => Math.max(1, Math.round(seconds / MIN))

const hhmm = (totalMinutes) => {
  const h = Math.floor(totalMinutes / 60) % 24
  const m = totalMinutes % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}

const pretty = (clock) => {
  const [h, m] = clock.split(':').map(Number)
  const suffix = h < 12 ? 'am' : 'pm'
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${suffix}`
}

// Every quarter hour the cafe is still open for, starting after the current one.
function slots(config, nowMinutes) {
  if (!config) return []
  const toMin = (s) => {
    const [h, m] = s.split(':').map(Number)
    return h * 60 + m
  }
  const close = toMin(config.closes_at)
  const first = Math.ceil((nowMinutes + 1) / STEP_MIN) * STEP_MIN
  const out = []
  for (let t = first; t <= close; t += STEP_MIN) out.push(hhmm(t))
  return out
}


// Every quarter hour left in the day, scrolled through rather than dropped
// down. The list runs to closing, so on a morning there are dozens of them,
// a dropdown made that look like a handful of choices.
function TimeScroll({ options, value, onChange }) {
  const box = useRef(null)
  const settling = useRef(null)

  // Centre the chosen row once the list exists. Not on every change: while
  // somebody is scrolling, the scroll is the input and moving it would fight
  // them.
  useEffect(() => {
    const el = box.current
    if (!el || !value) return
    const row = el.querySelector(`[data-t="${value}"]`)
    if (row) el.scrollTop = row.offsetTop - (el.clientHeight - row.clientHeight) / 2
  }, [options.length])

  const settle = () => {
    clearTimeout(settling.current)
    settling.current = setTimeout(() => {
      const el = box.current
      if (!el) return
      const middle = el.scrollTop + el.clientHeight / 2
      let closest = null
      let best = Infinity
      for (const row of el.querySelectorAll('[data-t]')) {
        const distance = Math.abs(row.offsetTop + row.clientHeight / 2 - middle)
        if (distance < best) {
          best = distance
          closest = row.dataset.t
        }
      }
      if (closest && closest !== value) onChange(closest)
    }, 110)
  }

  return (
    <div className="picker">
      <div className="timescroll" ref={box} onScroll={settle}>
        <div className="pad" />
        {options.map((t) => (
          <button
            key={t}
            data-t={t}
            className={t === value ? 'on' : ''}
            onClick={() => onChange(t)}
          >
            {pretty(t)}
          </button>
        ))}
        <div className="pad" />
      </div>
    </div>
  )
}

export function Planner({ lines, config, nowMinutes, onHold, onOrderNow, placing, error }) {
  const options = useMemo(() => slots(config, nowMinutes), [config, nowMinutes])
  // Open on a time far enough out to be worth planning for, so the screen
  // arrives with a quote rather than an empty picker.
  const [wantedAt, setWantedAt] = useState(() => options[1] || options[0] || null)
  const [now, setNow] = useState(null)          // ordering immediately, for the saving
  const [later, setLater] = useState(null)      // the chosen time
  const [failed, setFailed] = useState(null)

  const basket = useMemo(() => JSON.stringify(lines), [lines])

  useEffect(() => {
    if (lines.length === 0) return undefined
    let alive = true
    setFailed(null)
    Promise.all([plan(lines), wantedAt ? plan(lines, wantedAt) : Promise.resolve(null)])
      .then(([a, b]) => {
        if (!alive) return
        setNow(a)
        setLater(b)
      })
      .catch((err) => alive && setFailed(err.message))
    return () => {
      alive = false
    }
  }, [basket, wantedAt, lines])

  const quote = later || now
  const saving = now && later ? now.wait_s - later.wait_s : null
  const holdable = later && later.achievable && later.order_in_s > 0

  return (
    <div className="planner">
      <div className="field">
        <span className="eyebrow">Ready by</span>
        {options.length === 0 ? (
          <p className="hint">
            The cafe closes at {config ? pretty(config.closes_at) : 'closing'}, so there is nothing left to
            plan for today.
          </p>
        ) : (
          <TimeScroll options={options} value={wantedAt} onChange={setWantedAt} />
        )}
      </div>

      {failed && <p className="strike">{failed}</p>}

      {quote && (
        <div className="quote">
          {/* Never invent a time: say what the earliest actually is, and offer
              the real quote for ordering now instead of an error. */}
          {!quote.achievable ? (
            <p className="hint amber">
              The earliest we can do is {pretty(quote.ready_at)}. Ordering now gets it to you then.
            </p>
          ) : (
            <>
              <p className="lead">
                {holdable
                  ? `We'll order it for you at ${pretty(quote.order_at)}.`
                  : 'That is as soon as we can start it.'}
              </p>
              <div className="line">
                <span className="k">Making</span>
                <span className="v">about {minutes(quote.wait_s)} min</span>
              </div>
            </>
          )}

          {saving != null && saving > MIN && (
            <p className="saving">{minutes(saving)} minutes less waiting than ordering now</p>
          )}
          {saving != null && saving <= MIN && quote.achievable && (
            <p className="hint">No quicker than ordering now.</p>
          )}

          <p className="hint provenance-note">
            An {quote.from_forecast.quantile}th-percentile figure from {quote.from_forecast.days}{' '}
            simulated days, for this basket
            {quote.basket_s > quote.typical_basket_s * 1.3 ? ', bigger than most' : ''}.
          </p>
        </div>
      )}

      {error && <p className="strike">{error}</p>}

      <button
        className="slab filled wide"
        disabled={placing || !holdable}
        onClick={() => onHold(later)}
      >
        {placing ? 'Holding…' : 'Pay and hold'}
      </button>
      <button className="slab wide" style={{ marginTop: '0.5rem' }} disabled={placing} onClick={onOrderNow}>
        Order now instead
      </button>
    </div>
  )
}
