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
| M3 simulator engine | not started |

## Deploying

One always-on machine with a persistent disk, SQLite replicated by Litestream.
The runbook, the environment table and the SSE gotchas are in
[deploy/README.md](deploy/README.md).

    docker build -f deploy/Dockerfile -t cafe-flow .
    docker run -p 8080:8080 -v cafe_data:/data cafe-flow
    docker run --rm --entrypoint /usr/local/bin/verify-restore.sh cafe-flow

## Parameters

Every duration, rate, capacity and mix fraction lives in `params/base.yaml`, and
each one carries provenance (`assumed`, `observed`, `fitted`). Overlays merge
left to right:

    from core.params import load_params
    params = load_params("params/base.yaml", "params/observed.yaml")
    params.provenance_report().caption()   # 'provenance: 100% assumed'

Calibration at M8 writes `params/observed.yaml` and changes no Python.
