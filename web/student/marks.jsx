import React from 'react'
import { productImage } from './products.js'

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

// A real picture when there is one, the drawn mark when there is not. The
// fallback is not a placeholder for missing art so much as a promise: the board
// stays legible whether or not anyone has photographed this week's additions.
export function ItemMark({ item, variant = null }) {
  const art = productImage(item.name, variant)
  if (art) return <img className="art" src={art} alt="" loading="lazy" />
  const kitchen = (item.stations || []).some((s) => s === 'panini_press' || s === 'food_counter')
  return kitchen ? <PlateMark /> : <CupMark />
}
