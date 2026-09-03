import React from 'react'

// Line art, drawn rather than photographed.
//
// The board has no product photography and this app is not going to invent
// any, so the thumbnails carry a drawn mark instead: a cup for the drinks, a
// plate for the kitchen. It reads as a placeholder on purpose — nobody should
// mistake a sketch for a picture of what they are about to be handed.

const INK = '#1a1917'

export function CupMark({ soft = '#e6ded0' }) {
  return (
    <svg className="mark" viewBox="0 0 48 48" aria-hidden="true">
      <path d="M15 15h18l-2.2 20.5a3 3 0 0 1-3 2.7h-7.6a3 3 0 0 1-3-2.7Z" fill={soft} />
      <path
        d="M15 15h18l-2.2 20.5a3 3 0 0 1-3 2.7h-7.6a3 3 0 0 1-3-2.7Z M15 21h18"
        fill="none"
        stroke={INK}
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
      {/* the little drinker: the reference's mascot, redrawn in two strokes */}
      <circle cx="24" cy="27" r="2.6" fill={INK} />
      <path d="M21.4 33.5c0-1.9 1.2-3.2 2.6-3.2s2.6 1.3 2.6 3.2" fill="none" stroke={INK} strokeWidth="1.1" />
      <path d="M20 12.5c1.6-2.2 6.4-2.2 8 0" fill="none" stroke={INK} strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  )
}

export function PlateMark({ soft = '#e6ded0' }) {
  return (
    <svg className="mark" viewBox="0 0 48 48" aria-hidden="true">
      <path d="M11 26h26l-2 6.5a4 4 0 0 1-3.8 2.8H16.8A4 4 0 0 1 13 32.5Z" fill={soft} />
      <path
        d="M11 26h26l-2 6.5a4 4 0 0 1-3.8 2.8H16.8A4 4 0 0 1 13 32.5Z"
        fill="none"
        stroke={INK}
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
      <path d="M14 22.5c2-4.5 6-6.5 10-6.5s8 2 10 6.5" fill="none" stroke={INK} strokeWidth="1.1" strokeLinecap="round" />
      <path d="M19 22.5c1-2.2 3-3.2 5-3.2s4 1 5 3.2" fill="none" stroke={INK} strokeWidth="1.1" strokeLinecap="round" />
    </svg>
  )
}

export function ItemMark({ item }) {
  const kitchen = (item.stations || []).some((s) => s === 'panini_press' || s === 'food_counter')
  return kitchen ? <PlateMark /> : <CupMark />
}

// The seasonal hero: a citrus sprig and a glass of soda, which is what the
// featured build actually is.
export function SeasonalArt() {
  return (
    <svg viewBox="0 0 320 200" role="img" aria-label="A citrus sprig beside a glass of sparkling cold brew">
      <path
        d="M243 18c-6 14-14 26-24 36-9 9-19 16-28 21"
        fill="none"
        stroke="#8a7b63"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
      <path d="M219 54c-7 3-14 4-21 4" fill="none" stroke="#8a7b63" strokeWidth="1.4" strokeLinecap="round" />
      <circle cx="243" cy="83" r="31" fill="#f2c3a6" />
      <circle cx="243" cy="83" r="31" fill="none" stroke="#c98f68" strokeWidth="1.1" />
      <circle cx="196" cy="66" r="25" fill="#eebd9f" />
      <circle cx="196" cy="66" r="25" fill="none" stroke="#c98f68" strokeWidth="1.1" />
      {/* the cut half, segments showing */}
      <circle cx="214" cy="136" r="28" fill="#fdf6ee" />
      <circle cx="214" cy="136" r="28" fill="none" stroke="#c98f68" strokeWidth="1.1" />
      <circle cx="214" cy="136" r="21" fill="#f6d9c6" />
      {[0, 45, 90, 135].map((deg) => (
        <line
          key={deg}
          x1={214 - 21 * Math.cos((deg * Math.PI) / 180)}
          y1={136 - 21 * Math.sin((deg * Math.PI) / 180)}
          x2={214 + 21 * Math.cos((deg * Math.PI) / 180)}
          y2={136 + 21 * Math.sin((deg * Math.PI) / 180)}
          stroke="#e0b294"
          strokeWidth="1"
        />
      ))}
      {/* a tall glass, bubbles rising */}
      <path d="M64 62h44l-5 76a9 9 0 0 1-9 8.4H78a9 9 0 0 1-9-8.4Z" fill="#efe8dc" />
      <path
        d="M64 62h44l-5 76a9 9 0 0 1-9 8.4H78a9 9 0 0 1-9-8.4Z"
        fill="none"
        stroke={INK}
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M66 84h40" fill="none" stroke={INK} strokeWidth="1.2" />
      <path d="M99 46l-4 16" fill="none" stroke={INK} strokeWidth="1.4" strokeLinecap="round" />
      {[
        [78, 100, 3],
        [92, 112, 2.2],
        [83, 124, 2.6],
        [95, 134, 1.8],
      ].map(([cx, cy, r]) => (
        <circle key={`${cx}-${cy}`} cx={cx} cy={cy} r={r} fill="none" stroke={INK} strokeWidth="1" />
      ))}
    </svg>
  )
}
