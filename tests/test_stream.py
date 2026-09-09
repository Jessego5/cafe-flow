"""
These are the tests for the within-a-second half of the acceptance criterion.
They run a real uvicorn server, because SSE latency is a property of the
transport and the ASGI server rather than of the route function, and the same
tests double as the guard on the reconnect contract that the app is deployed
behind a proxy with. Run them with pytest.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn

BUDGET_S = 1.0        # the acceptance criterion
CONNECT_S = 5.0
LINE_WAIT_S = 5.0


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def bar_session(live_server, staff_account):
    """
    A logged-in bar, over real HTTP. Advancing an order is a staff route, so
    the taps in these tests need a session the way a barista's browser does."""
    from tests.conftest import STAFF_PASSWORD, STAFF_USER

    response = httpx.post(
        f"{live_server}/login",
        json={"username": STAFF_USER, "password": STAFF_PASSWORD},
    )
    assert response.status_code == 200, response.text
    return dict(response.cookies)


@pytest.fixture
def live_server(app_env, staff_account):
    from app.main import create_app

    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + CONNECT_S
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        pytest.fail("uvicorn did not start")

    yield f"http://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=CONNECT_S)


class Listener:
    """Reads an SSE stream on a background thread, stamping arrival times."""

    def __init__(self, base_url: str, headers: dict[str, str] | None = None) -> None:
        self.base_url = base_url
        self.headers = headers or {}
        self.events: list[tuple[float, str, dict]] = []
        self.opened = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> "Listener":
        self._thread.start()
        assert self.opened.wait(CONNECT_S), "stream never opened"
        return self

    def __exit__(self, *_exc) -> None:
        self._stop.set()
        self._thread.join(timeout=CONNECT_S)

    def _run(self) -> None:
        with httpx.Client(timeout=None) as client:
            with client.stream("GET", f"{self.base_url}/stream", headers=self.headers) as response:
                self.opened.set()
                name = ""
                for line in response.iter_lines():
                    if self._stop.is_set():
                        return
                    if line.startswith("event:"):
                        name = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        payload = json.loads(line.split(":", 1)[1].strip())
                        self.events.append((time.monotonic(), name, payload))

    def wait_for(self, predicate, timeout: float = LINE_WAIT_S):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for stamped in self.events:
                if predicate(stamped[2]):
                    return stamped
            time.sleep(0.005)
        return None


def test_an_order_reaches_the_bar_within_a_second(live_server):
    with Listener(live_server) as bar:
        time.sleep(0.1)  # let the subscription register before the order lands
        sent_at = time.monotonic()
        response = httpx.post(
            f"{live_server}/orders", json={"lines": [{"drink": "latte", "milk_type": "oat"}]}
        )
        assert response.status_code == 201
        order_id = response.json()["order_id"]

        arrival = bar.wait_for(lambda event: event["order_id"] == order_id)
        assert arrival is not None, "the bar never saw the order"
        received_at, name, event = arrival
        assert received_at - sent_at < BUDGET_S
        assert name == "state_change"
        assert event["to_state"] == "placed"


def test_every_tap_is_broadcast_in_order(live_server, bar_session):
    with Listener(live_server) as bar:
        time.sleep(0.1)
        order_id = httpx.post(
            f"{live_server}/orders", json={"lines": [{"drink": "drip_coffee"}]}
        ).json()["order_id"]

        for state in ("accepted", "in_progress", "ready", "picked_up"):
            httpx.post(
                f"{live_server}/orders/{order_id}/transition",
                json={"to": state},
                cookies=bar_session,
            )

        assert bar.wait_for(lambda event: event["to_state"] == "picked_up") is not None
        states = [event["to_state"] for _, _, event in bar.events if event["order_id"] == order_id]
        assert states == ["placed", "accepted", "in_progress", "ready", "picked_up"]


def test_a_reconnecting_client_can_resume_from_last_event_id(live_server, bar_session):
    """
    Cafe wifi drops. A client that reconnects with Last-Event-ID gets what it
    missed; it still refetches whole queue state, which `/queue` provides."""
    order_id = httpx.post(
        f"{live_server}/orders", json={"lines": [{"drink": "drip_coffee"}]}
    ).json()["order_id"]
    httpx.post(
        f"{live_server}/orders/{order_id}/transition", json={"to": "accepted"}, cookies=bar_session
    )

    with Listener(live_server, headers={"Last-Event-ID": "dev:0000000"}) as reconnected:
        replayed = reconnected.wait_for(lambda event: event["to_state"] == "accepted")
        assert replayed is not None
        seqs = [event["seq"] for _, _, event in reconnected.events]
        assert seqs == [1, 2]

    queue = httpx.get(f"{live_server}/queue", cookies=bar_session).json()
    assert [order["order_id"] for order in queue["orders"]] == [order_id]


def test_an_illegal_tap_broadcasts_nothing(live_server, bar_session):
    with Listener(live_server) as bar:
        time.sleep(0.1)
        order_id = httpx.post(
            f"{live_server}/orders", json={"lines": [{"drink": "drip_coffee"}]}
        ).json()["order_id"]
        assert bar.wait_for(lambda event: event["order_id"] == order_id) is not None

        assert httpx.post(
            f"{live_server}/orders/{order_id}/transition",
            json={"to": "picked_up"},
            cookies=bar_session,
        ).status_code == 409

        time.sleep(0.2)
        assert [event["to_state"] for _, _, event in bar.events] == ["placed"]


def test_the_stream_declares_itself_unbufferable(live_server):
    """A buffering proxy makes SSE look fine locally and fail in production."""
    with httpx.Client(timeout=CONNECT_S) as client:
        with client.stream("GET", f"{live_server}/stream") as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-accel-buffering"] == "no"
            assert "no-cache" in response.headers["cache-control"]
