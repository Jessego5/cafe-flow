import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from '../student/App.jsx'
import { PhoneFrame } from '../shared/PhoneFrame.jsx'
import './demo.css'

// The cafe app, in the device it was designed for.
//
// This page exists to look at the frame, so the frame's options are the
// controls; what is inside it is the real student view, talking to the real
// API. Served from the app's own origin (`/frame`), so `/menu` and `/stream`
// resolve exactly as they do at `/`.

// The screen's own colour, matched to the app's paper so the status bar has no
// seam under it.
const SCREEN = { '--screen-bg': '#f7f5f1' }

function Demo() {
  const [bezel, setBezel] = useState('dark')
  const [statusBar, setStatusBar] = useState(true)
  const [width, setWidth] = useState(390)

  return (
    <div className="gallery">
      <h1>Phone frame</h1>
      <p className="lede">
        One wrapper around arbitrary content: here, the whole cafe app, live. The bezel, island,
        side buttons and status bar are drawn in CSS, and every dimension derives from{' '}
        <code>--phone-width</code>: drag it and the device resizes as one piece.
      </p>

      <div className="controls">
        <button className={bezel === 'dark' ? 'on' : ''} onClick={() => setBezel('dark')}>
          Dark bezel
        </button>
        <button className={bezel === 'light' ? 'on' : ''} onClick={() => setBezel('light')}>
          Light bezel
        </button>
        <button className={statusBar ? 'on' : ''} onClick={() => setStatusBar((s) => !s)}>
          Status bar {statusBar ? 'on' : 'off'}
        </button>
        <span className="spacer" />
        <label>
          --phone-width
          <input
            type="range"
            min="240"
            max="480"
            value={width}
            onChange={(e) => setWidth(Number(e.target.value))}
          />
          {width}px
        </label>
      </div>

      <div className="rack">
        <figure>
          <PhoneFrame width={width} bezel={bezel} statusBar={statusBar} style={SCREEN}>
            <App />
          </PhoneFrame>
          <figcaption>--phone-width: {width}px</figcaption>
        </figure>
      </div>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<Demo />)
