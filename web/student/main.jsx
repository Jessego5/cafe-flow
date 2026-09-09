import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App.jsx'
import { PhoneFrame } from '../shared/PhoneFrame.jsx'
import './stage.css'

// The app is a phone app, so on a phone it fills the phone. On anything wider
// it is shown in the device it was designed for, rather than stretched across
// a desktop window it will never actually run in.
//
// The switch is a media query rather than a user-agent guess: a narrowed
// browser window gets the bare app for the same reason a phone does: there is
// no room for a frame, and drawing one would only shrink the screen.
const FRAMED = '(min-width: 34rem) and (min-height: 34rem)'

function useFramed() {
  const [framed, setFramed] = useState(() => window.matchMedia(FRAMED).matches)
  useEffect(() => {
    const query = window.matchMedia(FRAMED)
    const sync = (event) => setFramed(event.matches)
    query.addEventListener('change', sync)
    return () => query.removeEventListener('change', sync)
  }, [])
  return framed
}

function Root() {
  const framed = useFramed()

  // `student.css` keeps its page-level rules behind `.student-page` so the app
  // can be rendered inside something else. Framed, the stage owns the page.
  useEffect(() => {
    const on = framed ? 'student-stage' : 'student-page'
    const off = framed ? 'student-page' : 'student-stage'
    for (const node of [document.documentElement, document.body]) {
      node.classList.add(on)
      node.classList.remove(off)
    }
  }, [framed])

  if (!framed) return <App />
  return (
    <div className="stage">
      <PhoneFrame>
        <App />
      </PhoneFrame>
    </div>
  )
}

createRoot(document.getElementById('root')).render(<Root />)
