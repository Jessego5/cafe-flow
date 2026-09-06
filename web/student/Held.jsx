import React from 'react'
import { money } from '../shared/api.js'
import { milkLabel, title } from './catalog.js'

// A paid order the app is holding back.
//
// No countdown. The release moment moves with the queue. That is the whole
// feature, so the screen says "usually around 12:45" and means it, rather
// than ticking down to a second it cannot promise.

const pretty = (clock) => {
  const [h, m] = String(clock).split(':').map(Number)
  const suffix = h < 12 ? 'am' : 'pm'
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${suffix}`
}

const lineText = (line) =>
  [line.variant ? title(line.variant) : null, title(line.drink)].filter(Boolean).join(' ') +
  (line.milk_type ? ` · ${milkLabel(line.milk_type)}` : '')

export function Held({ held, onCancel }) {
  return (
    <div className="held">
      <div className="head">
        <div>
          <span className="eyebrow">Held for</span>
          <div className="when">{pretty(held.wanted_at)}</div>
        </div>
        <span className="state">Paid</span>
      </div>

      <ul>
        {held.lines.map((line, i) => (
          <li key={i}>{lineText(line)}</li>
        ))}
      </ul>

      <p className="hint">
        We'll put it in when the counter is ready, usually around {pretty(held.expected_order_at)}.
      </p>

      <div className="foot">
        <span className="hint">{money(held.price_cents)} · payment stubbed</span>
        <button className="drop" onClick={() => onCancel(held.held_id)}>
          Cancel
        </button>
      </div>
    </div>
  )
}
