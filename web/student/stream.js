// One event-stream connection per phone.
//
// Two panels on this screen care about live state — the wait estimate and this
// phone's own orders — and a cafe full of customers each holding two open
// streams is fan-out the server does not need to do. So the page opens one
// connection and hands every subscriber the same signal.
//
// The signal is not the state. Subscribers refetch their own endpoint when it
// fires, including on reconnect, which is what keeps a dropped connection from
// leaving a quietly wrong screen behind.

const EVENTS = ['state_change', 'station_start', 'station_end', 'batch_formed']

let source = null
const subscribers = new Set()

const fire = (reason) => subscribers.forEach((handler) => handler(reason))

function open() {
  source = new EventSource('/stream')
  source.onopen = () => fire('open')
  EVENTS.forEach((type) => source.addEventListener(type, () => fire(type)))
}

export function onLive(handler) {
  subscribers.add(handler)
  if (!source) open()
  return () => {
    subscribers.delete(handler)
    if (subscribers.size === 0 && source) {
      source.close()
      source = null
    }
  }
}
