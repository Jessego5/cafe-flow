import React from 'react'
import './phone-frame.css'

// A phone, to put a screen inside.
//
// One wrapper: whatever you nest inside renders on the screen, interactive and
// scrollable, clipped to the screen's corners. Nothing about the device is an
// image — the bezel, the island, the side buttons and the status bar are all
// drawn — and every dimension derives from `--phone-width`, so `width` is the
// only knob needed to resize the whole thing.
//
//   <PhoneFrame>            <YourApp />           </PhoneFrame>
//   <PhoneFrame width={195} bezel="light" statusBar={false}> … </PhoneFrame>
//
// Content sized in em or % scales with the frame; content sized in rem is
// anchored to the document and will not.

// No default width lives here: `--phone-width` in the stylesheet is the one
// place the device's size is written down, and an omitted `width` leaves it
// alone. Pass a number for px, or any CSS length as a string.

export function PhoneFrame({
  children,
  width,
  bezel = 'dark',
  statusBar = true,
  island = true,
  time = '9:41',
  battery = 1,
  wifi = 3,                        // 3 full, 2 or 1 dims the outer arcs
  className = '',
  style,
  ...rest
}) {
  const classes = [
    'phone-frame',
    island ? '' : 'phone-frame--no-island',
    statusBar ? '' : 'phone-frame--bare',
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={classes}
      data-bezel={bezel}
      style={{
        ...(width == null ? null : { '--phone-width': typeof width === 'number' ? `${width}px` : width }),
        ...style,
      }}
      {...rest}
    >
      <div className="phone-frame__screen">
        {statusBar && (
          <div className="phone-frame__status" aria-hidden="true">
            <span>{time}</span>
            <span className="phone-frame__glyphs">
              <span className="phone-frame__signal">
                <i />
                <i />
                <i />
                <i />
              </span>
              {/* the wedge, then two arcs */}
              <span className="phone-frame__wifi" data-signal={wifi}>
                <i />
                <i />
                <i />
              </span>
              <span
                className="phone-frame__battery"
                style={{ '--battery-level': Math.min(1, Math.max(0, battery)) }}
              />
            </span>
          </div>
        )}
        <div className="phone-frame__content">{children}</div>
      </div>
    </div>
  )
}

export default PhoneFrame
