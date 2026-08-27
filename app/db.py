"""Storage. Tables, the event store, and slot capacity accounting.

`app/` holds no domain rules (ground rule 2). These tables persist what
`core/` decides: prices come from the menu resolution, legal moves from the
state machine, and bottleneck cost from the capacity model.

`order_events` is append-only and is written by `core.states` alone, through
`DbEventLog` (ground rule 4).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from sqlalchemy import Column, UniqueConstraint, event as sa_event, text, update
from sqlalchemy.engine import Engine
from sqlmodel import JSON, Field, Session, SQLModel, create_engine, select

from app.config import at_local_time, day_seconds, now_utc, service_date, settings
from core.capacity import StationCapacityModel
from core.events import Event, EventLog, EventType
from core.menu import resolve_tasks
from core.params import Params, SECONDS_PER_MINUTE
from core.states import PENDING_SEQ, State
from core.types import Channel, Item, Order

__all__ = [
    "MenuItemRow",
    "OrderRow",
    "OrderItemRow",
    "OrderEventRow",
    "SlotRow",
    "DbEventLog",
    "get_engine",
    "init_db",
    "session_scope",
    "seed_menu",
    "ensure_slots",
    "reserve_slot_capacity",
    "next_order_number",
    "persist_order",
    "load_order",
    "open_orders",
]


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------


class MenuItemRow(SQLModel, table=True):
    """A projection of `params.menu` so the API and reports can join on it.

    Reseeded from params at startup; params stays the authority.
    """

    __tablename__ = "menu_items"

    name: str = Field(primary_key=True)
    price_cents: int
    cogs_cents: int
    requires_milk: bool
    assembly_s: float
    service_s: float                       # hands-on seconds, made alone
    stations: str                          # comma separated, in order
    bottleneck_cost_s: float               # seconds at params.bottleneck_station


class OrderRow(SQLModel, table=True):
    __tablename__ = "orders"
    # the display shows one number per order per day, so it has to be unique
    __table_args__ = (UniqueConstraint("service_date", "number", name="uq_order_number_per_day"),)

    order_id: str = Field(primary_key=True)
    number: int = Field(index=True)         # what the display shows
    service_date: str = Field(index=True)   # local business date, not UTC date
    channel: str
    state: str = Field(index=True)
    is_simulated: bool = Field(default=False, index=True)
    customer_id: str | None = None
    slot_id: str | None = Field(default=None, foreign_key="slots.slot_id", index=True)
    placed_at: datetime                     # UTC
    placed_at_s: float                      # seconds since local midnight
    promised_at_s: float | None = None
    price_cents: int = 0
    margin_cents: int = 0
    bottleneck_cost_s: float = 0.0
    updated_at: datetime


class OrderItemRow(SQLModel, table=True):
    __tablename__ = "order_items"

    item_id: str = Field(primary_key=True)
    order_id: str = Field(foreign_key="orders.order_id", index=True)
    position: int
    drink: str
    milk_type: str | None = None
    price_cents: int
    cogs_cents: int
    requires_milk: bool


class OrderEventRow(SQLModel, table=True):
    """Append-only. Never updated, never deleted.

    `seq` is the database's own monotonic counter, which is what makes the
    stream resumable by Last-Event-ID.
    """

    __tablename__ = "order_events"

    seq: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(default="", index=True)
    t_s: float
    type: str
    order_id: str | None = Field(default=None, index=True)
    item_id: str | None = None
    customer_id: str | None = None
    from_state: str | None = None
    to_state: str | None = None
    station: str | None = None
    batch_id: str | None = None
    actor: str = "system"
    channel: str | None = None
    is_simulated: bool = False
    scenario: str = ""
    wall_ts: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))

    def to_event(self) -> Event:
        return Event(
            seq=self.seq if self.seq is not None else PENDING_SEQ,
            event_id=self.event_id,
            t_s=self.t_s,
            type=EventType(self.type),
            order_id=self.order_id,
            item_id=self.item_id,
            customer_id=self.customer_id,
            from_state=self.from_state,
            to_state=self.to_state,
            station=self.station,
            batch_id=self.batch_id,
            actor=self.actor,
            channel=self.channel,
            is_simulated=self.is_simulated,
            scenario=self.scenario,
            seed=None,
            wall_ts=self.wall_ts,
            payload=self.payload or {},
        )


class SlotRow(SQLModel, table=True):
    """A pickup window and its bottleneck-seconds budget.

    Present from M2 so the capacity plumbing is exercised, but inert while
    `params.slots.enabled` is false: orders go straight to the queue.
    """

    __tablename__ = "slots"

    slot_id: str = Field(primary_key=True)
    service_date: str = Field(index=True)
    starts_at_s: float
    ends_at_s: float
    capacity_s: float                       # bottleneck-seconds the window offers
    preorder_capacity_s: float              # capacity_s minus the walk-up reserve
    used_s: float = 0.0
    is_open: bool = True


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------

_engine: Engine | None = None


def get_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """One engine per process. WAL plus a busy timeout, because SSE readers and
    the writer share the file."""
    global _engine
    if url is None and _engine is not None:
        return _engine

    target = url or settings.db_url
    if target.startswith("sqlite:///") and not target.endswith(":memory:"):
        Path(target.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        target,
        echo=echo,
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @sa_event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):  # pragma: no cover - driver callback
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    if url is None:
        _engine = engine
    return engine


def init_db(engine: Engine | None = None) -> Engine:
    engine = engine or get_engine()
    SQLModel.metadata.create_all(engine)
    return engine


def session_scope(engine: Engine | None = None) -> Session:
    return Session(engine or get_engine())


# --------------------------------------------------------------------------
# the event store
# --------------------------------------------------------------------------


class DbEventLog(EventLog):
    """An EventLog that writes through to `order_events`.

    Events are held in `published` until the caller commits, so the SSE stream
    never announces something a rollback would erase.
    """

    def __init__(self, session: Session, scenario: str = "live", *, is_simulated: bool = False) -> None:
        super().__init__(scenario=scenario, seed=None, is_simulated=is_simulated)
        self.session = session
        self.published: list[Event] = []

    def append(self, event: Event) -> Event:
        row = OrderEventRow(
            t_s=event.t_s,
            type=str(event.type),
            order_id=event.order_id,
            item_id=event.item_id,
            customer_id=event.customer_id,
            from_state=event.from_state,
            to_state=event.to_state,
            station=event.station,
            batch_id=event.batch_id,
            actor=event.actor,
            channel=event.channel,
            is_simulated=event.is_simulated or self.is_simulated,
            scenario=self.scenario,
            wall_ts=event.wall_ts or now_utc(),
            payload=dict(event.payload),
        )
        self.session.add(row)
        self.session.flush()               # assigns seq

        row.event_id = f"{self.scenario}:{row.seq:07d}"
        stamped = event.model_copy(
            update={
                "seq": row.seq,
                "event_id": row.event_id,
                "scenario": self.scenario,
                "seed": None,
                "is_simulated": row.is_simulated,
                "wall_ts": row.wall_ts,
            }
        )
        self.published.append(stamped)
        return stamped

    def emit(self, type: EventType, t_s: float, **fields: Any) -> Event:
        fields.setdefault("is_simulated", self.is_simulated)
        return self.append(
            Event(seq=PENDING_SEQ, event_id="", t_s=t_s, type=type, **fields)
        )


def events_for(session: Session, order_id: str) -> list[Event]:
    rows = session.exec(
        select(OrderEventRow)
        .where(OrderEventRow.order_id == order_id)
        .order_by(OrderEventRow.seq)
    ).all()
    return [row.to_event() for row in rows]


def events_since(session: Session, seq: int, limit: int = 500) -> list[Event]:
    """Replay for a client reconnecting with Last-Event-ID."""
    rows = session.exec(
        select(OrderEventRow)
        .where(OrderEventRow.seq > seq)
        .order_by(OrderEventRow.seq)
        .limit(limit)
    ).all()
    return [row.to_event() for row in rows]


# --------------------------------------------------------------------------
# menu projection
# --------------------------------------------------------------------------


def seed_menu(session: Session, params: Params) -> int:
    """Rewrite the menu projection from params. Idempotent."""
    model = StationCapacityModel(params)
    existing = {row.name: row for row in session.exec(select(MenuItemRow)).all()}

    for name, spec in params.menu.items():
        probe = Item(
            item_id=f"probe-{name}",
            order_id="probe",
            drink=name,
            price_cents=spec.price_cents,
            cogs_cents=spec.cogs_cents,
            requires_milk=spec.requires_milk,
            milk_type=next(iter(params.mix.milk)) if spec.requires_milk else None,
        )
        probe.tasks = resolve_tasks(probe, params)

        row = existing.pop(name, None) or MenuItemRow(name=name, price_cents=0, cogs_cents=0,
                                                      requires_milk=False, assembly_s=0.0,
                                                      service_s=0.0, stations="",
                                                      bottleneck_cost_s=0.0)
        row.price_cents = spec.price_cents
        row.cogs_cents = spec.cogs_cents
        row.requires_milk = spec.requires_milk
        row.assembly_s = spec.assembly_s
        row.service_s = sum(task.duration_s for task in probe.tasks)
        row.stations = ",".join(t.station for t in spec.tasks)
        row.bottleneck_cost_s = model.cost(probe)
        session.add(row)

    for stale in existing.values():         # menu items removed from params
        session.delete(stale)

    session.commit()
    return len(params.menu)


# --------------------------------------------------------------------------
# slots
# --------------------------------------------------------------------------


def slot_id_for(on: date, starts_at_s: float) -> str:
    minutes = int(starts_at_s) // SECONDS_PER_MINUTE
    return f"{on.isoformat()}T{minutes // 60:02d}{minutes % 60:02d}"


def ensure_slots(session: Session, params: Params, on: date) -> list[SlotRow]:
    """Materialise the day's pickup windows and their capacity budgets.

    Capacity is measured in bottleneck-seconds, so if M8 moves
    `bottleneck_station` the budgets follow with no code change.
    """
    existing = {
        row.slot_id: row
        for row in session.exec(
            select(SlotRow).where(SlotRow.service_date == on.isoformat())
        ).all()
    }
    model = StationCapacityModel(params)
    width_s = params.slots.width_min * SECONDS_PER_MINUTE
    reserve = params.slots.walkup_reserve_fraction

    rows: list[SlotRow] = []
    start = float(params.meta.start_s)
    while start < params.meta.end_s:
        end = min(start + width_s, float(params.meta.end_s))
        slot_id = slot_id_for(on, start)
        capacity_s = model.capacity_seconds(end - start, start)
        row = existing.get(slot_id)
        if row is None:
            row = SlotRow(
                slot_id=slot_id,
                service_date=on.isoformat(),
                starts_at_s=start,
                ends_at_s=end,
                capacity_s=capacity_s,
                preorder_capacity_s=capacity_s * (1.0 - reserve),
            )
            session.add(row)
        rows.append(row)
        start = end

    session.commit()
    return rows


def reserve_slot_capacity(session: Session, slot_id: str, cost_s: float) -> bool:
    """Check and decrement in one statement.

    A single conditional UPDATE is atomic, so N concurrent reservations against
    a slot with room for K leave exactly K winners. Splitting this into a read
    then a write is the bug the M9 concurrency test exists to catch.
    """
    result = session.exec(
        update(SlotRow)
        .where(
            SlotRow.slot_id == slot_id,
            SlotRow.is_open == True,  # noqa: E712 - SQL, not Python truthiness
            SlotRow.used_s + cost_s <= SlotRow.preorder_capacity_s,
        )
        .values(used_s=SlotRow.used_s + cost_s)
    )
    return bool(result.rowcount)


def release_slot_capacity(session: Session, slot_id: str, cost_s: float) -> None:
    """Give capacity back when an order is cancelled before it is made."""
    session.exec(
        update(SlotRow)
        .where(SlotRow.slot_id == slot_id)
        .values(used_s=SlotRow.used_s - cost_s)
    )


# --------------------------------------------------------------------------
# orders
# --------------------------------------------------------------------------


def next_order_number(session: Session, on: date) -> int:
    """Daily counter. The display shows numbers, never names (M11)."""
    row = session.exec(
        text("SELECT COALESCE(MAX(number), 0) + 1 FROM orders WHERE service_date = :d").bindparams(
            d=on.isoformat()
        )
    ).one()
    return int(row[0])


def persist_order(
    session: Session,
    order: Order,
    params: Params,
    *,
    number: int,
    on: date,
    bottleneck_cost_s: float,
) -> OrderRow:
    row = OrderRow(
        order_id=order.order_id,
        number=number,
        service_date=on.isoformat(),
        channel=str(order.channel),
        state=str(order.state),
        is_simulated=order.is_simulated,
        customer_id=order.customer_id,
        slot_id=order.slot_id,
        placed_at=at_local_time(params, on, order.placed_at_s),
        placed_at_s=order.placed_at_s,
        promised_at_s=order.promised_at_s,
        price_cents=order.price_cents,
        margin_cents=order.margin_cents,
        bottleneck_cost_s=bottleneck_cost_s,
        updated_at=now_utc(),
    )
    session.add(row)
    # The order row must land before its items: a foreign key column alone does
    # not tell the unit of work which insert comes first.
    session.flush()

    for position, item in enumerate(order.items):
        session.add(
            OrderItemRow(
                item_id=item.item_id,
                order_id=order.order_id,
                position=position,
                drink=item.drink,
                milk_type=item.milk_type,
                price_cents=item.price_cents,
                cogs_cents=item.cogs_cents,
                requires_milk=item.requires_milk,
            )
        )
    return row


def hydrate(row: OrderRow, items: Sequence[OrderItemRow], params: Params) -> Order:
    """Rebuild the domain object, re-resolving station plans from params.

    Prices come from the stored rows (what the customer was charged), while the
    work plan comes from `core.menu` (what the cafe has to do now).
    """
    order = Order(
        order_id=row.order_id,
        channel=Channel(row.channel),
        placed_at_s=row.placed_at_s,
        state=State(row.state),
        customer_id=row.customer_id,
        slot_id=row.slot_id,
        promised_at_s=row.promised_at_s,
        is_simulated=row.is_simulated,
    )
    for item_row in sorted(items, key=lambda i: i.position):
        item = Item(
            item_id=item_row.item_id,
            order_id=item_row.order_id,
            drink=item_row.drink,
            price_cents=item_row.price_cents,
            cogs_cents=item_row.cogs_cents,
            requires_milk=item_row.requires_milk,
            milk_type=item_row.milk_type,
        )
        item.tasks = resolve_tasks(item, params)
        order.items.append(item)
    return order


def load_order(session: Session, order_id: str, params: Params) -> tuple[OrderRow, Order] | None:
    row = session.get(OrderRow, order_id)
    if row is None:
        return None
    items = session.exec(
        select(OrderItemRow).where(OrderItemRow.order_id == order_id)
    ).all()
    return row, hydrate(row, items, params)


def open_orders(
    session: Session, *, include_simulated: bool = True, on: date | None = None
) -> list[tuple[OrderRow, list[OrderItemRow]]]:
    """Everything still in flight, oldest first — the barista's queue."""
    statement = select(OrderRow).where(
        OrderRow.state.in_([str(s) for s in State if not State.is_terminal(s)])
    )
    if not include_simulated:
        statement = statement.where(OrderRow.is_simulated == False)  # noqa: E712
    if on is not None:
        statement = statement.where(OrderRow.service_date == on.isoformat())

    rows = session.exec(statement.order_by(OrderRow.placed_at_s)).all()
    if not rows:
        return []

    ids = [row.order_id for row in rows]
    items = session.exec(
        select(OrderItemRow).where(OrderItemRow.order_id.in_(ids))
    ).all()
    by_order: dict[str, list[OrderItemRow]] = {order_id: [] for order_id in ids}
    for item in items:
        by_order[item.order_id].append(item)
    return [(row, sorted(by_order[row.order_id], key=lambda i: i.position)) for row in rows]
