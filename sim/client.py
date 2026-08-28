"""Replay a simulated day against the running app, over real HTTP.

Not for sweeps. A sweep is hundreds of arms and the DES runs a day in
milliseconds because virtual time jumps event to event; pushing that through
HTTP would take days and prove nothing extra. This exists for two other jobs.

**Validation.** Both runtimes share `core/`, so in principle they cannot
disagree about what a latte costs, what work it implies, or which state moves
are legal. The drift check makes that a fact rather than a hope: it replays a
scenario over HTTP and compares the app's own event log, through the same
`analysis.metrics`, against the in-process run. If they diverge, the app has
grown a rule of its own, and that is a bug in `app/` rather than a tolerance to
widen.

**Demo.** `--speed 60` compresses a morning into a minute against the live UIs,
which is the only way to show someone what a rush looks like on the bar display
without waiting for one.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import threading
import time
from dataclasses import dataclass, field

import httpx

from analysis.metrics import (
    balk_count_and_lost_margin,
    state_counts,
    wait_percentiles,
)
from core.capacity import StationCapacityModel
from core.events import EventLog, EventType
from core.params import Params, load_params
from core.states import State
from core.types import Channel
from sim.engine import RunResult, run

__all__ = ["Replay", "DriftReport", "drift_check", "check_slot_concurrency", "serve"]

SIMULATED = {"X-Simulated-Order": "1"}
DEFAULT_SPEED = 60.0

#: Replayed waits are wall-clock and rescaled, so they carry scheduling jitter
#: the in-process run does not. Prices, costs and counts are compared exactly;
#: only the timings get a tolerance, and it is deliberately loose because
#: tightening it would only ever produce flakes, never catch drift.
WAIT_TOLERANCE = 0.25


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@dataclass
class Mismatch:
    """One place the app and the core disagreed."""

    what: str
    in_process: object
    over_http: object

    def __str__(self) -> str:
        return f"{self.what}: core says {self.in_process!r}, app says {self.over_http!r}"


@dataclass
class DriftReport:
    scenario: str
    seed: int
    orders: int
    mismatches: list[Mismatch] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mismatches

    def render(self) -> str:
        head = f"{self.scenario} seed={self.seed}: {self.orders} orders replayed"
        if self.ok:
            return f"{head}\n  no drift: the app and the core agree"
        lines = [head, f"  {len(self.mismatches)} disagreement(s):"]
        lines += [f"    {mismatch}" for mismatch in self.mismatches]
        lines.append("  this is a bug in app/, not a tolerance to widen")
        return "\n".join(lines)


class Replay:
    """Drives one simulated day at the app over HTTP."""

    def __init__(self, base_url: str, params: Params, *, speed: float = DEFAULT_SPEED):
        """`speed` compresses the day; 0 means as fast as the app will take it.

        A paced replay is what a demo needs and what makes the timing
        comparison meaningful. An unpaced one is what a check wants: the rules
        the app applies do not depend on how fast the orders arrive, and eight
        minutes of CI to re-learn that is eight minutes nobody spends.
        """
        self.base_url = base_url.rstrip("/")
        self.params = params
        self.speed = speed
        self.placed: dict[str, dict] = {}

    def _script(self, result: RunResult) -> list[tuple[float, str, str]]:
        """(virtual time, order id, state) for every move the day made."""
        moves = [
            (event.t_s, event.order_id, event.to_state)
            for event in result.log
            if event.type is EventType.STATE_CHANGE
            and event.order_id is not None
            and event.to_state is not None
        ]
        moves.sort(key=lambda move: move[0])
        return moves

    async def play(self, result: RunResult, *, on_error=None) -> int:
        """Replay a day, keeping the app's own pace proportional to the sim's."""
        moves = self._script(result)
        if not moves:
            return 0

        opened_at = moves[0][0]
        wall_start = time.monotonic()
        failures = 0

        async with httpx.AsyncClient(base_url=self.base_url, timeout=30.0) as client:
            for virtual_s, order_id, to_state in moves:
                if self.speed > 0:
                    due = (virtual_s - opened_at) / self.speed
                    delay = due - (time.monotonic() - wall_start)
                    if delay > 0:
                        await asyncio.sleep(delay)

                order = result.orders[order_id]
                try:
                    if to_state == State.PLACED:
                        await self._place(client, order)
                    else:
                        await self._move(client, order_id, to_state)
                except Exception as exc:  # a replay failure is data, not a crash
                    failures += 1
                    if on_error is not None:
                        on_error(order_id, to_state, exc)
        return failures

    async def _place(self, client: httpx.AsyncClient, order) -> None:
        body = {
            "lines": [
                {
                    "drink": item.drink,
                    "milk_type": item.milk_type,
                    "variant": item.variant,
                }
                for item in order.items
            ],
            "channel": str(order.channel),
            "customer_id": order.customer_id,
        }
        response = await client.post("/orders", json=body, headers=SIMULATED)
        response.raise_for_status()
        self.placed[order.order_id] = response.json()

    async def _move(self, client: httpx.AsyncClient, order_id: str, to_state: str) -> None:
        known = self.placed.get(order_id)
        if known is None:
            return
        response = await client.post(
            f"/orders/{known['order_id']}/transition",
            json={"to": to_state, "actor": "replay"},
        )
        response.raise_for_status()


# --------------------------------------------------------------------------
# the drift check
# --------------------------------------------------------------------------


def _compare_prices(params: Params, result: RunResult, replay: Replay) -> list[Mismatch]:
    """What the app charged and costed, against what core says it should be."""
    model = StationCapacityModel(params)
    mismatches: list[Mismatch] = []

    for order_id, served in replay.placed.items():
        order = result.orders[order_id]
        if served["price_cents"] != order.price_cents:
            mismatches.append(Mismatch(f"{order_id} price_cents", order.price_cents, served["price_cents"]))

        expected_cost = round(model.order_cost(order), 6)
        actual_cost = round(float(served["bottleneck_cost_s"]), 6)
        if expected_cost != actual_cost:
            mismatches.append(Mismatch(f"{order_id} bottleneck_cost_s", expected_cost, actual_cost))

        expected_items = [(item.drink, item.milk_type, item.variant) for item in order.items]
        actual_items = [
            (item["drink"], item["milk_type"], item.get("variant")) for item in served["items"]
        ]
        if expected_items != actual_items:
            mismatches.append(Mismatch(f"{order_id} items", expected_items, actual_items))
    return mismatches


def _compare_metrics(
    sim_log: EventLog, app_log: EventLog, speed: float, *, compare_waits: bool = True
) -> list[Mismatch]:
    """The same metric functions, run over both logs."""
    mismatches: list[Mismatch] = []

    sim_counts = state_counts(sim_log)
    app_counts = state_counts(app_log)
    for state in sorted(set(sim_counts) | set(app_counts)):
        if sim_counts.get(state, 0) != app_counts.get(state, 0):
            mismatches.append(
                Mismatch(f"state_counts[{state}]", sim_counts.get(state, 0), app_counts.get(state, 0))
            )

    sim_lost = balk_count_and_lost_margin(sim_log)
    app_lost = balk_count_and_lost_margin(app_log)
    for key in ("balked", "abandoned", "lost"):
        if sim_lost[key] != app_lost[key]:
            mismatches.append(Mismatch(f"lost[{key}]", sim_lost[key], app_lost[key]))

    # Waits are wall-clock on the app's side and rescale by the replay speed.
    # Only meaningful when the replay was actually paced.
    if not compare_waits or speed <= 0:
        return mismatches

    sim_waits = wait_percentiles(sim_log)
    app_waits = wait_percentiles(app_log)
    for key in ("p50", "p90"):
        expected, actual = sim_waits[key], app_waits[key]
        if expected is None or actual is None:
            continue
        rescaled = actual * speed
        if expected > 0 and abs(rescaled - expected) / expected > WAIT_TOLERANCE:
            mismatches.append(
                Mismatch(f"wait {key} (rescaled)", round(expected, 1), round(rescaled, 1))
            )
    return mismatches


async def _drift(
    base_url: str, params: Params, seed: int, speed: float, compare_waits: bool
) -> DriftReport:
    result = run(params, seed)
    replay = Replay(base_url, params, speed=speed)

    failures: list[str] = []
    await replay.play(result, on_error=lambda o, s, e: failures.append(f"{o}->{s}: {e}"))

    report = DriftReport(
        scenario=result.scenario, seed=seed, orders=len(replay.placed)
    )
    report.mismatches += [Mismatch("replay refused", "accepted", failure) for failure in failures[:5]]
    report.mismatches += _compare_prices(params, result, replay)

    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        traces = await asyncio.gather(
            *(client.get(f"/orders/{served['order_id']}") for served in replay.placed.values())
        )

    rows: list[dict] = []
    for response, (order_id, served) in zip(traces, replay.placed.items()):
        response.raise_for_status()
        body = response.json()
        expected = [
            event.to_state
            for event in result.orders[order_id].history
            if event.type is EventType.STATE_CHANGE
        ]
        actual = [event["to_state"] for event in body["events"]]
        if expected != actual:
            report.mismatches.append(Mismatch(f"{order_id} state path", expected, actual))

        for event in body["events"]:
            rows.append(
                {
                    "seq": event["seq"],
                    "event_id": event["event_id"],
                    "t_s": event["t_s"],
                    "type": event["type"],
                    "order_id": body["order_id"],
                    "from_state": event["from_state"],
                    "to_state": event["to_state"],
                    "actor": event["actor"],
                    "channel": body["channel"],
                    "is_simulated": body["is_simulated"],
                    "payload": event["payload"],
                }
            )

    rows.sort(key=lambda row: row["seq"])
    report.mismatches += _compare_metrics(
        result.log, EventLog.from_rows(rows), speed, compare_waits=compare_waits
    )
    return report


def drift_check(
    base_url: str,
    params: Params,
    *,
    seed: int = 0,
    speed: float = 0.0,
    compare_waits: bool = False,
) -> DriftReport:
    """Replay a day at the app and compare it against the in-process run.

    Unpaced and structural by default: prices, bottleneck costs, item lists,
    state paths, state counts and lost margin have to agree exactly, and none
    of them depend on how fast the replay went. Pass a `speed` and
    `compare_waits` to add the timing comparison, which needs a paced replay
    and takes as long as the day divided by the speed.
    """
    return asyncio.run(_drift(base_url, params, seed, speed, compare_waits))


# --------------------------------------------------------------------------
# the concurrency check
# --------------------------------------------------------------------------


def check_slot_concurrency(base_url: str, *, attempts: int = 50) -> dict:
    """Fire many reservations at one slot at once and count the winners.

    The capacity check and the decrement have to be one transaction. Split into
    a read and a write they interleave, and a slot with room for ten takes
    thirty bookings — which nobody notices until the pickup shelf is full of
    drinks nobody can make.
    """
    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        slots = client.get("/slots").json()
        if not slots["enabled"]:
            raise RuntimeError("slots are disabled; enable them to check this")
        slot = next((s for s in slots["slots"] if s["bookable"]), None)
        if slot is None:
            raise RuntimeError(
                f"no bookable slot today: the cafe's day is over, or every window is "
                f"inside the {slots['min_lead_time_min']} minute lead time. Run this "
                f"with a params overlay that widens meta.sim_start / sim_end."
            )

    body = {
        "lines": [{"drink": "latte", "milk_type": "oat", "variant": "hot"}],
        "channel": str(Channel.PREORDER),
        "slot_id": slot["slot_id"],
    }

    outcomes: list[int] = []
    lock = threading.Lock()

    def attempt() -> None:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            status = client.post("/orders", json=body, headers=SIMULATED).status_code
        with lock:
            outcomes.append(status)

    threads = [threading.Thread(target=attempt) for _ in range(attempts)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    with httpx.Client(base_url=base_url, timeout=30.0) as client:
        after = next(
            s for s in client.get("/slots").json()["slots"] if s["slot_id"] == slot["slot_id"]
        )

    placed = sum(1 for status in outcomes if status == 201)
    return {
        "slot_id": slot["slot_id"],
        "attempts": attempts,
        "placed": placed,
        "refused": attempts - placed,
        "room_for": int(slot["remaining_s"] // 38.0) if slot["remaining_s"] else 0,
        "used_s": after["used_s"],
        "capacity_s": after["preorder_capacity_s"],
        "overbooked": after["used_s"] > after["preorder_capacity_s"] + 1e-6,
    }


# --------------------------------------------------------------------------
# running a server to talk to
# --------------------------------------------------------------------------


class serve:
    """Start the app in this process, for a check that needs no deployment."""

    def __init__(self, port: int | None = None):
        self.port = port or free_port()
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._server = None
        self._thread = None

    def __enter__(self) -> str:
        import uvicorn

        from app.main import create_app

        self._server = uvicorn.Server(
            uvicorn.Config(create_app(), host="127.0.0.1", port=self.port, log_level="error")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

        deadline = time.monotonic() + 15
        while not self._server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        if not self._server.started:
            raise RuntimeError("the app did not start")
        return self.base_url

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15)


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay a day at the app over HTTP.")
    parser.add_argument("--params", nargs="+", default=["params/base.yaml"])
    parser.add_argument("--url", default=None, help="a running app; one is started if omitted")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--speed", type=float, default=0.0,
        help="compress the day by this much; 0 replays as fast as the app allows",
    )
    parser.add_argument("--drift-check", action="store_true", help="the default")
    parser.add_argument(
        "--compare-waits", action="store_true",
        help="also compare timings, which needs a paced --speed and takes longer",
    )
    parser.add_argument("--concurrency", action="store_true")
    args = parser.parse_args()

    params = load_params(*args.params)

    def work(base_url: str) -> int:
        failed = False
        if args.concurrency:
            outcome = check_slot_concurrency(base_url)
            print(json.dumps(outcome, indent=2))
            failed |= outcome["overbooked"]

        report = drift_check(
            base_url, params, seed=args.seed, speed=args.speed,
            compare_waits=args.compare_waits,
        )
        print(report.render())
        return 1 if (failed or not report.ok) else 0

    if args.url:
        raise SystemExit(work(args.url))
    with serve() as base_url:
        raise SystemExit(work(base_url))


if __name__ == "__main__":
    main()
