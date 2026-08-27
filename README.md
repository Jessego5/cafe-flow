# cafe-flow

Order-ahead web app for a campus cafe, plus a discrete-event simulator of the
same cafe at peak, built on one shared domain core so the two cannot diverge.

Build order and acceptance criteria live in
[campus-cafe-ordering-plan.md](campus-cafe-ordering-plan.md).

## Setup

    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python -m pytest tests -q

## Status

| Milestone | State |
|---|---|
| M1 shared core | done |
| M2 app skeleton | not started |

## Parameters

Every duration, rate, capacity and mix fraction lives in `params/base.yaml`, and
each one carries provenance (`assumed`, `observed`, `fitted`). Overlays merge
left to right:

    from core.params import load_params
    params = load_params("params/base.yaml", "params/observed.yaml")
    params.provenance_report().caption()   # 'provenance: 100% assumed'

Calibration at M8 writes `params/observed.yaml` and changes no Python.
