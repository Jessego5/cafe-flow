"""
These are the tests for pre-orders that are paid for and held back until the
live queue says to place them. The feature's whole claim is that the release
decision happens later than the quote did, with the actual line in hand, so
these check that it does, that nothing reaches the bar before it, and that the
rule is the one the simulator runs. Run them with pytest.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from app.config import get_params
from app.db import HeldOrderRow, session_scope
from tests.conftest import FROZEN_NOW_S

LINES = [{"drink": "latte", "milk_type": "oat", "variant": "hot"}]
HOUR = 3600.0


@pytest.fixture
def at(monkeypatch):
    """Move the app's clock. The release loop is a function of time."""
    now = [FROZEN_NOW_S]

    def move(t_s):
        now[0] = t_s

    for module in ("orders", "slots", "barista", "common", "menu", "held"):
        monkeypatch.setattr(
            f"app.routes.{module}.day_seconds", lambda params, moment=None: now[0]
        )
    return move


def hold(client, wanted_at="09:00"):
    response = client.post("/held", json={"lines": LINES, "wanted_at": wanted_at})
    assert response.status_code == 201, response.text
    return response.json()


def test_a_held_order_is_not_an_order(client, at):
    """
    It must emit no order events until released. If you find yourself wanting
    to log one against an order_id, it does not have one yet."""
    from app.routes.held import release_due

    held = hold(client)
    assert client.get("/queue").json()["queue_depth"] == 0

    at(FROZEN_NOW_S)
    assert release_due() == []
    assert client.get("/queue").json()["queue_depth"] == 0
    assert client.get(f"/held/{held['held_id']}").json()["order_id"] is None


def test_it_releases_when_the_live_queue_says_to_not_when_the_forecast_did(client, at):
    """
    The point of holding. The quote came off a forecast; the release happens
    against the line as it actually is, which on a quiet morning is later."""
    from app.routes.held import release_due

    held = hold(client, "09:00")
    quoted = held["expected_order_at_s"]

    at(9 * HOUR - 60.0)          # a minute out, with nobody in the queue
    assert release_due() != []

    with session_scope() as session:
        row = session.get(HeldOrderRow, held["held_id"])
        assert row.state == "released"
        assert row.order_id is not None
        # later than the forecast wanted, because the bar turned out to be empty
        assert row.released_at_s > quoted
    assert client.get("/queue").json()["queue_depth"] == 1


def test_it_releases_by_the_wanted_time_whatever_the_queue_is_doing(client, at):
    """A hold that never fires is worse than a late drink."""
    from app.routes.held import release_due

    held = hold(client, "09:00")
    at(9 * HOUR + 600.0)
    assert release_due() != []
    with session_scope() as session:
        assert session.get(HeldOrderRow, held["held_id"]).state == "released"


def test_releasing_twice_does_not_place_two_orders(client, at):
    from app.routes.held import release_due

    hold(client, "09:00")
    at(9 * HOUR)
    assert len(release_due()) == 1
    assert release_due() == []
    assert client.get("/queue").json()["queue_depth"] == 1


def test_cancelling_is_idempotent_and_stops_the_release(client, at):
    from app.routes.held import release_due

    held = hold(client, "09:00")
    assert client.delete(f"/held/{held['held_id']}").status_code == 204
    assert client.delete(f"/held/{held['held_id']}").status_code == 204
    assert client.delete("/held/never-existed").status_code == 204

    at(9 * HOUR + 600.0)
    assert release_due() == []
    assert client.get("/queue").json()["queue_depth"] == 0
    with session_scope() as session:
        row = session.get(HeldOrderRow, held["held_id"])
        assert row.state == "cancelled" and row.order_id is None


def test_yesterdays_lunch_is_not_worth_firing(client, at):
    from app.routes.held import release_due

    held = hold(client, "09:00")
    with session_scope() as session:
        row = session.get(HeldOrderRow, held["held_id"])
        row.service_date = "2000-01-01"
        session.add(row)
        session.commit()

    at(9 * HOUR + 600.0)
    assert release_due() == []
    with session_scope() as session:
        assert session.get(HeldOrderRow, held["held_id"]).state == "cancelled"


def test_a_time_in_the_past_or_after_closing_is_refused(client, at):
    params = get_params()
    early = client.post("/held", json={"lines": LINES, "wanted_at": "07:30"})
    assert early.status_code == 400

    late = client.post("/held", json={"lines": LINES, "wanted_at": "23:00"})
    assert late.status_code == 400
    assert params.meta.end_s < 23 * HOUR


def test_the_server_never_takes_an_order_time_from_the_caller(client, at):
    """
    Same rule as POST /orders and its quoted flag: this is a time the cafe
    will act on, so a client must not be able to name it."""
    response = client.post(
        "/held",
        json={"lines": LINES, "wanted_at": "09:00", "order_at": "08:01"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["expected_order_at"] != "08:01"


def test_the_release_rule_is_the_one_the_simulator_runs(client, at):
    """
    `now + estimate + margin >= wanted`, in both runtimes. If these drifted
    the cafe would be telling people one thing while the model assumed another.
    """
    import inspect

    from sim.engine import Cafe

    source = inspect.getsource(Cafe.hold_until_release)
    assert "estimate + margin >= wanted" in source

    import app.routes.held as held_module

    assert "estimate + margin >= row.wanted_at_s" in inspect.getsource(
        held_module.release_due
    )


def test_both_shapes_carry_what_the_card_renders(client, at):
    """
    The held card has nowhere else to read the basket from: until the hold
    becomes an order there is no order to look it up on."""
    posted = hold(client, "09:00")
    fetched = client.get(f"/held/{posted['held_id']}").json()
    needed = {"held_id", "state", "wanted_at", "expected_order_at", "price_cents", "lines"}
    assert needed <= set(posted)
    assert needed <= set(fetched)
    assert fetched["lines"] == LINES


def test_holding_needs_its_clocks_configured(client, at, monkeypatch):
    """
    Durations live in config. A cafe that has not said how much slack to
    leave has not decided whether it wants this, so refuse rather than invent
    the number the whole behaviour turns on."""
    params = get_params()
    monkeypatch.setattr(params.customers, "release_margin_s", None)
    monkeypatch.setattr("app.routes.held.get_params", lambda: params)

    response = client.post("/held", json={"lines": LINES, "wanted_at": "09:00"})
    assert response.status_code == 503
    assert "release_margin_s" in response.text


def test_a_held_order_keeps_its_name_through_release(client, at):
    """
    The hold is paid for. Losing the name on the way to the bar is losing the
    only handle the counter has on a coffee somebody has been charged for."""
    from app.routes.held import release_due

    held = client.post(
        "/held", json={"lines": LINES, "wanted_at": "09:00", "customer_name": "Sam"}
    ).json()
    at(9 * HOUR + 600.0)
    assert release_due() != []

    order_id = client.get(f"/held/{held['held_id']}").json()["order_id"]
    assert client.get(f"/orders/{order_id}").json()["customer_name"] == "Sam"


def test_changing_a_hold_re_quotes_and_re_prices_it(client, at):
    """
    A new basket at a new time is a new promise. Patching half of the old one
    would leave a price from one order beside a release time from another."""
    held = hold(client, "09:00")
    two = LINES + [{"drink": "latte", "milk_type": "oat", "variant": "hot"}]

    changed = client.patch(
        f"/held/{held['held_id']}", json={"lines": two, "wanted_at": "10:00"}
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["held_id"] == held["held_id"]        # same hold, not a new one
    assert body["wanted_at"] == "10:00"
    assert len(body["lines"]) == 2
    assert body["price_cents"] > held["price_cents"]
    assert body["expected_order_at"] != held["expected_order_at"]

    # and the card the phone reads back agrees with what it was told
    stored = client.get(f"/held/{held['held_id']}").json()
    assert stored["wanted_at"] == "10:00" and len(stored["lines"]) == 2


def test_a_changed_hold_releases_against_its_new_time(client, at):
    """The amendment has to reach the release rule, not just the card."""
    from app.routes.held import release_due

    held = hold(client, "09:00")
    assert client.patch(
        f"/held/{held['held_id']}", json={"lines": LINES, "wanted_at": "11:00"}
    ).status_code == 200

    at(9 * HOUR + 600.0)
    assert release_due() == []                        # the old time is not due
    at(11 * HOUR + 600.0)
    assert release_due() != []


def test_a_hold_that_has_gone_in_cannot_be_changed_here(client, at):
    """
    Once released it is a live order with a log and a place in the line, and
    amending it is `PATCH /orders/{id}`, which has the accounting for that."""
    from app.routes.held import release_due

    held = hold(client, "09:00")
    at(9 * HOUR + 600.0)
    assert release_due() != []

    refused = client.patch(
        f"/held/{held['held_id']}", json={"lines": LINES, "wanted_at": "11:00"}
    )
    assert refused.status_code == 409
    assert client.patch(
        "/held/never-existed", json={"lines": LINES, "wanted_at": "11:00"}
    ).status_code == 404


def test_a_refused_change_leaves_the_hold_as_it_was(client, at):
    """The customer still has the order they paid for, at the time they paid for."""
    held = hold(client, "09:00")
    for bad in ({"lines": LINES, "wanted_at": "07:30"}, {"lines": [], "wanted_at": "10:00"}):
        assert client.patch(f"/held/{held['held_id']}", json=bad).status_code == 400

    stored = client.get(f"/held/{held['held_id']}").json()
    assert stored["wanted_at"] == "09:00" and stored["lines"] == LINES
