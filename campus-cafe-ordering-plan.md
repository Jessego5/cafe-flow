# Campus Cafe Order-Ahead — Implementation Spec

> **Audience: a coding agent.** Build in milestone order. Each milestone has explicit interfaces and a definition of done. Later milestones assume earlier invariants hold.

## What this is

An order-ahead web app for a campus cafe, plus a discrete-event simulator of the cafe at peak. Both are built on one shared domain core so they cannot diverge.

**Research question:** how much additional peak throughput (drinks/hour) is available at fixed prices, and which mechanism delivers it — register decoupling, milk batching, queue reordering, or shifting demand into the trough.

**Build strategy:** shared core first, then a working app skeleton, then the simulator on top of the same core, then experiments, then feed the findings back into the app. All parameters are assumed placeholders until calibration at M8, when real observed values are swapped in via config with no code changes.

---

## Architecture

```
                   ┌──────────────────────────┐
                   │        core/             │
                   │  domain types            │
                   │  order state machine     │
                   │  menu + station plans    │
                   │  capacity cost function  │
                   │  event schema            │
                   │  (pure python, no I/O)   │
                   └────────┬────────┬────────┘
                            │        │
              imports ──────┘        └────── imports
                   │                              │
        ┌──────────▼──────────┐      ┌────────────▼───────────┐
        │       app/          │      │        sim/            │
        │  FastAPI + SQLite   │      │  SimPy, virtual time   │
        │  3 web views, SSE   │      │  arrivals, policies    │
        │  WALL-CLOCK time    │      │  sweeps, 600+ runs     │
        └──────────▲──────────┘      └────────────────────────┘
                   │
                   └──── sim/client.py (HTTP replay: demo + load test only)
```

### Why there are two runtimes

**The experiments cannot run through HTTP.** Sweeps are ~5 arms x 6 points x 20 seeds = 600 runs, each placing hundreds of orders. The DES runs a simulated day in milliseconds because virtual time jumps event to event; the app runs on wall-clock time. Do not attempt to unify them.

The split is by purpose:

| Activity | Runtime | Why |
|---|---|---|
| Parameter sweeps, finding the adoption threshold | in-process DES | needs virtual time and 600 runs |
| Validating capacity accounting under concurrency | HTTP replay | must exercise the real DB transactions |
| Demo (60x rush driving the live UIs) | HTTP replay | must look real |

Both write the **same event schema**, so `analysis/metrics.py` works on either without modification.

---

## Ground rules

Invariants. Violating any of them breaks the project.

1. **No magic numbers outside config.** Every duration, rate, capacity, probability, and mix fraction lives in `params/base.yaml`. If you are typing a number into a `.py` file that isn't `0`, `1`, or an array index, it belongs in config. This is what makes calibration at M8 a file swap rather than a refactor.
2. **Rules live in `core/` only.** If the app and the simulator could ever disagree about what a latte costs or which state transitions are legal, the logic is in the wrong place. `app/` and `sim/` contain no domain rules.
3. **Every parameter carries provenance:** `assumed`, `observed`, or `fitted`. Reports state what fraction of their inputs are still `assumed`.
4. **The event log is the only source of truth for metrics.** Append-only, one row per state transition. Never compute a wait time from mutable state.
5. **Determinism in `sim/`.** One seeded `numpy.random.Generator` created once and passed down. Same seed + same params = byte-identical event log.
6. **Stations are seized by baristas, not independent servers.** See M3. This is the modelling error that would invalidate every result.
7. **Fail loudly on bad config.** Missing or nonsensical parameters raise at load, never silently default.

---

## Stack

- Python 3.11+, `pydantic`, `pyyaml`
- `app/`: `fastapi`, `sqlmodel`, `sse-starlette`, `uvicorn`
- `sim/`: `simpy`, `numpy`, `pandas`, `matplotlib`
- `web/`: React + Vite, plain CSS. No component library.
- `pytest` throughout
- Deploy: Docker, Fly.io (single instance + persistent volume), Litestream for SQLite backup, GitHub Actions for CI

---

## Repo layout

```
cafe-flow/
├── params/
│   ├── base.yaml              # all-assumed defaults
│   ├── observed.yaml          # created at M8, overlays base
│   └── experiments/           # per-arm overlays
├── core/
│   ├── params.py              # load, validate, merge, provenance
│   ├── types.py               # Order, Item, Task, Batch, Customer
│   ├── states.py              # state machine + legal transitions
│   ├── menu.py                # menu -> station plan resolution
│   ├── capacity.py            # pluggable bottleneck cost function
│   └── events.py              # event schema (shared by app + sim)
├── app/
│   ├── main.py                # FastAPI
│   ├── db.py                  # SQLModel tables
│   ├── routes/                # menu, slots, orders, barista
│   └── stream.py              # SSE
├── sim/
│   ├── engine.py              # SimPy harness over core/
│   ├── arrivals.py            # class-block-driven generator
│   ├── policies.py            # FIFO / batching / bounded reorder
│   ├── balking.py
│   ├── experiments.py         # sweep runner
│   └── client.py              # HTTP replay (M9)
├── analysis/
│   ├── metrics.py             # ALL metrics, from event log only
│   ├── calibrate.py           # fit params to observations (M8)
│   └── figures.py
├── web/{student,barista,display}/
├── observations/              # human-collected CSVs land here
├── out/
├── deploy/
│   ├── Dockerfile
│   ├── fly.toml
│   ├── litestream.yml
│   └── entrypoint.sh          # migrate, restore from backup, start
├── .github/workflows/ci.yml
└── tests/
```

---

## The parameter file

`params/base.yaml`. The spine of the project. Design it first.

```yaml
meta:
  scenario: base_assumed
  seed: 42
  sim_start: "07:00"
  sim_end: "15:00"

stations:
  register:    { capacity: from_staffing, base_s: 35, source: assumed }
  steam_wand:  { capacity: 1, setup_s: 10, per_6oz_s: 15,
                 batch_key: milk_type, max_batch_oz: 32, source: assumed }
  group_head:  { capacity: 2, shot_s: 25, source: assumed }
  blender:     { capacity: 1, run_s: 90, blocking: true, source: assumed }
  cold_tap:    { capacity: 99, pour_s: 8, source: assumed }
  oven:        { capacity: 1, run_s: 180, batch_size: 6, source: assumed }

# Which station gates slot capacity. Set from the M8 findings.
# If this turns out to be `register`, swap the name — no code changes.
bottleneck_station: steam_wand

staffing:
  - { from: "07:00", to: "10:00", baristas: 2 }
  - { from: "10:00", to: "13:00", baristas: 3 }
  - { from: "13:00", to: "15:00", baristas: 2 }

menu:
  latte:
    price_cents: 500
    cogs_cents: 130
    requires_milk: true
    tasks: [ { station: steam_wand, oz: 8 }, { station: group_head, shots: 1 } ]
    assembly_s: 20
  drip:       { price_cents: 250, cogs_cents: 40,  requires_milk: false,
                tasks: [ { station: cold_tap } ], assembly_s: 5 }
  cold_brew:  { price_cents: 450, cogs_cents: 90,  requires_milk: false,
                tasks: [ { station: cold_tap } ], assembly_s: 8 }
  cappuccino: { price_cents: 475, cogs_cents: 120, requires_milk: true,
                tasks: [ { station: steam_wand, oz: 6 },
                         { station: group_head, shots: 1 } ], assembly_s: 18 }
  blended:    { price_cents: 600, cogs_cents: 170, requires_milk: true,
                tasks: [ { station: blender } ], assembly_s: 15 }
  pastry:     { price_cents: 350, cogs_cents: 110, requires_milk: false,
                tasks: [ { station: oven } ], assembly_s: 5 }

mix:
  drink: { latte: 0.38, drip: 0.18, cold_brew: 0.16,
           cappuccino: 0.12, blended: 0.08, pastry: 0.08 }
  milk:  { whole: 0.32, oat: 0.38, skim: 0.14, almond: 0.16 }
  attach_rate: 0.15
  source: assumed

arrivals:
  class_blocks:
    - { ends_at: "08:50", sections: 8,  avg_enrollment: 28 }
    - { ends_at: "09:50", sections: 14, avg_enrollment: 30 }
    - { ends_at: "10:50", sections: 16, avg_enrollment: 32 }
    - { ends_at: "11:50", sections: 12, avg_enrollment: 30 }
    - { ends_at: "12:50", sections: 10, avg_enrollment: 28 }
  capture_rate: 0.055          # FITTED AT M8 — primary calibration knob
  offset_min: 3.0
  sigma_min: 2.5
  background_per_hour: 8
  source: assumed

customers:
  time_budget_min:    { dist: lognormal, median: 9.0, sigma: 0.5 }
  balk_tolerance_min: { dist: lognormal, median: 7.0, sigma: 0.45 }
  preorder_adoption: 0.0
  no_show_rate: 0.03
  source: assumed

policy:
  name: fifo                   # fifo | batch_milk | bounded_reorder
  lookahead_s: 180
  reorder_window_s: 180
  starvation_guard_s: 300

slots:
  enabled: false               # turned on at M10 if findings support it
  width_min: 5
  walkup_reserve_fraction: 0.30
  min_lead_time_min: 10
```

---

## Milestones

### M1 — Shared core

`core/`. Pure Python. No FastAPI, no SimPy, no database, no I/O beyond reading config.

```python
# core/states.py
class State(StrEnum):
    PLACED = "placed"; ACCEPTED = "accepted"; IN_PROGRESS = "in_progress"
    READY = "ready"; PICKED_UP = "picked_up"
    CANCELLED = "cancelled"; ABANDONED = "abandoned"; BALKED = "balked"

LEGAL: dict[State, set[State]] = {...}
def transition(order, to: State, at: float, actor: str) -> Event:
    """Raises IllegalTransition. Returns the event to append."""

# core/capacity.py
class CapacityModel(Protocol):
    def cost(self, item: Item) -> float:
        """Bottleneck-seconds consumed by this item."""
    def batch_cost(self, items: list[Item]) -> float:
        """Cost when made together. <= sum of individual costs."""

class StationCapacityModel(CapacityModel):
    """Reads bottleneck_station from params. If that station is
    `register`, cost is flat per order and this degenerates to
    order-count slots. No code changes needed to switch."""

# core/menu.py
def resolve_tasks(item: Item, params) -> list[Task]
```

**Done when:** `pytest tests/test_core.py` passes; illegal transitions raise; `batch_cost` of 4 same-milk lattes is strictly less than 4x `cost`; core imports nothing from `app/` or `sim/`; a corrupted `base.yaml` raises a validation error naming the bad field.

### M2 — App skeleton

End-to-end working app on the assumed params. Orders placed by hand through the UI.

- SQLModel tables: `menu_items`, `orders`, `order_items`, `order_events`, `slots`. `orders.is_simulated` boolean so sim and real traffic coexist and filter cleanly.
- `order_events` is append-only and written by `core.states.transition` only.
- Routes: `GET /menu`, `POST /orders`, `GET /orders/{id}`, `POST /orders/{id}/transition`, `GET /stream` (SSE).
- Slots exist in schema and API but `slots.enabled: false` — orders go straight to the queue. The capacity plumbing is built; the policy is deferred.
- Three minimal views: student (menu, cart, status), barista (SSE list, one tap per transition), display (ready order numbers).
- Payment and auth are stubbed. Non-negotiable.

**Done when:** a person can place an order in the student view, watch it appear in the barista view within a second, tap it through to ready, and see the number on the display. `order_events` contains the full trace.

### M2.5 — Ship the skeleton

Deploy now, while the app is small. Deploying a 400-line app is an afternoon; deploying a 4,000-line app you have never deployed is a week. This also gets a URL in front of the cafe manager early, which is worth more than any amount of local polish.

**Target: one always-on process with a persistent disk.** Not serverless. SSE needs long-lived connections and SQLite needs a real filesystem, so Vercel/Lambda-style functions are out for the API. Fly.io, Railway, or Render all work; the spec assumes Fly.

- `deploy/Dockerfile` — multi-stage: build the Vite frontends, copy static output, install Python deps, run uvicorn. One container serves API and static assets, so there is no CORS configuration to get wrong.
- `deploy/fly.toml` — **pin to exactly one instance** (`min_machines_running = 1`, `max_machines_running = 1`). SQLite in WAL mode is single-writer; two instances silently corrupt state. Mount a volume at `/data`.
- **Litestream** replicating `/data/cafe.db` to S3 or Cloudflare R2. For a pilot, losing the order log means losing the research data.
- `params/` mounted from the volume, not baked into the image, so `observed.yaml` can be updated at M8 without a rebuild.
- Health check at `GET /healthz` asserting DB reachable and event log writable.

**Environments** — three, with separate databases:

| Env | DB | Simulated orders |
|---|---|---|
| `dev` | local file | allowed |
| `demo` | Fly volume | allowed, seeded |
| `pilot` | Fly volume | **rejected at the API** |

`is_simulated` stops being a convenience flag and becomes a safety property: in `pilot`, `POST /orders` with the sim header returns 403, and the barista view filters on it server-side. A simulated order appearing on the bar during a real rush destroys trust in the tool permanently.

**SSE deployment gotchas**, all of which will bite:
- Disable proxy buffering (`X-Accel-Buffering: no`); a buffering proxy makes SSE appear to work locally and silently fail in production.
- Set proxy read timeout above the heartbeat interval; send a comment heartbeat every 15s.
- Client must auto-reconnect with `Last-Event-ID` and **resync full queue state on reconnect**, not just resume the stream. Cafe wifi drops.

**Timezone.** Store UTC, render local. Add `cafe.timezone` to `params/base.yaml` and use it for slot boundaries and class-block times. Servers run UTC and class schedules are local — this is the highest-probability bug in the project.

**CI** (`.github/workflows/ci.yml`): run `pytest` on every push, and on merge to main run the M9 drift check (see below) before deploying. Deploy on green only.

**Done when:** a public URL serves the student view; an order placed on a phone appears on the barista view on another device within a second; the container restarts without data loss; Litestream restore into a fresh volume reproduces the database.

### M3 — Simulator engine

`sim/engine.py`. SimPy over the same `core/`. Virtual time.

```python
class Barista:
    """Agent. Seizes stations. Never parallel with itself."""

class Cafe:
    def __init__(self, env, params, rng, log): ...
    def serve(self, order): ...   # generator
```

A drink needs **a barista AND a station**:

```
with barista.request():
    with wand.request():       yield timeout(steam_s)
    with group_head.request(): yield timeout(shot_s)
    yield timeout(assembly_s)
```

With 2 baristas and 1 wand, the wand contends and baristas block. Do **not** model stations as independent parallel servers. In a small cafe the same human takes orders and pulls shots, so the register competes for barista time even when it isn't the nominal bottleneck.

Arrivals in `sim/arrivals.py`: per class block, `n = sections * avg_enrollment * capture_rate`, times drawn from `Normal(ends_at + offset_min, sigma_min)`, plus Poisson background. Writes `out/events_<scenario>_<seed>.parquet` in the M1 event schema.

**Done when:** conservation test (`placed == picked_up + balked + abandoned + in_flight_at_end`); determinism test (same seed → identical log hash); a lone latte takes exactly `setup_s + per_6oz_s*(8/6) + shot_s + assembly_s`; the arrival histogram shows five distinct bursts.

### M4 — Metrics

`analysis/metrics.py`. Reads the event log and nothing else. Works identically on app-produced and sim-produced logs.

```python
wait_percentiles(log, by_channel=True)   # p50/p90/p95
throughput(log, window)                  # drinks/hour
station_utilisation(log)                 # time series
balk_count_and_lost_margin(log, params)
promise_error(log)                       # ready_at - promised_at
batch_rate(log)                          # milk drinks steamed in a batch
fairness_gap(log)                        # walkup p95 - preorder p95
```

**Done when:** each function is tested against a hand-built synthetic log with a known answer, and `analysis/` imports nothing from `sim/` or `app/`.

### M5 — Scheduling policies

`sim/policies.py`:

```python
class Policy(Protocol):
    def next_task(self, pending: list[Order], now: float) -> Task | Batch: ...
```

- `FIFOPolicy` — baseline.
- `BatchMilkPolicy(lookahead_s)` — groups pending milk drinks sharing `batch_key` within the lookahead window, up to `max_batch_oz`, costing `core.capacity.batch_cost`. **This is the core throughput mechanism.**
- `BoundedReorderPolicy(window_s, starvation_guard_s)` — permutes within a window to cut switchover; any order past the guard jumps unconditionally.

**Done when:** for 4 same-milk lattes, batching consumes strictly fewer wand-seconds than FIFO with identical drinks served; no order under bounded reorder waits more than `starvation_guard_s` past its FIFO position.

### M6 — Balking and channel choice

`sim/balking.py`. On arrival a walk-up observes queue depth, estimates wait, and abandons if it exceeds their drawn `balk_tolerance_min`; emits `balked` with the estimate. With `preorder_adoption > 0`, that fraction instead order one class block early for pickup at their block's end and never balk.

**Done when:** balks rise monotonically with volume; a very high tolerance drives balks to zero; adoption 1.0 produces zero walk-ups.

### M7 — Experiments

`sim/experiments.py`. **In-process only.** Arms as overlays in `params/experiments/`:

| Arm | Config |
|---|---|
| A | walk-up only, fifo — baseline |
| B | adoption 0.3, no slots, fifo |
| C | B + slots enabled |
| D | C + batch_milk |
| E | D + bounded_reorder |

Sweeps, >=20 seeds each: `preorder_adoption` 0→0.6; `staffing.baristas` 2/3; `mix.milk` 2 vs 5 types; `slots.width_min` 3/5/10; `policy.lookahead_s` 60/180/300.

```
python -m sim.experiments --arms A,C,D --sweep preorder_adoption --seeds 20
```

**Done when:** results table with 95% CIs across seeds, plus figures for throughput-vs-adoption, wait-p95-by-arm, and station utilisation. Every figure caption states `provenance: N% assumed`.

### M8 — Calibration (the swap-in point)

Real numbers enter here. **No code changes to M1–M7.**

`analysis/calibrate.py`:
1. Read `observations/*.csv` (schemas below).
2. Write measured service times, station occupancies, mixes, and staffing into `params/observed.yaml` with `source: observed`.
3. Fit `arrivals.capture_rate` by minimising squared error between simulated and observed queue-length samples; mark `source: fitted`.
4. Set `bottleneck_station` from measured occupancy.
5. Emit `out/validation.html` — simulated vs observed queue depth with RMSE.

**Validation gate:** if the curves diverge badly the model is wrong. **Fix the model; do not tune the fit.** Fitting `capture_rate` to match arrival volume is legitimate. Fitting it to paper over a wrong resource model produces a confident wrong answer. Re-run M7 with the overlay only after this passes.

### M9 — HTTP replay

`sim/client.py`. Replays M7 scenarios against the running app over real HTTP. Not for sweeps — for validation and demo.

- `--speed 60` compresses a morning into a minute.
- Concurrency test: 50 simultaneous reservations against a slot with room for 10 → exactly 10 succeed. The capacity check and decrement must be one atomic transaction.
- Asserts the app's event log produces the same metrics as the in-process run for the same scenario, within tolerance. **If they disagree, the app and the core have drifted — that is a bug in `app/`, not a tolerance to widen.**

### M10 — Feed findings back into the app

Only now are these decided, because only now is there evidence:

| Decision | Determined by |
|---|---|
| Slots on or off, and width | M7 arms C/D + slot-width sweep |
| `bottleneck_station` for capacity costs | M8 measured occupancy |
| Batch grouping in the barista view | M7 arm D batch rate |
| Per-drink wait estimates in the student view | M8 service time distributions |
| `walkup_reserve_fraction` | M7 fairness gap |
| Staffing recommendation | M7 staffing sweep |

Also add the handoff-shelf display refinements — interruptions are a direct tax on the bottleneck, and this is the cheapest throughput win available.

### M11 — Pilot hardening (only if real students will use it)

Skip this entirely if the deployment is a demo. Everything here exists because a real barista during a real rush has no patience and no fallback.

**Reality check on scale.** Peak is roughly 40 orders in 10 minutes. That is nothing — a single small instance handles it with orders of magnitude to spare. **The constraint is reliability, not throughput.** Do not build caching, queues, read replicas, or horizontal scaling. Build for the wifi dropping.

**The barista device is the real deployment target.** Assume an iPad on cafe wifi, mounted behind the bar:
- Ship the barista view as a PWA so it installs to the home screen and runs without browser chrome.
- Prevent screen sleep (Wake Lock API) — a sleeping display during a rush is worse than no display.
- Render from local state and reconcile on reconnect, so a 20-second network drop shows a stale queue rather than an empty one.
- Every action idempotent by order id and target state, so a double-tap on a laggy connection cannot skip a transition.
- A visible connection indicator. The barista must be able to tell "no new orders" from "not connected."

**Manual override.** A "system down" mode that shows the pending queue as a plain printable list, and an obvious way for staff to fall back to the normal POS. Never let the app be the only path to a coffee.

**Data minimisation.** Use order numbers, not names. Do not collect email or phone unless a notification actually requires it, and if it does, store a push token rather than a phone number. Delete order-level PII on a short schedule; the event log needs timestamps and item composition, not identities.

**Human subjects.** If pilot data will appear in a paper or public report, check whether your institution requires IRB review. Aggregate operational data usually falls outside it, but ask before collecting rather than after.

**Campus network.** Verify the cafe wifi does not block outbound connections or captive-portal the iPad every few hours. Test this before the pilot day, not during it.

**Pilot exit criteria.** Agree with the cafe manager in advance on what ends the pilot: a wrong-order rate, a downtime threshold, or simply staff saying stop. Write it down. Have a rollback that takes under a minute.

---

## Observation data contract

The human collects these in parallel with M1–M7. Column names are the contract; `calibrate.py` reads exactly these.

```
observations/service_times.csv
  timestamp, drink_type, milk_type, station, start_s, end_s, barista_count

observations/queue_samples.csv
  timestamp, queue_length, baristas_on, wand_busy, register_busy

observations/balks.csv
  timestamp, count, observed_queue_length

observations/order_mix.csv
  drink_type, milk_type, count

observations/class_blocks.csv
  ends_at, sections, avg_enrollment, building, walk_minutes
```

Sample every 30s across at least one peak and one trough. The balk count is the entire revenue case — collect it carefully.

---

## Non-goals

Payments, authentication, real inventory, multi-location, native mobile, nutrition data, loyalty. None bear on the research question. Do not build them.

---

## Expected findings

Be prepared for the gains to be modest, to vanish below ~25% adoption, or for the register to turn out not to be the bottleneck. A negative result honestly measured is a better outcome than a positive one assumed. The report states the ceiling and the assumed-parameter fraction either way.

## Start here

M1. Build `core/` and the config validator before anything else — both the app and the simulator inherit from it, and it is far cheaper to correct at 200 lines than at 2,000.
