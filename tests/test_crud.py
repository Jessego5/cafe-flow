"""The writable edges: what the cafe has run out of, and changing your mind.

Both sit over an append-only log, which is what makes the second one
interesting: an edit that only updates the projection leaves every margin in
`analysis/` reporting the basket the customer changed their mind about.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.db import MenuItemRow, OrderItemRow, session_scope
from core.events import EventType

LATTE = [{"drink": "latte", "milk_type": "oat", "variant": "hot"}]
WITH_FOOD = LATTE + [{"drink": "bacon_egg_cheese_bagel"}]


def place(client, lines=LATTE):
    response = client.post("/orders", json={"lines": lines, "channel": "walkup"})
    assert response.status_code == 201, response.text
    return response.json()


def item_named(client, name):
    return next(i for i in client.get("/menu").json()["items"] if i["name"] == name)


# --------------------------------------------------------------------------
# availability
# --------------------------------------------------------------------------


def test_the_bar_can_mark_an_item_sold_out(client):
    assert item_named(client, "bacon_egg_cheese_bagel")["available"] is True

    body = client.patch("/menu/bacon_egg_cheese_bagel", json={"available": False}).json()
    assert body == {"name": "bacon_egg_cheese_bagel", "available": False}
    assert item_named(client, "bacon_egg_cheese_bagel")["available"] is False

    refused = client.post("/orders", json={"lines": WITH_FOOD, "channel": "walkup"})
    assert refused.status_code == 409
    assert "bacon_egg_cheese_bagel" in refused.json()["detail"]

    # and only that item
    assert client.post("/orders", json={"lines": LATTE, "channel": "walkup"}).status_code == 201

    client.patch("/menu/bacon_egg_cheese_bagel", json={"available": True})
    assert client.post("/orders", json={"lines": WITH_FOOD, "channel": "walkup"}).status_code == 201


def test_a_hold_is_refused_now_rather_than_at_release(client):
    """Taking money for a sandwich the cafe has run out of and discovering it an
    hour later is the worst order of events available here."""
    client.patch("/menu/bacon_egg_cheese_bagel", json={"available": False})
    assert client.post("/held", json={"lines": WITH_FOOD, "wanted_at": "09:00"}).status_code == 409


def test_a_replay_is_not_refused_for_something_todays_counter_is_out_of(client):
    """A replay reproduces a day that already happened; making history depend on
    the present would break the drift check for a reason having nothing to do
    with the model."""
    client.patch("/menu/bacon_egg_cheese_bagel", json={"available": False})
    response = client.post(
        "/orders",
        json={"lines": WITH_FOOD, "channel": "walkup"},
        headers={"X-Simulated-Order": "1"},
    )
    assert response.status_code == 201


def test_availability_survives_a_reseed(client):
    """seed_menu rewrites the projection from params on every boot. The bagels
    running out must not be undone by a restart."""
    from app.config import get_params
    from app.db import seed_menu

    client.patch("/menu/bacon_egg_cheese_bagel", json={"available": False})
    with session_scope() as session:
        seed_menu(session, get_params())
        session.commit()
    assert item_named(client, "bacon_egg_cheese_bagel")["available"] is False


def test_patching_an_item_that_is_not_on_the_menu(client):
    assert client.patch("/menu/nope", json={"available": False}).status_code == 404


# --------------------------------------------------------------------------
# amending
# --------------------------------------------------------------------------


def test_a_basket_can_change_while_nobody_is_making_it(client):
    order = place(client)
    amended = client.patch(f"/orders/{order['order_id']}", json={"lines": WITH_FOOD})
    assert amended.status_code == 200
    body = amended.json()

    assert body["price_cents"] > order["price_cents"]
    assert [i["drink"] for i in body["items"]] == ["latte", "bacon_egg_cheese_bagel"]
    # the id and the number on the pickup display are what amending preserves
    assert body["order_id"] == order["order_id"]
    assert body["number"] == order["number"]

    with session_scope() as session:
        rows = session.exec(
            select(OrderItemRow).where(OrderItemRow.order_id == order["order_id"])
        ).all()
    assert len(rows) == 2, "the old item rows should be gone, not accumulated"


def test_a_basket_cannot_change_once_somebody_is_holding_the_cup(client):
    order = place(client)
    client.post(f"/orders/{order['order_id']}/transition",
                json={"to": "accepted", "actor": "barista"})
    refused = client.patch(f"/orders/{order['order_id']}", json={"lines": WITH_FOOD})
    assert refused.status_code == 409
    assert "accepted" in refused.json()["detail"]


def test_the_amendment_is_in_the_log_not_only_in_the_row(client):
    """Every margin in analysis/ is summed from events. An edit the log cannot
    see is an edit the metrics report the old basket for."""
    order = place(client)
    client.patch(f"/orders/{order['order_id']}", json={"lines": WITH_FOOD})

    events = client.get(f"/orders/{order['order_id']}").json()["events"]
    amended = [e for e in events if e["type"] == EventType.ORDER_AMENDED]
    assert len(amended) == 1
    payload = amended[0]["payload"]
    assert payload["margin_delta_cents"] > 0
    assert payload["price_delta_cents"] > 0
    assert payload["items"] == ["latte", "bacon_egg_cheese_bagel"]


def test_metrics_count_the_amended_basket(client):
    """The point of putting deltas in the log rather than new totals: the same
    `analysis.metrics` that reads the simulator's log reads this one."""
    from analysis.metrics import balk_count_and_lost_margin
    from app.db import events_for

    from app.db import OrderRow

    order = place(client)
    with session_scope() as session:
        before_margin = session.get(OrderRow, order["order_id"]).margin_cents

    client.patch(f"/orders/{order['order_id']}", json={"lines": WITH_FOOD})

    with session_scope() as session:
        events = events_for(session, order["order_id"])
        after_margin = session.get(OrderRow, order["order_id"]).margin_cents

    # margin is the cafe's cost data and is deliberately not in the API payload,
    # so this reads the row the bar reports from.
    offered = balk_count_and_lost_margin(events)["offered_margin_cents"]
    assert after_margin > before_margin
    assert offered == after_margin, "the log still reports the basket that was placed"


def test_an_amendment_is_refused_for_a_sold_out_item(client):
    order = place(client)
    client.patch("/menu/bacon_egg_cheese_bagel", json={"available": False})
    refused = client.patch(f"/orders/{order['order_id']}", json={"lines": WITH_FOOD})
    assert refused.status_code == 409
    assert "sold out" in refused.json()["detail"]


def test_an_empty_basket_is_not_an_amendment(client):
    order = place(client)
    assert client.patch(f"/orders/{order['order_id']}", json={"lines": []}).status_code == 422


# --------------------------------------------------------------------------
# the schema change that made this possible
# --------------------------------------------------------------------------


def test_a_database_that_predates_a_column_still_boots(tmp_path):
    """`create_all` creates missing tables and never missing columns, so a
    schema that grew a field passes every test and a fresh container while
    refusing to start against any database that already exists.

    Found the only way it can be: the HTTP replay would not start after
    `available` was added, because out/cafe.db predated it.
    """
    import sqlite3

    from sqlalchemy import create_engine

    from app.db import MenuItemRow, add_missing_columns

    target = tmp_path / "old.db"
    connection = sqlite3.connect(target)
    connection.execute(
        "CREATE TABLE menu_items (name VARCHAR PRIMARY KEY, price_cents INTEGER,"
        " cogs_cents INTEGER, requires_milk BOOLEAN, assembly_s FLOAT,"
        " service_s FLOAT, stations VARCHAR, bottleneck_cost_s FLOAT, variants VARCHAR)"
    )
    connection.execute(
        "INSERT INTO menu_items VALUES ('latte',575,200,1,20.0,74.0,'steam_wand',26.0,'hot,iced')"
    )
    connection.commit()
    connection.close()

    engine = create_engine(f"sqlite:///{target}")
    added = add_missing_columns(engine)
    assert "menu_items.available" in added

    from sqlmodel import Session

    with Session(engine) as session:
        row = session.get(MenuItemRow, "latte")
        assert row.available is True, "an existing item defaults to on sale"
        assert row.price_cents == 575, "and keeps everything else"

    assert add_missing_columns(engine) == [], "idempotent"
