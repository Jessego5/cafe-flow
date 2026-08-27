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
| `/bar` | barista: live queue, one tap per transition |
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
| M4 metrics | partial: waits, throughput, utilisation, lost margin |
| M5 policies | partial: fifo and batching, no bounded reorder yet |
| M6 balking and channel choice | done |
| M7 experiments | partial: named arms, no sweeps yet |

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

## Comparing arms

An arm is a set of parameter overlays. `params/experiments/superauto.yaml`
holds the second reading of the espresso bar, in case the Schaerer in the
photographs is a super-automatic: no pitcher, so nothing batches, and the wand
and group head collapse into one serialised machine.

    python -m sim.experiments --arms manual_bar,batched,adoption,adoption_batched --seeds 20

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
