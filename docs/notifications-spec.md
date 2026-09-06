# Notifications — backend spec

Section 4 of [ui-spec.md](ui-spec.md), written out for whoever builds it.

This document used to be long. Most of it described a notification that no
longer exists, and what is left is short enough that the reason for the change
is worth more than the remaining spec.

Two notifications were specified. One of them has been withdrawn: the app now
holds a paid pre-order and puts it in the queue itself, so there is nothing to
remind anybody to do.

That was the notification needing all the hard parts -- web push, VAPID keys, a
subscription store, and on iOS a home-screen install before Safari will deliver
anything. None of it is needed now. What replaces it is a release loop in the
`lifespan` hook that already exists, specified in `docs/ui-spec.md` under
`POST /held`.

| | fires | needs |
|---|---|---|
| **#42 is ready** | on the *order* reaching `ready` | nothing new — the SSE stream already carries it |
| ~~**Time to order**~~ | ~~at `order_at`~~ | **withdrawn — the app places the order itself** |

Only the first one is left, and it is an afternoon.

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

## 2 · What replaced it

Adaptive release, in `sim/engine.py` and swept in
`params/experiments/adaptive_release.yaml`. The server holds the paid order and
puts it in the queue at the last moment the live queue depth still says it will
be ready on time.

A reminder could never have done this. Once somebody has been told to order they
cannot be re-timed, so a reminder has to commit to the forecast made when they
booked. Holding the order means the decision happens with the actual line in
hand. Over twelve simulated days at half adoption:

    fixed lead        median drink ready 59 minutes early, 99% over five
    adaptive release  median within a minute, 91% on time

The build that is left: a `held_orders` table, a release loop polling live queue
depth, and cancel-until-release. No push library, no keys, no secrets. The app
still needs to boot and take orders if the release loop is off, and a held order
must survive a restart -- Litestream replicates `${CAFE_DB}`, so put the table
there rather than in memory.

## 3 · What this must not break

`tests/test_client.py::test_the_app_and_the_core_agree` replays a simulated day
through the API and asserts the app moves every order through exactly the states
`core` moves it through. It is ground rule 2 in executable form.

A held order is not an order and must emit no order events until it is released.
If you find yourself wanting to log one against an `order_id`, it does not have
one yet -- that is the point of the feature.

## 4 · Deployment

- **HTTPS everywhere** for the ready notification's push, if you build the
  out-of-tab version. Fly gives it; localhost is exempt.
- **The service worker** must be served from the app origin at a path that
  scopes the page.
- **No iOS install prompt is required any more.** The only push left is the
  ready notification, which rides the SSE stream while a tab is open and is
  worth having in that degraded form on its own.

## 5 · Done when

- A hold booked for twenty minutes out releases itself, once, with the tab
  closed, and the drink is ready within a couple of minutes of the time asked
  for.
- Cancelling before release leaves nothing behind; cancelling after it is a
  normal order cancellation.
- Restarting the process mid-hold still releases it.
- With the release loop disabled the app boots and takes walk-up orders as now.
- `test_the_app_and_the_core_agree` still passes.
