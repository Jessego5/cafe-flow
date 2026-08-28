# cafe-flow

Order-ahead web app for a campus cafe, plus a discrete-event simulator of the
same cafe at peak, built on one shared domain core so the two cannot diverge.

Build order and acceptance criteria live in
[campus-cafe-ordering-plan.md](campus-cafe-ordering-plan.md).

## Setup

    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python -m pytest tests -q

## Running the app

    cd web && npm install && npm run build && cd ..
    .venv/bin/python -m uvicorn app.main:app --reload

| Path | View |
|---|---|
| `/` | student: menu, cart, order status |
| `/bar` | barista: live queue, one tap per transition, what to run together |
| `/pickup` | display: order numbers ready for collection |

The API keeps the unprefixed paths (`/menu`, `/orders`, `/orders/{id}`,
`/orders/{id}/transition`, `/queue`, `/display`, `/slots`, `/stream`, `/config`).
For frontend work, `cd web && npm run dev` proxies them to uvicorn on :8000.

`CAFE_ENV` selects the environment (`dev`, `demo`, `pilot`); `pilot` rejects
simulated orders at the API. `CAFE_DB` and `CAFE_PARAMS` (colon separated,
merged left to right) point at the database and the config.

Payment and auth are stubbed, and stay that way: neither bears on the research
question.

## Status

| Milestone | State |
|---|---|
| M1 shared core | done |
| M2 app skeleton | done |
| M2.5 ship the skeleton | built, not yet deployed |
| M3 simulator engine | done |
| M4 metrics | done |
| M5 policies | done |
| M10 findings back into the app | partial: the bar runs the measured policy |
| M6 balking and channel choice | done |
| M7 experiments | partial: arms, sweeps and figures; no calibrated run yet |
| M8 calibration | built; waiting on a counted rush |
| M9 HTTP replay and drift check | done |
| Policy selection | done: chooses a scheduler for any configuration |

## Deploying

One always-on machine with a persistent disk, SQLite replicated by Litestream.
The runbook, the environment table and the SSE gotchas are in
[deploy/README.md](deploy/README.md).

    docker build -f deploy/Dockerfile -t cafe-flow .
    docker run -p 8080:8080 -v cafe_data:/data cafe-flow
    docker run --rm --entrypoint /usr/local/bin/verify-restore.sh cafe-flow

## Scope

The cafe is Ground Truth, a Wisconsin Union cafe pouring Peet's Coffee, and it
already has order-ahead: Transact Mobile Ordering, deployed and advertised at
the door. So this project does not build a competing student ordering app. The
student view is a demo; the deliverable is the simulator, the observations, and
a barista-side queue that batches and reorders work the existing system does
not. `customers.preorder_adoption` is an observable here, not a sweep.

## Feeding it a till's history

A point-of-sale export replaces two of the largest guesses in any configuration
— the shape of the day and the mix of the order book — with something counted:

    python -m analysis.demand "Coffee Shop Sales.xlsx" --location "Hell's Kitchen" \
      --drop Housewares Clothing "Organic Beans" --out demand.yaml

Column names are arguments, so the same code reads a Maven Analytics teaching
set, a Transact export or a Square CSV. `arrivals.model: profile` then draws
the day from that measured curve instead of a class timetable.

`params/examples/maven_roasters.yaml` is built this way, from 46,844
transactions over 181 days. It is marked `synthetic`, not `observed`: Maven
Roasters is a fictitious business and the data is realistic in shape rather
than a record of anything that happened. A log from a real till would earn the
stronger word.

**What no sales log can supply**, however many rows it has: service times, which
station did the work, staffing, and — the important one — balking. A list of
people who bought something is structurally blind to everyone who looked at the
queue and left. That still has to be counted by a person.

## Choosing the scheduler for whatever cafe it is fed

`params/base.yaml` describes one cafe. Point the tool at another — a different
menu, different stations, different demand — and it works out which scheduler
*that* operation should run:

    python -m sim.select --objective margin --seeds 40
    python -m sim.select --params params/examples/espresso_bar.yaml --objective margin

The answers differ, which is the point. Three operations, built three different
ways — a photographed menu board, a hand-written sketch, and a transaction log:

| configuration | built from | verdict |
|---|---|---|
| Ground Truth | menu boards + published times | batching wins, given enough seeded days |
| Example espresso bar | hand written | FIFO; the group head is the constraint and cannot batch |
| Maven Roasters | 46,844 transactions | every policy identical to the cent: the shop is never busy enough for scheduling to matter |

Two rules keep it honest. A challenger must clear the incumbent's confidence
interval rather than merely beat its mean, because two policies whose intervals
overlap have not been told apart. And a tie goes to the simpler policy: a
scheduler that is harder to explain to a barista, adopted for a difference the
evidence cannot see, is a bad trade at any confidence.

It also refuses to apply a selection against a configuration that is still
mostly assumed. A model nobody has checked against the floor will name a winner
regardless, confidently, and be wrong.

`--apply` writes `params/selected.yaml`, which is an ordinary overlay. The app
reads it the way it reads everything else and never imports the simulator.

## One scheduler, two runtimes

`core/policies.py` decides what to make next and what to make together. The
simulator and the bar both run it, selected by `policy.name` in the config, so
a policy an experiment measured is the policy the bar is shown — not a second
implementation that has to be kept in step. Every event records which scheduler
was in force, because a week of logs spanning two policies is uninterpretable
without it.

`GET /queue` returns the resulting suggestions and what each is worth in
bottleneck-seconds. They are advisory: the barista decides.

## The app and the core cannot drift apart

Both runtimes share `core/`, so in principle neither can grow its own idea of
what a latte costs or which state moves are legal. `sim/client.py` makes that a
fact: it replays a simulated day at the running app over real HTTP and compares
the app's own event log, through the same `analysis/metrics.py`, against the
in-process run.

    python -m sim.client                    # structural, about two seconds
    python -m sim.client --speed 400 --compare-waits
    CAFE_PARAMS="params/base.yaml:params/experiments/slots_check.yaml" \
      python -m sim.client --concurrency

CI runs the first and the third on every merge, before deploying. If they
disagree it is a bug in `app/`, not a tolerance to widen — and there is a test
that deliberately breaks the app to prove the check would notice.

`--speed 60` compresses a morning into a minute against the live UIs, which is
the only way to show someone a rush on the bar display without waiting for one.

## Turning a counted rush into parameters

One person watching one rush produces five numbers, not a time series. Paste
what the counter gives you into a file and:

    python -m analysis.calibrate observations/rush.txt

It writes `params/observed.yaml`, searches for the one parameter that cannot be
counted directly (`arrivals.capture_rate`), and says whether the calibrated
model reproduces what was seen. It exits non-zero if it does not: the answer to
a model that cannot reproduce the observation is to fix the model, not to tune
the fit.

Volume agreeing proves little, because it is what `capture_rate` was fitted to.
Balking is the honest test — nothing is fitted to it — so the report calls out
how far the model's patience is from what was counted.

## Comparing arms

An arm is a set of parameter overlays. `params/experiments/superauto.yaml`
holds the second reading of the espresso bar, in case the Schaerer in the
photographs is a super-automatic: no pitcher, so nothing batches, and the wand
and group head collapse into one serialised machine.

    python -m sim.experiments --arms manual_bar,batched,adoption_batched --seeds 20 --figures
    python -m sim.experiments --sweep customers.preorder_adoption --values 0,0.3,0.6 --seeds 20

A sweep is not looking for an optimum — almost every input is still assumed, so
an optimum would be an artefact of a guess. It answers which assumptions the
conclusion depends on: an arm that wins across the whole plausible range of a
parameter does not need that parameter measured carefully.

## Parameters

Item names, prices and the board's hot/iced offer are `observed`. Service times
are `published`, cited at each parameter. Mix, hot/iced split, capture rate and
staffing are still `assumed`; see
[observations/README.md](observations/README.md) for what to count and in what
order. Overlays merge left to right:

    from core.params import load_params
    params = load_params("params/base.yaml", "params/observed.yaml")
    params.provenance_report().detail()
    # 'provenance: 79% assumed, 2% published, 19% observed'

Calibration at M8 writes `params/observed.yaml` and changes no Python.
