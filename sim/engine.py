"""
This is the discrete-event harness, built on the same core/ the app uses. It
runs in virtual time, so a simulated day takes milliseconds because the clock
jumps from event to event, and that is the whole reason the experiments cannot
go through HTTP. The modelling decision that matters most is here: a drink
needs a barista and a station, both. With two baristas and one steam wand the
wand contends and the baristas block, and in a small cafe the same human takes
the order and pulls the shot, so the register competes for barista time even
when it is not the nominal bottleneck. Modelling stations as independent
parallel servers would invalidate every result the project produces. Run it
with python -m sim.engine for a single day, or import run_day from the
experiment and forecast runners.
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import simpy

from core.capacity import StationCapacityModel
from core.events import EventLog, EventType
from core.menu import make_order
from core.params import SECONDS_PER_MINUTE, Params, load_params
from core.states import LOST, State, place, promise, transition
from core.types import Channel, Item, Order, Task, TaskKind
from sim.arrivals import Arrival, generate_arrivals
from sim.balking import (
    estimate_wait_s,
    nominal_seconds_per_order,
    observable_queue_depth,
)
from core.policies import Policy, make_policy
from sim.policies import Pending

__all__ = ["Barista", "Cafe", "RunResult", "run", "event_path"]

REGISTER = "register"


class Barista:
    """
    One worker.

    A barista is held for the whole of a piece of work and seizes stations from
    inside that hold, so they are never parallel with themselves. Baristas come
    from a pool sized by the staffing plan; going off shift means being taken
    out of the pool, not disappearing mid-drink.
    """

    def __init__(self, cafe: "Cafe", index: int) -> None:
        self.cafe = cafe
        self.name = f"barista-{index}"

    def __repr__(self) -> str:
        return self.name

    def work(self, task: Task, order: Order, *, station: str | None = None, kind: str = "prep"):
        """Do one timed piece of work, seizing the station if there is one."""
        env, cafe = self.cafe.env, self.cafe
        station_name = station if station is not None else task.station

        if station_name is None:                      # assembly: hands only
            cafe.emit_station(EventType.STATION_START, order, task, self, None, kind)
            yield env.timeout(task.duration_s)
            cafe.emit_station(EventType.STATION_END, order, task, self, None, kind)
            return

        resource = cafe.stations[station_name]
        with resource.request() as slot:
            yield slot
            cafe.emit_station(EventType.STATION_START, order, task, self, station_name, kind)
            yield env.timeout(task.duration_s)
            cafe.emit_station(EventType.STATION_END, order, task, self, station_name, kind)


@dataclass
class Cafe:
    """
    Resources, staffing, and the service process.

    Everything it knows about drinks it asks `core/` for: what work an item
    implies, how long it takes, and which state moves are legal.
    """

    env: simpy.Environment
    params: Params
    rng: np.random.Generator
    log: EventLog

    stations: dict[str, simpy.Resource] = field(init=False)
    crew: simpy.Store = field(init=False)
    orders: dict[str, Order] = field(init=False, default_factory=dict)
    bench: list[Barista] = field(init=False, default_factory=list)
    policy: Policy = field(init=False)
    queues: dict[str, list[Pending]] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.stations = {
            name: simpy.Resource(self.env, capacity=self.params.station_capacity(name))
            for name in self.params.stations
        }
        self.capacity_model = StationCapacityModel(self.params)

        # The crew is a pool of identified workers rather than an anonymous
        # counter, so the log can say which barista did what.
        self.bench = [Barista(self, index) for index in range(self.params.max_baristas)]
        self.crew = simpy.Store(self.env, capacity=self.params.max_baristas)
        self.orders = {}

        # Stations that can run several items at once get a queue and a server
        # that decides what to run together. Everything else is worked directly
        # by whoever is holding the order.
        self.policy = make_policy(self.params)
        self._nominal: dict[int, float] = {}
        self.grouping = [
            name for name, station in self.params.stations.items() if station.groups_work
        ]
        self.queues = {name: [] for name in self.grouping}
        self._waiting = {name: self.env.event() for name in self.grouping}

    # ---- staffing ------------------------------------------------------

    def staffing(self):
        """
        Track the staffing plan by holding spare workers off the floor.

        Off-shift baristas are taken out of the pool the moment they are free;
        nobody is pulled off a drink they are already making.
        """
        held: list[Barista] = []
        for block in sorted(self.params.staffing, key=lambda b: b.from_s):
            if self.env.now < block.from_s:
                yield self.env.timeout(block.from_s - self.env.now)

            while len(self.bench) - len(held) < block.baristas:
                self.bench.append(Barista(self, len(self.bench)))
            wanted = block.baristas

            while len(held) > self.params.max_baristas - wanted:
                self.crew.put(held.pop())
            while len(held) < self.params.max_baristas - wanted:
                held.append((yield self.crew.get()))

    def open_the_doors(self) -> None:
        for barista in self.bench:
            self.crew.put(barista)
        self.env.process(self.staffing())
        for name in self.grouping:
            self.env.process(self.station_server(name))

    # ---- events --------------------------------------------------------

    def emit_station(
        self,
        kind: EventType,
        order: Order,
        task: Task,
        barista: Barista,
        station: str | None,
        label: str,
        **payload: object,
    ) -> None:
        """
        Record a span of station work.

        `attended` is the part that matters downstream: a press cycle occupies
        the press but not a person, so machine utilisation and crew utilisation
        are different questions and the log has to answer both.
        """
        attended = station is None or self.params.station(station).attended
        self.log.emit(
            kind,
            self.env.now,
            order_id=order.order_id,
            item_id=task.item_id,
            customer_id=order.customer_id,
            station=station,
            actor=barista.name,
            channel=order.channel,
            payload={
                "kind": label,
                "duration_s": task.duration_s,
                "attended": attended,
                **payload,
            },
        )

    # ---- the service process ------------------------------------------

    def submit(self, station: str, task: Task, order: Order, item: Item) -> Pending:
        """Hand a piece of work to a station's queue and get a receipt."""
        entry = Pending(
            task=task,
            order=order,
            item=item,
            station=station,
            submitted_at=self.env.now,
            done=self.env.event(),
        )
        self.queues[station].append(entry)
        waiting = self._waiting[station]
        if not waiting.triggered:
            waiting.succeed()
        return entry

    def station_server(self, name: str):
        """
        Run one batching station for the day.

        Takes a person first and the machine second, like everything else here,
        and asks the policy what to run together. `core.capacity` prices the
        batch, so a press cycle costs the same whether it holds one sandwich or
        three, which is the entire point of running three.
        """
        station = self.params.station(name)
        resource = self.stations[name]
        model = StationCapacityModel(self.params, name)
        last_key: str | None = None

        while True:
            if not self.queues[name]:
                self._waiting[name] = self.env.event()
                yield self._waiting[name]
                continue

            barista = None
            if station.attended:
                barista = yield self.crew.get()

            with resource.request() as slot:
                yield slot
                batch = self.policy.next_batch(name, self.queues[name], self.env.now)
                for entry in batch:
                    self.queues[name].remove(entry)

                items = [entry.item for entry in batch]
                duration = model.batch_cost(items)

                # Switching the station between batch keys costs something, and
                # it is paid by whoever runs the batch that changes it. This is
                # sequence-dependent, so it lives here and not in the capacity
                # model, which prices a batch on its own.
                key = model.batch_value(items[0])
                changeover_s = 0.0
                if station.changeover_s and last_key is not None and key != last_key:
                    changeover_s = station.changeover_s
                    duration += changeover_s
                last_key = key

                actor = barista.name if barista is not None else name
                self.emit_batch(
                    EventType.STATION_START, name, batch, actor, duration,
                    changeover_s=changeover_s,
                )
                if len(batch) > 1:
                    self.emit_batch(EventType.BATCH_FORMED, name, batch, actor, duration)
                yield self.env.timeout(duration)
                self.emit_batch(EventType.STATION_END, name, batch, actor, duration)

            if barista is not None:
                self.crew.put(barista)
            for entry in batch:
                entry.done.succeed()

    def emit_batch(
        self,
        kind: EventType,
        station: str,
        batch: list[Pending],
        actor: str,
        duration_s: float,
        **extra: object,
    ) -> None:
        """
        One event for the whole batch, keyed on its first item so the start
        and end pair up."""
        head = batch[0]
        self.log.emit(
            kind,
            self.env.now,
            order_id=head.order.order_id,
            item_id=head.item.item_id,
            customer_id=head.order.customer_id,
            station=station,
            batch_id=f"{station}-{self.log.next_id()[0]}",
            actor=actor,
            channel=head.order.channel,
            payload={
                "kind": "batch",
                "attended": self.params.station(station).attended,
                "duration_s": duration_s,
                "size": len(batch),
                "items": [entry.item.item_id for entry in batch],
                "orders": sorted({entry.order.order_id for entry in batch}),
                **extra,
            },
        )

    def unattended(self, task: Task, order: Order, item: Item, starter: "Barista"):
        """
        A machine cycle that needs a slot but nobody to watch it.

        Run as its own process so the barista is free the whole time. No
        try/finally: a generator that yields from `finally` cannot be closed,
        and the harness closes every unfinished order at the cut-off.
        """
        with self.stations[task.station].request() as slot:
            yield slot
            self.emit_station(
                EventType.STATION_START, order, task, starter, task.station, "machine",
            )
            yield self.env.timeout(task.duration_s)
            self.emit_station(
                EventType.STATION_END, order, task, starter, task.station, "machine",
            )

    def make(self, order: Order, barista: Barista):
        """
        Work the order's items, overlapping whatever a person is not needed for.

        A barista does not stand and watch an extraction. They start the shot,
        steam the milk while it pulls, and pour when both are done, so a latte
        costs `max(shot, steam) + pour`, not the sum. Working strictly in series
        priced a latte at 112 seconds against a sandwich's 90 and made drinks
        the slower half of the menu, which is the wrong way round from what the
        cafe does.

        So: everything needing no person starts at once and runs in the
        background (the group head, and the oven), which is why a sandwich heats
        while the coffee gets made. Attended work is done in series by the one
        barista holding the order, because a person cannot steam and pour at the
        same time. Assembly waits for its own item's machines and nothing else.

        Resources are still taken in the same order, a person and then a
        machine, never the other way round. The background processes only ever take a
        machine, so they cannot be holding one while the foreground waits for a
        person: that cycle is what deadlocked a busy cafe once already.

        Returns whoever is holding the order at the end, which need not be
        whoever started it.
        """
        started = self.env.now
        machines: dict[str, list] = {item.item_id: [] for item in order.items}
        attended: list[tuple[Item, Task]] = []
        assembly: list[tuple[Item, Task]] = []

        for item in order.items:
            for task in item.tasks:
                station = (
                    self.params.station(task.station) if task.station is not None else None
                )
                if station is None or task.kind is TaskKind.ASSEMBLY:
                    assembly.append((item, task))
                elif station.groups_work:
                    # The station's server decides what runs with what; standing
                    # aside for it is the whole point of batching.
                    machines[item.item_id].append(
                        self.submit(task.station, task, order, item).done
                    )
                elif station.attended:
                    attended.append((item, task))
                else:
                    machines[item.item_id].append(
                        self.env.process(self.unattended(task, order, item, barista))
                    )

        for _item, task in attended:
            yield from barista.work(task, order, kind=str(task.kind))

        for item, task in assembly:
            waiting = machines.get(item.item_id) or []
            if waiting:
                # Hand the crew back before waiting on machines, never after.
                self.crew.put(barista)
                barista = None
                for event in waiting:
                    yield event
                machines[item.item_id] = []
                barista = yield self.crew.get()
            yield from barista.work(task, order, kind=str(task.kind))
            self.item_ready(order, item, started)

        # An item with no assembly step still has to finish its machines.
        for item in order.items:
            waiting = machines.get(item.item_id) or []
            if not waiting:
                continue
            self.crew.put(barista)
            barista = None
            for event in waiting:
                yield event
            machines[item.item_id] = []
            barista = yield self.crew.get()
            self.item_ready(order, item, started)
        return barista

    def item_ready(self, order: Order, item: Item, started_s: float) -> None:
        """
        This item is on the shelf, whatever the rest of the order is doing.

        Watched behaviour: a drink goes out when it is poured and the sandwich
        follows when the oven is done. The order is not ready until its last
        item is, but the customer has had part of it for a while by then, and
        an app that says "#42 is ready" once cannot describe that.
        """
        self.log.emit(
            EventType.STATION_END,
            self.env.now,
            order_id=order.order_id,
            item_id=item.item_id,
            actor="bar",
            station="shelf",
            payload={
                "kind": "item_ready",
                "drink": item.drink,
                "since_start_s": self.env.now - started_s,
            },
        )

    def serve(self, order: Order, arrival: Arrival):
        """One placed order, from the register to the handoff shelf."""
        # The register is priced by the capacity model rather than read off one
        # field, so the published "transaction plus a few seconds an item"
        # shape applies here and in slot accounting alike.
        register_task = Task(
            item_id=order.items[0].item_id,
            order_id=order.order_id,
            station=REGISTER,
            kind=TaskKind.PREP,
            duration_s=StationCapacityModel(self.params, REGISTER).order_cost(order),
        )

        # Somebody who ordered on their phone does not use the till. They chose,
        # paid and left before they got here, so they spend no register seconds
        # and no barista's time taking them, which is the entire mechanism the
        # app has. Charging them for it had every pre-order queueing twice.
        #
        # It matters more than a transaction's worth of seconds, because part of
        # `base_s` is deciding rather than paying, and deciding is what varies
        # between people. The register is a decision point that happens to have
        # a card reader on it, and this is what moves the deciding off it.
        if arrival.channel is Channel.PREORDER:
            transition(order, State.ACCEPTED, at=self.env.now, actor="app", log=self.log)
        else:
            yield from self.take_the_order(order, arrival, register_task)
            if order.state is not State.ACCEPTED:
                return

        barista = yield self.crew.get()
        try:
            transition(order, State.IN_PROGRESS, at=self.env.now, actor=barista.name, log=self.log)
            barista = yield from self.make(order, barista)
            transition(order, State.READY, at=self.env.now, actor=barista.name, log=self.log)
        finally:
            # `make` hands the crew back itself while a machine runs; it only
            # returns holding nobody if the day was cut off mid-cycle
            if barista is not None:
                self.crew.put(barista)

        yield from self.hand_over(order, arrival)

    def hand_over(self, order: Order, arrival: Arrival):
        """
        Getting the drink to the person, or discovering they have gone.

        Someone who ordered ahead collects at the time they asked for, so a
        pre-order made early sits on the shelf rather than being handed to
        nobody. A walk-up whose drink finally appears after their time budget
        has run out has already left for class; the cafe made it anyway, which
        is precisely the cost.
        """
        if arrival.wanted_at_s is not None and arrival.wanted_at_s > self.env.now:
            yield self.env.timeout(arrival.wanted_at_s - self.env.now)

        if arrival.no_show:
            transition(
                order, State.ABANDONED, at=self.env.now, actor="customer", log=self.log,
                reason="no_show", margin_cents=order.margin_cents,
            )
            return

        # Nothing is checked against the time budget here any more. By this
        # point the customer has been through the register, and somebody who
        # has paid does not walk away from a drink on the shelf in front of
        # them. What can still go wrong is a pre-order nobody comes back for,
        # which is `no_show` above.
        transition(order, State.PICKED_UP, at=self.env.now, actor="customer", log=self.log)

    def admit(self, arrival: Arrival):
        """Wait for the customer, then start serving them."""
        if arrival.at_s > self.env.now:
            yield self.env.timeout(arrival.at_s - self.env.now)

        # A pre-order under adaptive release has been paid for but not yet
        # placed. `at_s` is when they committed; this is the wait until the line
        # says it is time.
        if (
            arrival.preordered
            and arrival.wanted_at_s is not None
            and self.params.customers.preorder_release == "adaptive"
        ):
            yield from self.hold_until_release(arrival)

        if self.defers(arrival):
            yield self.env.timeout(
                self.params.customers.shift_delay_min * SECONDS_PER_MINUTE
            )

        self.log.emit(
            EventType.ARRIVAL,
            self.env.now,
            order_id=arrival.order_id,
            customer_id=arrival.customer_id,
            channel=arrival.channel,
            actor="customer",
            payload={"source": arrival.source, "lines": arrival.size},
        )

        order = make_order(
            arrival.order_id,
            self.params,
            lines=list(arrival.lines),
            channel=arrival.channel,
            placed_at_s=self.env.now,
            customer_id=arrival.customer_id,
            is_simulated=True,
        )
        self.orders[order.order_id] = order
        place(
            order, at=self.env.now, actor="customer", log=self.log,
            price_cents=order.price_cents,
            margin_cents=order.margin_cents,
            items=[item.drink for item in order.items],
        )

        if arrival.preordered and arrival.wanted_at_s is not None:
            promise(order, at=self.env.now, promised_at_s=arrival.wanted_at_s, log=self.log)

        if self.balks(arrival, order):
            return

        yield self.env.process(self.serve(order, arrival))

    def hold_until_release(self, arrival: Arrival):
        """
        Keep a paid pre-order back, and join the queue at the last safe moment.

        The customer chose a time and paid; nothing has reached the bar yet. The
        quote that got them here came off a forecast, but by the time it matters
        the live line is knowable, so this re-decides against that instead of
        committing to the prediction.

        Release when ordering now would land at the wanted time, plus a margin
        of slack. Hold no later than the wanted time itself: a held order that
        never releases is worse than a late one, and a line that grows faster
        than the poll can follow has to be given up on rather than waited out.

        This is what a pushed reminder cannot do. Once somebody has been told to
        order, they cannot be re-timed.
        """
        wanted = arrival.wanted_at_s
        poll = self.params.customers.release_poll_s
        margin = self.params.customers.release_margin_s
        while self.env.now < wanted:
            depth = observable_queue_depth(
                other.state for other in self.orders.values()
            )
            estimate = estimate_wait_s(depth, self.nominal_wait_per_person())
            if self.env.now + estimate + margin >= wanted:
                return
            yield self.env.timeout(min(poll, wanted - self.env.now))

    def defers(self, arrival: Arrival) -> bool:
        """
        Does this customer see the wait and decide to come back later?

        Only walk-ups, only once, and only if the cafe is showing a number at
        all. Unlike a balk this is deferred revenue rather than lost revenue,
        and that is exactly why it is worth something even where nobody balks.
        """
        rules = self.params.customers
        if not rules.shown_wait or arrival.channel is not Channel.WALKUP:
            return False
        if arrival.defer_roll >= rules.shift_fraction:
            return False

        depth = observable_queue_depth(order.state for order in self.orders.values())
        shown_s = estimate_wait_s(depth, self.nominal_wait_per_person())
        if shown_s <= rules.shift_threshold_min * SECONDS_PER_MINUTE:
            return False

        self.log.emit(
            EventType.ARRIVAL,
            self.env.now,
            customer_id=arrival.customer_id,
            order_id=arrival.order_id,
            channel=arrival.channel,
            actor="customer",
            payload={
                "deferred": True,
                "shown_wait_s": round(shown_s, 1),
                "queue_depth": depth,
                "by_min": rules.shift_delay_min,
            },
        )
        return True

    def nominal_wait_per_person(self) -> float:
        """
        Cached per staffing level: it walks the whole menu to work out what
        an average order is worth."""
        baristas = self.params.baristas_at(self.env.now)
        if baristas not in self._nominal:
            self._nominal[baristas] = nominal_seconds_per_order(self.params, baristas)
        return self._nominal[baristas]

    def take_the_order(self, order: Order, arrival: Arrival, register_task: Task):
        """
        A walk-up at the till: waiting for a free barista, then ordering.

        Patience is spent here and nowhere after it. Reach the register and you
        stay, because you have paid and the drink is being made in front of you.
        Spending it later instead had the model shedding ~77 customers a day at
        a cafe where watching produced one.
        """
        waiting = self.crew.get()
        if math.isfinite(arrival.time_budget_s):
            left_s = arrival.time_budget_s - (self.env.now - order.placed_at_s)
            if left_s <= 0:
                waiting.cancel()
                self.renege(order, arrival)
                return
            outcome = yield waiting | self.env.timeout(left_s)
            if waiting not in outcome:
                waiting.cancel()
                self.renege(order, arrival)
                return
            barista: Barista = outcome[waiting]
        else:
            barista = yield waiting

        # The crew is never handed back with `yield`: a generator that yields
        # from `finally` cannot be closed, and the harness closes every
        # unfinished order when the day is cut off at closing time.
        try:
            yield from barista.work(register_task, order, station=REGISTER, kind="register")
            transition(order, State.ACCEPTED, at=self.env.now, actor=barista.name, log=self.log)
        finally:
            self.crew.put(barista)

    def renege(self, order: Order, arrival: Arrival) -> None:
        """
        A walk-up who gave up in the line before ever reaching the till.

        Distinct from balking, which is a decision taken on arrival by looking
        at the queue. This one joined it and then ran out of time. The cafe
        never took the order, so unlike the old accounting there is no drink
        made for somebody who has already gone.
        """
        transition(
            order, State.ABANDONED, at=self.env.now, actor="customer", log=self.log,
            reason="left_the_line",
            waited_s=self.env.now - order.placed_at_s,
            time_budget_s=arrival.time_budget_s,
            margin_cents=order.margin_cents,
        )

    def balks(self, arrival: Arrival, order: Order) -> bool:
        """
        Does this customer look at the line and leave?

        Someone who ordered ahead has already committed and never balks, which
        is the whole of the pre-order case. The estimate is recorded on the
        event because the balk count and the margin behind it are the revenue
        argument, and both have to be readable from the log alone.
        """
        if arrival.channel is not Channel.WALKUP:
            return False

        # the line they are looking at, not counting themselves
        depth = observable_queue_depth(
            other.state
            for order_id, other in self.orders.items()
            if order_id != order.order_id
        )
        estimate_s = estimate_wait_s(depth, self.nominal_wait_per_person())
        if estimate_s <= arrival.balk_tolerance_s:
            return False

        transition(
            order, State.BALKED, at=self.env.now, actor="customer", log=self.log,
            estimated_wait_s=estimate_s,
            queue_depth=depth,
            tolerance_s=arrival.balk_tolerance_s,
            margin_cents=order.margin_cents,
        )
        return True


@dataclass
class RunResult:
    """One simulated day."""

    params: Params
    scenario: str
    seed: int
    log: EventLog
    arrivals: list[Arrival]
    orders: dict[str, Order]
    until_s: float

    def census(self) -> dict[str, int]:
        """
        The conservation identity, as counts.

        placed == picked_up + balked + abandoned + cancelled + in_flight
        """
        counts: dict[str, int] = {str(state): 0 for state in State}
        for order in self.orders.values():
            counts[str(order.state)] += 1

        terminal = counts[State.PICKED_UP] + sum(counts[str(state)] for state in LOST)
        in_flight = len(self.orders) - terminal
        return {
            "placed": len(self.orders),
            "picked_up": counts[State.PICKED_UP],
            "balked": counts[State.BALKED],
            "abandoned": counts[State.ABANDONED],
            "cancelled": counts[State.CANCELLED],
            "in_flight_at_end": in_flight,
        }

    def conserved(self) -> bool:
        census = self.census()
        return census["placed"] == (
            census["picked_up"]
            + census["balked"]
            + census["abandoned"]
            + census["cancelled"]
            + census["in_flight_at_end"]
        )


def event_path(scenario: str, seed: int, out_dir: str | Path = "out") -> Path:
    return Path(out_dir) / f"events_{scenario}_{seed}.parquet"


def run(
    params: Params,
    seed: int | None = None,
    *,
    scenario: str | None = None,
    until_s: float | None = None,
    arrivals: list[Arrival] | None = None,
) -> RunResult:
    """
    Simulate one day.

    One generator, created here and passed down, is the whole of the randomness
    (ground rule 5): same params and same seed give a byte-identical log.
    """
    seed = params.meta.seed if seed is None else seed
    scenario = scenario or params.meta.scenario
    until = float(params.meta.end_s if until_s is None else until_s)

    rng = np.random.default_rng(seed)
    log = EventLog(
        scenario=scenario, seed=seed, is_simulated=True, policy=params.policy.name
    )
    env = simpy.Environment(initial_time=float(params.meta.start_s))

    cafe = Cafe(env=env, params=params, rng=rng, log=log)
    cafe.open_the_doors()

    demand = generate_arrivals(params, rng) if arrivals is None else list(arrivals)
    for arrival in demand:
        env.process(cafe.admit(arrival))

    env.run(until=until)

    return RunResult(
        params=params,
        scenario=scenario,
        seed=seed,
        log=log,
        arrivals=demand,
        orders=cafe.orders,
        until_s=until,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate one cafe day.")
    parser.add_argument("--params", nargs="+", default=["params/base.yaml"])
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--scenario", default=None)
    parser.add_argument("--out", default="out")
    args = parser.parse_args()

    params = load_params(*args.params)
    result = run(params, args.seed, scenario=args.scenario)
    path = result.log.write_parquet(event_path(result.scenario, result.seed, args.out))

    census = result.census()
    print(f"{result.scenario} seed={result.seed}  {params.provenance_report().caption()}")
    print(f"  arrivals {len(result.arrivals)}  events {len(result.log)}  -> {path}")
    print("  " + "  ".join(f"{key}={value}" for key, value in census.items()))
    print(f"  conserved: {result.conserved()}  digest {result.log.digest()[:16]}")


if __name__ == "__main__":
    main()
