import React from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App.jsx'

// The app on its own page, filling the browser. `student.css` keeps its
// page-level rules behind this class, so the same App can be rendered inside
// something else — a phone frame, say — without seizing the document.
document.documentElement.classList.add('student-page')
document.body.classList.add('student-page')

createRoot(document.getElementById('root')).render(<App />)
