// "Your order is ready", from the event stream that already says so.
//
// No push, no service worker, no keys: `GET /stream` broadcasts the state
// change and the page is already listening, so this is the browser's own
// Notification fired from an event we receive anyway. That also fixes its
// limit: it only works while a tab is open, which is the case that matters
// here: you are waiting for the drink.
//
// Permission is asked for at the moment it means something, when someone with
// an order in the queue taps the control, never on load.

const TOLD_KEY = 'cafe-flow.told.v1'

export const supported = () =>
  typeof window !== 'undefined' && 'Notification' in window

// 'granted' | 'denied' | 'default' | 'unsupported'
export const permission = () => (supported() ? Notification.permission : 'unsupported')

export async function ask() {
  if (!supported()) return 'unsupported'
  if (Notification.permission !== 'default') return Notification.permission
  try {
    return await Notification.requestPermission()
  } catch {
    return Notification.permission
  }
}

// Which orders have already been announced. Kept on the device so a reload
// while the drink sits on the shelf does not announce it a second time.
const told = () => {
  try {
    const raw = JSON.parse(localStorage.getItem(TOLD_KEY) || '[]')
    return new Set(Array.isArray(raw) ? raw : [])
  } catch {
    return new Set()
  }
}

const remember = (orderId) => {
  const all = told()
  all.add(orderId)
  try {
    localStorage.setItem(TOLD_KEY, JSON.stringify([...all].slice(-50)))
  } catch {
    /* private browsing: it just announces once per session instead */
  }
}

export function alreadyTold(orderId) {
  return told().has(orderId)
}

/** Announce one order, at most once. Returns whether anything was shown. */
export function announceReady(order, describe) {
  if (permission() !== 'granted') return false
  if (alreadyTold(order.order_id)) return false
  // Someone looking at the screen can already see it turn READY; the banner is
  // for the phone in a pocket.
  if (document.visibilityState === 'visible') return false

  remember(order.order_id)
  const note = new Notification(`#${order.number} is ready`, {
    body: `${describe(order)} On the shelf.`,
    tag: order.order_id,          // a second one replaces rather than stacks
    renotify: false,
  })
  note.onclick = () => {
    window.focus()
    note.close()
  }
  return true
}
