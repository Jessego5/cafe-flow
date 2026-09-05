# Notifications — backend spec

Section 3 of [ui-spec.md](ui-spec.md), written out for whoever builds it. The
front end is done and shipping the fallback: the planner states the order time
plainly and tells the customer to set their own alarm.

Two notifications were specified. They need very different things, and only one
of them needs a backend at all.

| | fires | needs |
|---|---|---|
| **#42 is ready** | on the *order* reaching `ready` | nothing new — the SSE stream already carries it |
| **Time to order** | at `order_at`, minutes later, tab closed | VAPID keys, a subscription store, a scheduler, and a push library |

Build them in that order. The first is an afternoon and can ship on its own.

---

## 1 · Ready — no backend

`GET /stream` already broadcasts `state_change`, and the student view is
already subscribed (`web/student/stream.js`). A `Notification` fired from that
existing event is the whole feature. It only works while a tab is open, which
covers the case that matters: you are waiting for the drink.

**Fire on the order, not on an item.** Items reach the shelf as they finish -- a
drink is poured while its sandwich is still in the oven -- and the simulator
emits an `item_ready` event for each. Do not notify on those. A notification
that walks somebody over for half their order is worse than no notification.

The wait this costs is small enough to be sure about. Across twelve simulated
days, 11% of orders have more than one item, and on those the gap between the
first item shelved and the last is a median of 30 seconds:

    p50 30s    p90 62s    p99 156s    max 237s

1.4% of all orders would have anything sitting longer than a minute. That is
the price of one honest notification instead of two confusing ones, and it is
worth paying.

The same rule holds for the pickup display: show the number when the order is
complete. The `item_ready` events are for the bar, which does want to see that
a ticket is half done -- not for the customer, who only wants to know when to
stand up.

Nothing below applies to it.

---

## 2 · Time to order — the stack

### What it does

At the moment the planner says to order, push a notification to a phone whose
tab is closed. The message the spec asks for:

```
Time to order
Order now and it's ready by 13:00 — about 4 minutes.
[ Open order ]
```

### Data model

One table, SQLModel like the rest, in the same database — Litestream
replicates `${CAFE_DB}`, so a reminder survives a restart. A reminder that
vanishes on deploy is worse than one never offered.

```python
class ReminderRow(SQLModel, table=True):
    reminder_id: str = Field(primary_key=True)      # returned to the client
    service_date: str                                # cafe-local business date
    due_at_s: float                                  # seconds since local midnight
    wanted_at_s: float | None                        # what they asked for
    lines: dict                                      # the basket, to re-quote with
    endpoint: str                                    # push subscription
    p256dh: str
    auth: str
    state: str = "pending"                           # pending|sent|failed|cancelled
    attempts: int = 0
    created_at: datetime
    sent_at: datetime | None = None
```

Index `(state, due_at_s)` — that is the scheduler's only query.

### `POST /reminders`

```json
{ "lines": [ { "drink": "latte", "milk_type": "oat", "variant": "hot" } ],
  "wanted_at": "13:00",
  "subscription": { "endpoint": "https://fcm.googleapis.com/…",
                    "keys": { "p256dh": "…", "auth": "…" } } }
```

→ `201 { "reminder_id": "…", "order_at": "12:45", "order_at_s": 45900.0 }`

**Re-derive the time on the server.** Call `plan_for(...)` from
`core/promise.py` with the posted basket, exactly as `POST /plan` does. Do not
accept an `order_at` from the client: this is a time the cafe will act on, and
a caller should not be able to name it. Same argument as the `quoted` flag on
`POST /orders`.

Reject with `400`: a `wanted_at` in the past, one past `closes_at`, an empty
basket, a malformed subscription.

### `DELETE /reminders/{id}`

The customer ordered anyway, or changed their mind. Also fired by the front end
immediately after a successful `POST /orders`. Idempotent — deleting a fired or
unknown reminder is a `204`, not an error.

### The scheduler

An asyncio task started in the `lifespan` hook that already exists in
`app/main.py`. Every 30 seconds:

1. `select` where `state = 'pending'` and `due_at_s <= day_seconds(params)` and
   `service_date = service_date(params)`
2. mark `sending` and commit before the network call, so a crash mid-send
   cannot double-fire
3. **re-quote before sending.** The queue has moved since the reminder was
   booked. If ordering now still lands before `wanted_at`, send the message
   above. If it no longer can, send the honest version — the earliest time now
   possible — and never a stale number. This is the same rule as the planner's
   `achievable: false` state.
4. mark `sent`, or `failed` with `attempts += 1`

No leader election. `deploy/fly.toml` sets `min_machines_running = 1` and
`auto_stop_machines = false`, so there is exactly one machine — but say so in a
comment, because the day that changes this task silently double-sends.

Sweep `pending` rows from earlier service dates to `cancelled` on startup. A
reminder for yesterday's lunch is not worth firing.

### Sending

`pywebpush`, with `TTL: 900` and `Urgency: normal`. It is advice about a queue;
if it cannot be delivered inside fifteen minutes it is wrong anyway.

| response | do |
|---|---|
| `201` / `200` | mark `sent` |
| `404` / `410` | the subscription is dead — delete the row |
| `429` | back off, retry next tick, honour `Retry-After` |
| `5xx` | retry next tick, give up after 3 attempts |

### Config

Follows the existing pattern in `app/config.py` — env vars read once in
`Settings.__init__`, no silent defaults.

| var | notes |
|---|---|
| `CAFE_VAPID_PRIVATE` | base64url. The first secret this app has had. |
| `CAFE_VAPID_PUBLIC` | served from `GET /config` for the client to subscribe with |
| `CAFE_VAPID_SUBJECT` | `mailto:` contact, required by the push services |

With no keys set, `POST /reminders` returns `503` and the front end keeps
showing the alarm fallback. The app must start and serve orders without them.

### Dependency

`pywebpush`, which pulls `py-vapid`, `http-ece` and `cryptography`.
`requirements.txt` is six packages and says it is "kept to the app so the image
stays small" — `cryptography` is a large native build. That is a real change to
the image, worth a look at the build time before committing to it.

---

## What this must not break

`tests/test_client.py::test_the_app_and_the_core_agree` replays a simulated day
through the API and asserts the app moves every order through exactly the
states `core` moves it through. It is ground rule 2 in executable form.

A reminder is not an order and must emit no order events. If you find yourself
wanting to log one against an `order_id`, there isn't one yet — that is the
point of the feature.

---

## Deployment

- **HTTPS everywhere.** Push requires a secure origin. Fly gives it; localhost
  is exempt for development.
- **The service worker must be served from the app origin** at a path that
  scopes the page — `/sw.js`, from the same static mount as `/assets`.
- **iOS needs a web app manifest.** Safari only permits push for a page the
  user has installed to the home screen. Without a manifest and an install
  prompt, every iPhone falls back to the alarm, which on a campus is most of
  the audience.

---

## Decide these before writing code

**A push subscription is the first thing tying a device to the server.** The
app stores no per-customer identity today — My Orders says so on the screen:
"No account — this phone remembers the orders it placed". A subscription store
changes that for a convenience feature, in a pilot, on a campus. It deserves a
decision, not a migration.

**The mechanism is already parameterised without push.** `core/params.py` has
`shown_wait`, `shift_fraction`, `shift_threshold_min` and `shift_delay_min`,
and `sim/engine.py` already defers a share of walk-ups who are shown a long
wait. Push raises the take-up of that nudge; it does not create it. Whether
people actually come back later is now measurable from the `promise_set` events
the app writes on every quoted order — worth reading that before paying for
this.

---

## Done when

- A reminder booked for five minutes out fires once, with the tab closed, on a
  phone that has installed the app.
- Placing the order first cancels it; nothing arrives.
- Restarting the process mid-wait still fires it.
- A revoked subscription is deleted rather than retried forever.
- `test_the_app_and_the_core_agree` still passes.
- With no VAPID keys configured, the app boots and takes orders as it does now.

---

## What already exists to build against

| thing | where | notes |
|---|---|---|
| `POST /plan` | `app/routes/orders.py` | both directions; returns `order_at_s`, `ready_at_s`, `wait_s`, `achievable` |
| `plan_for` / `ready_if_ordered_now` | `core/promise.py` | quote from the forecast, 80th percentile |
| `promise_set` events | written on every quoted order | `{promised_at_s, source, wait_s, basket_s, quantile, days}` |
| `promise_error` | `analysis/metrics.py` | runs over the app's own log today |
| `GET /stream` | `app/stream.py` | SSE, already carries `state_change` |
| `lifespan` | `app/main.py` | where the scheduler task starts |
| `Settings` | `app/config.py` | env-var pattern for the VAPID keys |

Times are seconds since local midnight in the cafe's timezone, paired with a
`service_date`. Convert with `day_seconds(params)` and `service_date(params)`
from `app/config.py` — never with the server's own clock.
