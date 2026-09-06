# Order-ahead screen spec

Two ways to buy a coffee, and the second one is the point of the project.

**Order now** is what the app already does. **Order ahead** lets someone scroll
to the time they want their drink, pay for it, and stop thinking about it — the
app holds the order and puts it in the queue itself, at the moment the live
queue says it will be ready on time.

Everything marked **BUILT** is live behind the API described at the end and
needs only a front end. **NEEDS BACKEND** is specified but not implemented;
build the screen against a stub.

Endpoints and field names below were exercised against the running app.

---

## Why the app places the order, and not you

An earlier draft of this had the app send a notification saying *time to order*.
That design is gone, and it is worth knowing why, because it is the difference
between the two buttons meaning anything.

A reminder has to commit to a forecast. Once somebody has been told to order,
they cannot be re-timed when the queue moves. Holding the order server-side
means the release decision happens with the live queue in hand.

Simulated over twelve days at half adoption, ordering a fixed hour ahead:

    fixed lead        median drink ready 59 minutes early, 99% over five
    adaptive release  median within a minute, 91% on time

The problem this solves is not waiting. It is a drink going cold on a shelf
with its owner still in a lecture.

It also removes the hardest thing from the build. There is no reminder push, so
no web push for it, no VAPID keys, no service worker, and no iOS home-screen
install standing between a customer and their coffee.

---

## 1 · Home — two buttons — **BUILT** (routing only)

```
┌──────────────────────────────┐
│  Morgridge Coffee            │
│  About 3 minutes             │
│  nobody waiting              │
│                              │
│  [   Order now            ]  │
│  [   Order ahead          ]  │
└──────────────────────────────┘
```

The live wait belongs above both buttons: it is the number that makes somebody
choose the second one. `wait_estimate_s` and `queue_depth` come from
`GET /menu`, refreshed every 30s.

- `wait_estimate_s` is **seconds**. Round to whole minutes, never show seconds.
- It is **`null` when the cafe is shut** — outside `opens_at`/`closes_at` there
  is nobody on the bar to quote for. Show the hours, not a zero. A quoted zero
  reads as *come now* to somebody at a locked door.
- `queue_depth` excludes drinks already on the shelf. Don't add them back.
- Above a threshold you pick — 8 minutes is reasonable — promote **Order
  ahead**. That is the moment it is worth the most.

---

## 2 · Order now — **BUILT**

Menu, cart, place. Unchanged. `POST /orders` returns a `number`, which is what
the pickup display shows — not the order id.

---

## 3 · Order ahead — the planner — **BUILT** (the quote) / **NEEDS BACKEND** (the hold)

Same menu and cart. The difference is the screen after the cart.

```
┌────────────────────────────────────┐
│ Ready by                           │
│                                    │
│        ▲                           │
│      12:45                         │
│      13:00   ←  scroll             │
│      13:15                         │
│        ▼                           │
│                                    │
│ We'll order it for you at 12:45.   │
│ About 4 minutes of making.         │
│ 9 minutes less than ordering now.  │
│                                    │
│ [  Pay and hold  ]                 │
│ [  Order now instead  ]            │
└────────────────────────────────────┘
```

> **Show the saving.** Call `/plan` twice — once with `wanted_at`, once
> without — and show the difference in `wait_s`. That gap is what makes someone
> choose a later time.

### The time picker

- Range: now → `closes_at` from `GET /config`.
- **Step in 15 minutes.** The forecast's own resolution is 15 minutes; a picker
  offering 12:07 implies a precision the answer does not have.
- Send `wanted_at` as `"HH:MM"`, 24-hour, cafe-local. Anything else is a 400.

### The quote is per basket

Request it **after** the cart is assembled and again whenever it changes. A
bagel and two paninis is a genuinely different quote from a drip coffee, and
food is not a rounding error — ringing up a sandwich costs 13 seconds more at
the till than a drink does, on top of being one more item. `basket_s` and
`typical_basket_s` come back so you can say *this order is bigger than most*.

### States

| state | condition | show |
|---|---|---|
| achievable | `achievable: true` | The time it will be ready, when the app will order it, and the saving against ordering now. |
| too soon | `achievable: false` | "The earliest we can do is *ready_at*." The response still carries a real quote for ordering immediately — offer that instead of an error. |
| order now | `order_in_s == 0` | Nothing to hold. The button is **Place order**. |
| hold | `order_in_s > 0` | **Pay and hold.** This is the new path. |
| closed | `wanted_at` past `closes_at` | Clamp the picker instead of letting someone ask for 8pm. |

### After paying — **NEEDS BACKEND**

```
┌────────────────────────────────────┐
│ Held for 13:00                     │
│ Paid · oat latte, bacon bagel      │
│                                    │
│ We'll put it in when the counter   │
│ is ready — usually around 12:45.   │
│                                    │
│ [  Cancel  ]                       │
└────────────────────────────────────┘
```

- **Do not show a countdown to a fixed time.** The release moment moves with the
  queue; that is the whole feature. "Usually around 12:45" is honest, "12:45:00"
  is not.
- **Cancel is available until release.** After that it is a live order and the
  existing rules apply.
- The state changes to a normal order when released — same `order_id`, so the
  ticket screen can just start working.

---

## 4 · Notifications — **NEEDS BACKEND**

**One push, and it fires when the whole order is ready.**

```
┌────────────────────────────┐
│ #42 is ready               │
│ Oat latte, bacon bagel.    │
│ On the shelf.              │
└────────────────────────────┘
```

Items reach the shelf as they finish — a drink is poured while its sandwich is
still in the oven, and the event stream carries an `item_ready` for each. **Do
not notify on those.** A notification that walks somebody over for half their
order is worse than none. Median gap between first and last item on a
multi-item order is 30 seconds; only 1.4% of orders have anything sitting more
than a minute.

`item_ready` is for the bar screen, which does want to see a half-finished
ticket. The customer only wants to know when to stand up.

Ask permission at the moment it makes sense — when someone pays for a held
order — never on page load. While the tab is open this can ride the existing
SSE stream with no backend at all.

---

## 5 · Bar queue — **BUILT**, minor additions

- `wait_estimate_s` and `queue_depth` from `GET /queue` — staff should see the
  number the app is promising on their behalf.
- `promised_at_s` on an order where one was quoted. Flag a promise about to be
  missed *before* it is missed.
- Held orders are not on the bar screen. They are not orders yet.

## 6 · Pickup display — **no change**

Order numbers on a shelf.

---

## API reference

One origin serves the API and the views, so there is no CORS. All times are the
cafe's local clock; `*_s` fields are seconds since local midnight, paired with
an `HH:MM` twin.

### `POST /plan` — **BUILT**

```json
{ "lines": [{ "drink": "latte", "milk_type": "oat", "variant": "hot" }],
  "wanted_at": "13:00" }
```

```json
{ "order_at": "12:45", "order_at_s": 45900.0,
  "ready_at": "12:49", "ready_at_s": 46140.0,
  "wait_s": 191.3, "order_in_s": 5483.2,
  "achievable": true,  "wanted_at": "13:00",
  "basket_s": 112.0,   "typical_basket_s": 71.7,
  "from_forecast": { "scenario": "ground_truth_observed", "days": 24, "quantile": 80 } }
```

Omit `wanted_at` for the other direction — *if I order now, when is it ready*.

> **`from_forecast` is not decoration.** The quote comes from `days` simulated
> days at the `quantile`th percentile, not the mean: a promise kept on average
> is missed half the time.

### `POST /held` — **NEEDS BACKEND**

Same body as `/plan` plus payment. Returns a `held_id`, the `order_at` the
server currently expects, and the `wanted_at` it is holding to. The server
re-derives the timing itself — never accept an `order_at` from the client, for
the same reason `POST /orders` does not accept a quoted time.

### `DELETE /held/{id}` — **NEEDS BACKEND**

Idempotent. Cancelling an already-released or unknown hold is a 204.

### Fields the screens read

| endpoint | field | use |
|---|---|---|
| `GET /menu` | `wait_estimate_s` | Home strip. Seconds, or `null` when shut. |
| `GET /menu` | `queue_depth` | "N people ahead". |
| `GET /menu` | `items[].variants` | Show a hot/iced toggle only when non-empty. |
| `GET /menu` | `items[].default_variant` | Pre-select. |
| `GET /menu` | `milks` | Milk picker, only when `requires_milk`. |
| `GET /config` | `opens_at` / `closes_at` | Clamp the picker; decide "shut". |
| `GET /queue` | `orders[].promised_at_s` | Bar: flag a promise at risk. |
| `GET /stream` | `state_change` | Ready notification, order-level only. |
| `POST /orders` | `number` | What the pickup display shows. |

---

## Three rules that are easy to get wrong

> **Quote the later time.** The API returns the 80th percentile. Do not round it
> down to look friendlier — a promise missed a third of the time is not one
> anyone relies on twice.

> **Never invent a time when `achievable` is false.** Say what the earliest
> actually is; the response carries a real quote for ordering now.

> **Never show a held order counting down to an exact second.** The release
> moment moves with the queue. Saying "around 12:45" is the honest version of a
> feature whose whole value is that it re-decides.
