"""M9 acceptance tests: the app and the core must not have drifted.

Both runtimes share `core/`, so in principle they cannot disagree about what a
latte costs or which state moves are legal. These tests make that a fact rather
than a hope — and, just as importantly, prove the check would notice if it
stopped being true.
"""

from __future__ import annotations

import pytest

from core.params import load_params
from sim.client import Replay, check_slot_concurrency, drift_check, serve

BASE = "params/base.yaml"
SLOTS = "params/experiments/slots_check.yaml"


@pytest.fixture
def live(app_env):
    """The app, running in this process, on the test's own database."""
    with serve() as base_url:
        yield base_url


def test_the_app_and_the_core_agree(live):
    """Replay a whole simulated day over HTTP and compare it to the in-process
    run: prices, bottleneck costs, item lists, state paths, state counts and
    lost margin, all through the same metrics code."""
    report = drift_check(live, load_params(BASE), seed=0)
    assert report.ok, report.render()
    assert report.orders > 100
    assert "no drift" in report.render()


def test_the_check_would_notice_a_price_that_drifted(live, monkeypatch):
    """A check that cannot fail proves nothing. If `app/` ever grew its own
    idea of what an order costs, this is what would catch it."""
    import app.routes.orders as orders

    class Drifted(orders.StationCapacityModel):
        def order_cost(self, order):
            return super().order_cost(order) + 1.0

    monkeypatch.setattr(orders, "StationCapacityModel", Drifted)

    report = drift_check(live, load_params(BASE), seed=0)
    assert not report.ok
    assert any("bottleneck_cost_s" in str(mismatch) for mismatch in report.mismatches)
    assert "bug in app/" in report.render()


def test_the_replay_reaches_every_order(live):
    import asyncio

    params = load_params(BASE)
    from sim.engine import run

    result = run(params, 1)
    replay = Replay(live, params, speed=0)
    failures: list[str] = []
    asyncio.run(replay.play(result, on_error=lambda o, s, e: failures.append(str(e))))

    assert failures == []
    assert len(replay.placed) == len(result.orders)
    for order_id, served in replay.placed.items():
        assert served["is_simulated"] is True
        assert served["price_cents"] == result.orders[order_id].price_cents


def test_a_replayed_order_is_flagged_simulated(live):
    """`dev` and `demo` take them; `pilot` refuses them at the API. Either way
    a replayed order is never mistaken for a real one."""
    import asyncio

    from sim.engine import run

    params = load_params(BASE)
    result = run(params, 2)
    replay = Replay(live, params, speed=0)
    asyncio.run(replay.play(result))

    import httpx

    queue = httpx.get(f"{live}/queue", timeout=30.0).json()
    assert all(order["is_simulated"] for order in queue["orders"])


# --------------------------------------------------------------------------
# the concurrency invariant
# --------------------------------------------------------------------------


@pytest.fixture
def live_with_slots(app_env, tmp_path, monkeypatch):
    from app.config import get_params

    from tests.conftest import BASE_YAML

    monkeypatch.setattr(app_env, "param_files", [BASE_YAML, SLOTS])
    get_params.cache_clear()
    with serve() as base_url:
        yield base_url
    get_params.cache_clear()


def test_a_slot_cannot_be_overbooked_under_concurrency(live_with_slots):
    """The capacity check and the decrement have to be one transaction. Split
    into a read and a write they interleave, and a slot with room for five
    takes twenty bookings."""
    outcome = check_slot_concurrency(live_with_slots, attempts=50)

    assert outcome["attempts"] == 50
    assert outcome["placed"] > 0
    assert outcome["placed"] + outcome["refused"] == 50
    assert outcome["overbooked"] is False
    assert outcome["used_s"] <= outcome["capacity_s"]

    # and it filled the window: one more would not have fitted
    assert outcome["capacity_s"] - outcome["used_s"] < outcome["used_s"] / outcome["placed"]


def test_the_concurrency_check_says_so_when_there_is_nothing_to_book(live):
    """Slots are off in the base config, and a check that quietly passes
    against a disabled feature is worse than one that fails."""
    with pytest.raises(RuntimeError, match="slots are disabled"):
        check_slot_concurrency(live, attempts=2)
