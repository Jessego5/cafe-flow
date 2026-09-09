"""
These are the acceptance tests for the app, and they are done when a person can
place an order in the student view, watch it appear in the barista view within a
second, tap it through to ready and see the number on the display, with
order_events holding the full trace. The within-a-second half is a property of
the SSE transport rather than of any route function, so it is proved against a
running server in tests/test_stream.py and everything else is here. Run them
with pytest.
"""

from __future__ import annotations

import threading

import pytest
from sqlmodel import select

from app.config import Env, get_params, service_date
from app.db import (
    OrderEventRow,
    SlotRow,
    ensure_slots,
    get_engine,
    init_db,
    reserve_slot_capacity,
    seed_menu,
    session_scope,
)
from core.menu import make_item, service_seconds
from core.states import State

from tests.conftest import FROZEN_NOW_S, sign_in, BASE_YAML as BASE_YAML_PATH

LATTE = {"drink": "latte", "milk_type": "oat"}
DRIP = {"drink": "drip_coffee"}


def place(client, *lines, **kwargs) -> dict:
    response = client.post("/orders", json={"lines": list(lines)}, **kwargs)
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# menu and config
# --------------------------------------------------------------------------


def test_menu_comes_from_params(client):
    body = client.get("/menu").json()
    names = [item["name"] for item in body["items"]]
    assert names == list(get_params().menu)
    latte = next(item for item in body["items"] if item["name"] == "latte")
    assert latte["price_cents"] == 575
    assert latte["stations"] == ["steam_wand", "group_head"]     # the hot build
    assert latte["variants"] == ["hot", "iced"]
    assert latte["default_variant"] == "hot"
    assert latte["service_s"] == service_seconds(
        make_item("latte", get_params(), order_id="p", item_id="p-0", milk_type="oat")
    )
    assert body["serve_styles"] == ["hot", "iced"]
    assert body["provenance"].startswith("provenance: ")


def test_config_states_the_environment(client):
    body = client.get("/config").json()
    assert body["env"] == "dev"
    assert body["slots_enabled"] is False
    assert body["bottleneck_station"] == "steam_wand"
    assert body["accepts_simulated_orders"] is True


# --------------------------------------------------------------------------
# placing and moving orders
# --------------------------------------------------------------------------


def test_placing_an_order_prices_it_and_costs_it(client):
    order = place(client, LATTE, {"drink": "bacon_egg_cheese_bagel"})
    assert order["state"] == State.PLACED
    assert order["number"] == 1
    assert order["price_cents"] == 575 + 539
    wand = get_params().station("steam_wand")
    steam_oz = next(
        task.oz for task in get_params().menu_item("latte").plan("hot")[0]
        if task.station == "steam_wand"
    )
    assert order["bottleneck_cost_s"] == pytest.approx(
        wand.setup_s + wand.per_6oz_s * steam_oz / 6      # one steam, setup included
    )
    assert order["payment"] == {"status": "stubbed", "amount_due_cents": 0}
    assert [item["drink"] for item in order["items"]] == ["latte", "bacon_egg_cheese_bagel"]


def test_an_iced_latte_costs_nothing_at_the_wand(client):
    """
    Same price, same drink, no steam. The variant reaches the capacity
    accounting, not just the label on the cup."""
    hot = place(client, {"drink": "latte", "milk_type": "oat", "variant": "hot"})
    iced = place(client, {"drink": "latte", "milk_type": "oat", "variant": "iced"})
    assert hot["price_cents"] == iced["price_cents"]
    assert hot["bottleneck_cost_s"] > 0.0
    assert iced["bottleneck_cost_s"] == 0.0


def test_an_unknown_variant_is_refused(client):
    response = client.post(
        "/orders", json={"lines": [{"drink": "latte", "milk_type": "oat", "variant": "warm"}]}
    )
    assert response.status_code == 400


def test_order_numbers_count_up_within_the_day(client):
    assert [place(client, DRIP)["number"] for _ in range(3)] == [1, 2, 3]


def test_the_full_trace_lands_in_order_events(client):
    order = place(client, LATTE)
    for state in ("accepted", "in_progress", "ready", "picked_up"):
        assert client.post(
            f"/orders/{order['order_id']}/transition", json={"to": state}
        ).status_code == 200

    events = client.get(f"/orders/{order['order_id']}").json()["events"]
    assert [(event["from_state"], event["to_state"]) for event in events] == [
        (None, "placed"),
        ("placed", "accepted"),
        ("accepted", "in_progress"),
        ("in_progress", "ready"),
        ("ready", "picked_up"),
    ]
    assert [event["actor"] for event in events] == ["customer"] + ["barista"] * 4
    assert all(event["wall_ts"] for event in events)
    assert [event["seq"] for event in events] == sorted(event["seq"] for event in events)


def test_an_illegal_tap_is_refused_and_writes_nothing(client):
    order = place(client, LATTE)
    response = client.post(f"/orders/{order['order_id']}/transition", json={"to": "ready"})
    assert response.status_code == 409
    assert "not legal" in response.json()["detail"]

    trace = client.get(f"/orders/{order['order_id']}").json()
    assert trace["state"] == State.PLACED
    assert len(trace["events"]) == 1


def test_a_double_tap_does_not_skip_a_state(client):
    order = place(client, LATTE)
    first = client.post(f"/orders/{order['order_id']}/transition", json={"to": "accepted"}).json()
    second = client.post(f"/orders/{order['order_id']}/transition", json={"to": "accepted"}).json()

    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["state"] == State.ACCEPTED
    assert len(client.get(f"/orders/{order['order_id']}").json()["events"]) == 2


def test_unknown_order_is_a_404(client):
    assert client.get("/orders/nope").status_code == 404
    assert client.post("/orders/nope/transition", json={"to": "accepted"}).status_code == 404


@pytest.mark.parametrize(
    "lines, reason",
    [
        ([{"drink": "flat_white"}], "not on the menu"),
        ([{"drink": "latte"}], "latte with no milk"),
        ([{"drink": "drip_coffee", "milk_type": "oat"}], "milk on a black coffee"),
        ([{"drink": "latte", "milk_type": "hemp"}], "unknown milk"),
    ],
)
def test_core_rules_reject_bad_orders(client, lines, reason):
    assert client.post("/orders", json={"lines": lines}).status_code == 400, reason


def test_an_empty_order_is_refused(client):
    assert client.post("/orders", json={"lines": []}).status_code == 422


# --------------------------------------------------------------------------
# the two staff views
# --------------------------------------------------------------------------


def test_the_queue_holds_open_orders_oldest_first(client):
    first = place(client, LATTE)
    second = place(client, DRIP)
    client.post(f"/orders/{first['order_id']}/transition", json={"to": "accepted"})

    queue = client.get("/queue").json()
    assert [order["number"] for order in queue["orders"]] == [first["number"], second["number"]]
    assert [order["state"] for order in queue["orders"]] == ["accepted", "placed"]
    assert queue["orders"][0]["items"][0]["milk_type"] == "oat"


def test_a_finished_order_leaves_the_queue_and_reaches_the_display(client):
    order = place(client, LATTE)
    for state in ("accepted", "in_progress", "ready"):
        client.post(f"/orders/{order['order_id']}/transition", json={"to": state})

    assert [row["number"] for row in client.get("/display").json()["ready"]] == [order["number"]]
    assert [o["order_id"] for o in client.get("/queue").json()["orders"]] == [order["order_id"]]

    client.post(f"/orders/{order['order_id']}/transition", json={"to": "picked_up"})
    assert client.get("/display").json()["ready"] == []
    assert client.get("/queue").json()["orders"] == []


def test_the_display_shows_numbers_not_names(client):
    order = place(client, LATTE)
    for state in ("accepted", "in_progress", "ready"):
        client.post(f"/orders/{order['order_id']}/transition", json={"to": state})
    row = client.get("/display").json()["ready"][0]
    assert set(row) == {"number", "order_id", "is_simulated", "ready_for_s", "items"}


# --------------------------------------------------------------------------
# simulated traffic
# --------------------------------------------------------------------------


def test_dev_accepts_simulated_orders_and_flags_them(client):
    order = place(client, DRIP, headers={"X-Simulated-Order": "1"})
    assert order["is_simulated"] is True

    with session_scope() as session:
        rows = session.exec(
            select(OrderEventRow).where(OrderEventRow.order_id == order["order_id"])
        ).all()
    assert all(row.is_simulated for row in rows)
    assert client.get("/queue").json()["orders"][0]["is_simulated"] is True


def test_pilot_rejects_simulated_orders_at_the_api(app_env, monkeypatch):
    """
    A simulated order on the bar during a real rush destroys trust in the
    tool permanently, so this is a 403 and not a filter."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setattr(app_env, "env", Env.PILOT)
    # https, because in pilot the session cookie is marked Secure and a browser
    # will not send it back over plain http. That is the point of the flag; it
    # just means the test has to look like the deployment.
    with TestClient(create_app(), base_url="https://testserver") as pilot:
        sign_in(pilot)
        response = pilot.post(
            "/orders", json={"lines": [DRIP]}, headers={"X-Simulated-Order": "1"}
        )
        assert response.status_code == 403
        assert pilot.get("/config").json()["accepts_simulated_orders"] is False
        assert pilot.get("/queue").json()["showing_simulated"] is False
        assert pilot.post("/orders", json={"lines": [DRIP]}).status_code == 201


# --------------------------------------------------------------------------
# slot plumbing: built now, inert until the findings support it
# --------------------------------------------------------------------------


def test_slots_are_listed_but_off(client):
    body = client.get("/slots").json()
    assert body["enabled"] is False
    assert len(body["slots"]) == 96                      # 07:00-15:00 in 5 min windows
    assert body["slots"][0]["capacity_s"] == 300.0       # one steam wand for 5 minutes
    assert body["slots"][0]["preorder_capacity_s"] == 210.0   # 30% held for walk-ups


def test_an_order_cannot_take_a_slot_while_slots_are_off(client):
    response = client.post(
        "/orders", json={"lines": [DRIP], "channel": "preorder", "slot_id": "whatever"}
    )
    assert response.status_code == 400
    assert "disabled" in response.json()["detail"]


def test_booking_a_slot_spends_bottleneck_seconds(with_slots):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        slots = client.get("/slots").json()
        assert slots["enabled"] is True
        bookable = next(slot for slot in slots["slots"] if slot["bookable"])

        response = client.post(
            "/orders",
            json={"lines": [LATTE], "channel": "preorder", "slot_id": bookable["slot_id"]},
        )
        assert response.status_code == 201, response.text
        order = response.json()
        assert order["slot_id"] == bookable["slot_id"]
        assert order["promised_at_s"] == bookable["ends_at_s"]

        after = next(
            slot for slot in client.get("/slots").json()["slots"]
            if slot["slot_id"] == bookable["slot_id"]
        )
        assert after["used_s"] == order["bottleneck_cost_s"]
        assert after["remaining_s"] == bookable["remaining_s"] - order["bottleneck_cost_s"]

        # cancelling before it is made gives the capacity back
        client.post(f"/orders/{order['order_id']}/transition", json={"to": "cancelled"})
        restored = next(
            slot for slot in client.get("/slots").json()["slots"]
            if slot["slot_id"] == bookable["slot_id"]
        )
        assert restored["used_s"] == pytest.approx(0.0)


def test_a_preorder_needs_a_slot_when_slots_are_on(with_slots):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        response = client.post("/orders", json={"lines": [DRIP], "channel": "preorder"})
        assert response.status_code == 400
        assert client.post("/orders", json={"lines": [DRIP]}).status_code == 201  # walk-ups fine


def test_a_full_slot_is_refused(with_slots):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        bookable = next(
            slot for slot in client.get("/slots").json()["slots"] if slot["bookable"]
        )
        attempts = 20
        placed, refused = 0, 0
        cost_s = None
        for _ in range(attempts):
            response = client.post(
                "/orders",
                json={"lines": [LATTE], "channel": "preorder", "slot_id": bookable["slot_id"]},
            )
            if response.status_code == 201:
                placed += 1
                cost_s = response.json()["bottleneck_cost_s"]
            else:
                assert response.status_code == 409
                refused += 1

        # the window holds as many as its bottleneck-seconds allow, and no more
        assert placed == int(bookable["remaining_s"] // cost_s)
        assert refused == attempts - placed


def test_capacity_check_and_decrement_are_one_transaction(tmp_path):
    """
    Concurrent reservations against a slot with room for K leave exactly K
    winners. This is the invariant the M9 concurrency test re-checks over HTTP.
    """
    params = get_params()
    engine = init_db(get_engine(f"sqlite:///{tmp_path / 'race.db'}"))
    on = service_date(params)
    with session_scope(engine) as session:
        seed_menu(session, params)
        slot = ensure_slots(session, params, on)[0]
        slot_id, capacity = slot.slot_id, slot.preorder_capacity_s

    cost = 30.0
    room = int(capacity // cost)
    wins: list[bool] = []
    lock = threading.Lock()

    def attempt() -> None:
        with session_scope(engine) as session:
            won = reserve_slot_capacity(session, slot_id, cost)
            session.commit()
        with lock:
            wins.append(won)

    threads = [threading.Thread(target=attempt) for _ in range(50)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(wins) == room
    with session_scope(engine) as session:
        assert session.get(SlotRow, slot_id).used_s == pytest.approx(room * cost)


# --------------------------------------------------------------------------
# the log is append-only
# --------------------------------------------------------------------------


def test_events_are_only_ever_appended(client):
    order = place(client, LATTE)
    client.post(f"/orders/{order['order_id']}/transition", json={"to": "accepted"})

    with session_scope() as session:
        rows = session.exec(select(OrderEventRow).order_by(OrderEventRow.seq)).all()
        seqs = [row.seq for row in rows]
        ids = [row.event_id for row in rows]

    assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))
    assert ids == [f"dev:{seq:07d}" for seq in seqs]
    assert len(set(ids)) == len(ids)


def test_a_rejected_order_leaves_no_trace(client):
    client.post("/orders", json={"lines": [{"drink": "flat_white"}]})
    with session_scope() as session:
        assert session.exec(select(OrderEventRow)).all() == []


def test_only_core_writes_the_event_log(root):
    """
    Ground rule 4 as an import-level invariant: `app/` never builds an event
    row or emits one itself; it hands `core.states` a log to write through."""
    import ast

    offences: list[str] = []
    for path in sorted((root / "app").rglob("*.py")):
        if path.name == "db.py":            # DbEventLog is the write-through
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                target = node.func
                name = getattr(target, "attr", None) or getattr(target, "id", None)
                if name in {"emit", "OrderEventRow"}:
                    offences.append(f"{path.relative_to(root)}:{node.lineno} calls {name}")
    assert offences == []


# --------------------------------------------------------------------------
# health
# --------------------------------------------------------------------------


def test_healthz_reports_a_reachable_writable_database(client):
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["event_log_readable"] is True
    assert body["event_log_writable"] is True
    assert body["events"] == 0
    assert body["provenance"].startswith("provenance: ")


def test_healthz_does_not_append_to_the_event_log(client):
    place(client, LATTE)
    before = client.get("/healthz").json()["events"]
    client.get("/healthz")
    assert client.get("/healthz").json()["events"] == before == 1


def test_healthz_fails_loudly_when_the_database_is_gone(client, app_env):
    import app.db as db

    db._engine = None
    app_env.db_path = app_env.db_path.parent / "read-only" / "cafe.db"
    app_env.db_path.parent.mkdir(parents=True, exist_ok=True)
    app_env.db_path.parent.chmod(0o500)
    try:
        response = client.get("/healthz")
        assert response.status_code == 503
        assert "error" in response.json()
    finally:
        app_env.db_path.parent.chmod(0o700)
        db._engine = None


# --------------------------------------------------------------------------
# the bar runs the policy the experiments measured
# --------------------------------------------------------------------------


def test_the_queue_names_the_scheduler_in_force(client):
    body = client.get("/queue").json()
    assert body["policy"] == get_params().policy.name
    assert body["batches"] == []          # nothing queued, nothing to group

    order = place(client, LATTE)
    with session_scope() as session:
        rows = session.exec(select(OrderEventRow)).all()
    assert {row.policy for row in rows} == {get_params().policy.name}
    assert order["order_id"]


def test_fifo_suggests_nothing_to_run_together(client):
    for _ in range(3):
        place(client, {"drink": "bacon_egg_cheese_bagel"})
    assert client.get("/queue").json()["batches"] == []


def test_the_bar_is_told_what_to_run_together(app_env, tmp_path, monkeypatch):
    """
    The payoff of keeping the scheduler in core: the policy an experiment
    measured is the policy the bar is shown, not a second implementation."""
    from fastapi.testclient import TestClient

    from app.config import get_params as fresh
    from app.main import create_app

    overlay = tmp_path / "batch.yaml"
    overlay.write_text("policy:\n  name: batch_milk\n  source: assumed\n")
    monkeypatch.setattr(app_env, "param_files", [BASE_YAML_PATH, overlay])
    fresh.cache_clear()

    with TestClient(create_app()) as batching:
        sign_in(batching)
        for lines in (
            [{"drink": "bacon_egg_cheese_bagel"}],
            [{"drink": "latte", "milk_type": "oat", "variant": "hot"}],
            [{"drink": "sausage_egg_cheese"}],
            [{"drink": "latte", "milk_type": "oat", "variant": "hot"}],
            [{"drink": "latte", "milk_type": "whole", "variant": "hot"}],
        ):
            assert batching.post("/orders", json={"lines": lines}).status_code == 201

        body = batching.get("/queue").json()
        assert body["policy"] == "batch_milk"

        by_station = {batch["station"]: batch for batch in body["batches"]}
        assert set(by_station) == {"panini_press", "steam_wand"}

        press = by_station["panini_press"]
        assert press["size"] == 2
        assert press["saving_s"] == get_params().station("panini_press").run_s

        wand = by_station["steam_wand"]
        assert wand["key"] == "oat"                     # never mixes milks
        assert {item["milk_type"] for item in wand["items"]} == {"oat"}

        # the finding, visible on the bar: the press is where batching pays
        assert press["saving_s"] > 10 * wand["saving_s"]

        # a suggestion never claims work already on the shelf
        for batch in body["batches"]:
            for item in batch["items"]:
                assert item["number"] in {order["number"] for order in body["orders"]}


def test_a_finished_order_drops_out_of_the_suggestions(app_env, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_params as fresh
    from app.main import create_app

    overlay = tmp_path / "batch.yaml"
    overlay.write_text("policy:\n  name: batch_milk\n  source: assumed\n")
    monkeypatch.setattr(app_env, "param_files", [BASE_YAML_PATH, overlay])
    fresh.cache_clear()

    with TestClient(create_app()) as batching:
        sign_in(batching)
        first = batching.post(
            "/orders", json={"lines": [{"drink": "bacon_egg_cheese_bagel"}]}
        ).json()
        batching.post("/orders", json={"lines": [{"drink": "sausage_egg_cheese"}]})
        assert batching.get("/queue").json()["batches"]

        for state in ("accepted", "in_progress", "ready"):
            batching.post(f"/orders/{first['order_id']}/transition", json={"to": state})

        assert batching.get("/queue").json()["batches"] == []


def test_the_app_shows_the_wait_the_model_would_assume(client):
    """
    One estimate, in core, used by both. If they disagreed the cafe would
    tell people one thing while the model assumed another."""
    from core.waiting import estimate_wait_s, nominal_seconds_per_order

    empty = client.get("/menu").json()
    assert empty["queue_depth"] == 0
    assert empty["wait_estimate_s"] == 0

    for _ in range(4):
        place(client, LATTE)

    menu = client.get("/menu").json()
    queue = client.get("/queue").json()
    assert menu["queue_depth"] == queue["queue_depth"] == 4

    params = get_params()
    per_order = nominal_seconds_per_order(params, params.baristas_at(FROZEN_NOW_S))
    assert menu["wait_estimate_s"] == pytest.approx(estimate_wait_s(4, per_order), abs=0.1)
    assert menu["wait_estimate_s"] == queue["wait_estimate_s"]


def test_a_drink_on_the_shelf_is_not_something_to_wait_behind(client):
    order = place(client, LATTE)
    for state in ("accepted", "in_progress", "ready"):
        client.post(f"/orders/{order['order_id']}/transition", json={"to": state})
    assert client.get("/menu").json()["queue_depth"] == 0


# --------------------------------------------------------------------------
# quoting a time through the API
# --------------------------------------------------------------------------

BASKET = [{"drink": "latte", "milk_type": "oat", "variant": "hot"},
          {"drink": "bacon_egg_cheese_bagel"}]


def test_ordering_now_gets_a_time(client):
    body = client.post("/plan", json={"lines": BASKET}).json()
    assert body["wait_s"] > 0
    assert body["ready_at_s"] == pytest.approx(body["order_at_s"] + body["wait_s"], abs=1)
    assert body["achievable"] is True
    assert body["order_in_s"] == 0


def test_asking_for_a_time_says_when_to_order(client):
    body = client.post("/plan", json={"lines": BASKET, "wanted_at": "13:00"}).json()
    assert body["wanted_at"] == "13:00"
    assert body["ready_at_s"] <= 13 * 3600
    assert body["order_at_s"] <= body["ready_at_s"]
    assert body["order_in_s"] > 0            # something to be reminded about


def test_the_quote_says_where_it_came_from(client):
    """A number on a screen with no provenance is a guess wearing a uniform."""
    body = client.post("/plan", json={"lines": BASKET}).json()
    assert body["from_forecast"]["days"] > 0
    assert body["from_forecast"]["quantile"] >= 75
    assert body["basket_s"] > 0 and body["typical_basket_s"] > 0


def test_a_time_already_gone_is_refused_honestly(client):
    body = client.post("/plan", json={"lines": BASKET, "wanted_at": "07:35"}).json()
    assert body["achievable"] is False


def test_an_unknown_drink_cannot_be_quoted(client):
    assert client.post("/plan", json={"lines": [{"drink": "flat_white"}]}).status_code == 400


def test_a_malformed_time_is_refused(client):
    assert client.post(
        "/plan", json={"lines": BASKET, "wanted_at": "half twelve"}
    ).status_code == 400
