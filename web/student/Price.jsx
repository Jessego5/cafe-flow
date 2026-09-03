import React from 'react'
import { money } from '../shared/api.js'

// A price the way a board writes one: the sign small, the number in the
// display face, digits tabular so a column of them lines up.
export function Price({ cents }) {
  const [dollars, cents_] = money(cents).slice(1).split('.')
  return (
    <span className="price">
      <span className="cur">$</span>
      {dollars}.{cents_}
    </span>
  )
}
