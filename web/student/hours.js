// Is the cafe open right now?
//
// The posted hours are the cafe's own, in the cafe's timezone, so the question
// is answered there and not in whatever timezone the phone happens to be in.
// `/config` gives both.

const toMinutes = (hhmm) => {
  const [h, m] = String(hhmm).split(':').map(Number)
  return h * 60 + (m || 0)
}

export function clockAt(timezone, at = new Date()) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(at)
  const get = (type) => Number(parts.find((part) => part.type === type)?.value)
  return (get('hour') % 24) * 60 + get('minute')
}

export function pretty(hhmm) {
  const total = toMinutes(hhmm)
  const hour = Math.floor(total / 60)
  const minute = String(total % 60).padStart(2, '0')
  const suffix = hour < 12 ? 'AM' : 'PM'
  return `${((hour + 11) % 12) + 1}:${minute} ${suffix}`
}

export function serviceHours(config, at = new Date()) {
  if (!config) return null
  const opens = toMinutes(config.opens_at)
  const closes = toMinutes(config.closes_at)
  const now = clockAt(config.cafe.timezone, at)
  const open = now >= opens && now < closes
  return {
    open,
    opens,
    closes,
    label: `${pretty(config.opens_at)} – ${pretty(config.closes_at)}`,
    // Closed says which way: before opening you can wait, after it you cannot.
    notice: open
      ? null
      : now < opens
        ? `Closed · opens ${pretty(config.opens_at)}`
        : `Closed for the day · opens ${pretty(config.opens_at)} tomorrow`,
  }
}
