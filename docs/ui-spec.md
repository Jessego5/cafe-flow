# Order-ahead screen spec

Two new capabilities for the student view: the live wait, and a Maps-style
*order now / ready by* planner.

Everything marked **BUILT** is live behind the API described at the end and
needs only a front end. Everything marked **NEEDS BACKEND** is specified but
not implemented — build the screen against a stub and wire it later.

Endpoints and field names below were verified against the running app.

---

## What changes, in one paragraph

Today a customer opens the menu, adds items, and taps *Place order* with no idea
what they are joining. The cafe's constraint is a single till facing two sharp
spikes a day, so the highest-value thing the interface can do is not speed
anything up — it is tell people what the queue costs, and offer them a cheaper
moment. Every screen below exists for that.

---

## 1 · Menu header — the live wait — **BUILT**

A persistent strip at the top of the menu, visible before anything is added to
the cart. Poll `GET /menu` on load and every 30s, or read the same two fields
from `GET /queue`.

```
┌──────────────────────────────┐   ┌──────────────────────────────┐
│ quiet                        │   │ busy                         │
│ About 3 minutes              │   │ About 12 minutes             │
│ nobody waiting               │   │ 9 people ahead               │
│                              │   │ → try 12:45 instead          │
└──────────────────────────────┘   └──────────────────────────────┘
```

- `wait_estimate_s` is **seconds**. Round to whole minutes; never show seconds.
- `queue_depth` counts orders placed but not yet ready. A drink already on the
  shelf is not something to wait behind, so it is excluded — **do not add it
  back**.
- At `queue_depth: 0` show the wait for a single order, not "no wait". There is
  still a drink to make.
- Above a threshold you choose (12 minutes is a reasonable start), surface the
  planner inline. That is the moment it is worth the most.

---

## 2 · The planner — order now, or ready by — **BUILT**

The new screen, and the one worth the most care. Two directions, like a maps
app: *if I order now, when do I get it*, and *I want it at quarter past, when
should I order*. Same endpoint, same basket; the presence of `wanted_at`
chooses the direction.

```
┌────────────────────────────┐   ┌────────────────────────────────────┐
│ direction A — default      │   │ direction B — via the time picker  │
│                            │   │                                    │
│ Order now                  │   │ Ready by  [ 13:00 ▾ ]              │
│ Ready 11:19                │   │ Order at 12:45                     │
│ about 5 minutes            │   │ about 4 minutes of waiting         │
│                            │   │ 9 minutes less than ordering now   │
│ [ Place order ]            │   │ [ Remind me at 12:45 ]             │
│                            │   │ [ Order now instead ]              │
└────────────────────────────┘   └────────────────────────────────────┘
```

> **Show the saving, not just the time.** The number that persuades someone to
> come back later is the difference between the two directions. Call `/plan`
> twice — once with `wanted_at`, once without — and show the gap in `wait_s`.

### The basket matters

The quote is for the items in the cart, so request it **after** the cart is
assembled and re-request it whenever the cart changes. A bagel and two paninis
is a genuinely different quote from a drip coffee. `basket_s` and
`typical_basket_s` come back so you can say so if you want — *"this order is
bigger than most"*.

### States to build

| state | condition | what to show |
|---|---|---|
| achievable | `achievable: true` | Order time, ready time, and the saving against ordering now. |
| too soon | `achievable: false` | "The earliest we can do is *ready_at*." The response still carries a real quote for ordering immediately — offer that rather than an error. |
| order now | `order_in_s == 0` | No reminder to offer. The button is *Place order*. |
| reminder | `order_in_s > 0` | Offer "Remind me at *order_at*". Countdown optional. |
| closed | `wanted_at` past `closes_at` | Hours come from `GET /config`. Clamp the picker rather than letting someone ask for 8pm. |

### The time picker

- Range: now → `closes_at` from `GET /config`.
- Step in **15 minutes**. The forecast's own resolution is 15 minutes, so a
  picker offering 12:07 implies a precision the answer does not have.
- `wanted_at` is sent as `"HH:MM"`, local cafe time, 24-hour. Anything else
  returns 400.

---

## 3 · Notifications — **NEEDS BACKEND**

Two of them, and they are the reason the planner is worth building rather than
just displaying. Neither exists yet.

```
┌────────────────────────────────────┐   ┌────────────────────────────┐
│ fires at order_at                  │   │ fires on state → ready     │
│                                    │   │                            │
│ Time to order                      │   │ #42 is ready               │
│ Order now and it's ready by 13:00  │   │ Oat latte, bacon egg &     │
│ — about 4 minutes.                 │   │ cheese. On the shelf.      │
│ [ Open order ]                     │   │                            │
└────────────────────────────────────┘   └────────────────────────────┘
```

- **Ask permission at the moment it makes sense** — when someone taps
  *Remind me*, never on page load.
- The **ready** notification can ride the existing SSE stream while the page is
  open. The **order reminder** cannot: it fires minutes later with the tab
  closed. That one needs web push.
- On iOS, web push requires the page be installed to the home screen first.
  Design a fallback: if push is unavailable, show the reminder time and let the
  customer set their own alarm.
- Backend still to build: a service worker, VAPID keys, a subscription store,
  and a scheduler that fires at `order_at`. The front end should assume
  `POST /reminders` exists and takes the quote plus a push subscription.

---

## 4 · Bar queue — **BUILT**, minor

Already shows the queue and one tap per transition. Two additions, both already
in `GET /queue`:

- `wait_estimate_s` and `queue_depth` — the same number the customer is being
  shown. Staff should see what the app is promising on their behalf.
- `promised_at_s` on an order, where one was quoted. Sort or flag by it, so a
  promise about to be missed is visible *before* it is missed.
- The existing `batches` panel can stay, but it is no longer a priority: the
  station it optimises runs at about 16%.

## 5 · Pickup display — **no change**

Order numbers on a shelf. Nothing here needs touching.

---

## API reference

One origin serves the API and the views, so there is no CORS to configure. All
times are the cafe's local clock. `*_s` fields are seconds since local midnight
— divide by 3600 for the hour, or use the `HH:MM` twin returned alongside.

### `POST /plan` — direction A, order now

```json
{ "lines": [
    { "drink": "latte", "milk_type": "oat", "variant": "hot" },
    { "drink": "bacon_egg_cheese_bagel" }
] }
```

```json
{
  "order_at": "11:13",  "order_at_s": 40416.8,
  "ready_at": "11:19",  "ready_at_s": 40741.1,
  "wait_s": 324.3,      "order_in_s": 0.0,
  "achievable": true,   "wanted_at": null,
  "basket_s": 187.0,    "typical_basket_s": 71.7,
  "from_forecast": { "scenario": "…", "days": 12, "quantile": 80 }
}
```

### `POST /plan` — direction B, ready by

```json
{ "lines": [ … ], "wanted_at": "13:00" }
```

```
→ "order_at": "12:45", "ready_at": "12:49",
  "wait_s": 278.8, "order_in_s": 5483.2, "achievable": true
```

> **`from_forecast` is not decoration.** The quote comes from a forecast built
> over `days` simulated days and quoted at the `quantile`th percentile rather
> than the mean — a promise kept on average is missed half the time. If the
> interface ever claims certainty it does not have, that is where to look.

### Fields the new screens read

| endpoint | field | use |
|---|---|---|
| `GET /menu` | `wait_estimate_s` | Header strip. Seconds. |
| `GET /menu` | `queue_depth` | "N people ahead". |
| `GET /menu` | `items[].variants` | `["hot","iced"]` or empty. Show a toggle only when non-empty. |
| `GET /menu` | `items[].default_variant` | Pre-select this. |
| `GET /menu` | `milks` | Milk picker, only when `requires_milk`. |
| `GET /config` | `opens_at` / `closes_at` | Clamp the time picker. |
| `GET /queue` | `orders[].promised_at_s` | Bar view: flag a promise at risk. |
| `POST /orders` | `number` | What the pickup display shows. Not the order id. |

---

## Two rules that are easy to get wrong

> **Quote the later time, not the friendlier one.** The API already returns the
> 80th-percentile wait. Do not round it down to look better — a promise missed
> a third of the time is not one anyone relies on twice.

> **Never invent a time when `achievable` is false.** Say what the earliest
> actually is. The response carries a real quote for ordering now; use that.
