"""
These are the tests for the staff login, covering both the guard and what it
deliberately leaves open. The failure they exist for is not a clever attack: it
is that /bar and every write route were reachable by anybody who found the URL,
on an app that was about to be deployed. Run them with pytest.
"""

from __future__ import annotations

import pytest

from tests.conftest import STAFF_PASSWORD, STAFF_USER

LINES = [{"drink": "latte", "milk_type": "oat", "variant": "hot"}]


def place(test_client):
    response = test_client.post("/orders", json={"lines": LINES, "channel": "walkup"})
    assert response.status_code == 201, response.text
    return response.json()


# --------------------------------------------------------------------------
# what a stranger with the URL gets
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/queue", None),
        ("patch", "/menu/latte", {"available": False}),
    ],
)
def test_the_staff_routes_are_shut(anon, staff_account, method, path, body):
    response = getattr(anon, method)(path, json=body) if body else getattr(anon, method)(path)
    assert response.status_code == 401


def test_a_stranger_cannot_advance_somebody_elses_order(anon, staff_account):
    order = place(anon)
    refused = anon.post(
        f"/orders/{order['order_id']}/transition", json={"to": "accepted", "actor": "b"}
    )
    assert refused.status_code == 401


def test_what_stays_open(anon, staff_account):
    """
    Customers have no account and must not need one. Everything they do is
    reachable by holding an unguessable order id, which is the same trust model
    the whole customer side runs on."""
    order = place(anon)
    assert anon.get("/menu").status_code == 200
    assert anon.get("/config").status_code == 200
    assert anon.get("/display").status_code == 200, "a wall screen has no keyboard"
    assert anon.get(f"/orders/{order['order_id']}").status_code == 200
    assert anon.post("/plan", json={"lines": LINES}).status_code == 200
    assert anon.patch(f"/orders/{order['order_id']}", json={"lines": LINES}).status_code == 200


def test_a_customer_may_cancel_their_own_order(anon, staff_account):
    """
    Refusing this would leave somebody who changed their mind with no way out
    but asking at the counter."""
    order = place(anon)
    cancelled = anon.post(
        f"/orders/{order['order_id']}/transition",
        json={"to": "cancelled", "actor": "customer"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"


# How to walk an order to each state the Cancel button is drawn in.
TO_REACH = {
    "placed": (),
    "accepted": ("accepted",),
    "in_progress": ("accepted", "in_progress"),
}


@pytest.mark.parametrize("reached", list(TO_REACH))
def test_a_customer_may_cancel_up_to_the_shelf(anon, client, staff_account, reached):
    """
    The exact set the Cancel button offers, in `web/student/Ticket.jsx`.

    The screen decides whether to draw the button from the order's state, so a
    state that renders one and then refuses is a button that fails when tapped.
    These are the three the rulebook allows."""
    order = place(anon)
    for state in TO_REACH[reached]:
        moved = client.post(f"/orders/{order['order_id']}/transition", json={"to": state})
        assert moved.status_code == 200, moved.text
    assert client.get(f"/orders/{order['order_id']}").json()["state"] == reached

    cancelled = anon.post(
        f"/orders/{order['order_id']}/transition",
        json={"to": "cancelled", "actor": "customer"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"


def test_a_customer_cannot_cancel_once_it_is_on_the_shelf(anon, client, staff_account):
    """
    The cup exists by then, so the only honest moves are collecting it or
    walking away. The button is not drawn in this state; this is the guard
    that says the screen and the rulebook agree about why."""
    order = place(anon)
    for state in ("accepted", "in_progress", "ready"):
        client.post(f"/orders/{order['order_id']}/transition", json={"to": state})

    refused = anon.post(
        f"/orders/{order['order_id']}/transition",
        json={"to": "cancelled", "actor": "customer"},
    )
    assert refused.status_code == 409


# --------------------------------------------------------------------------
# logging in
# --------------------------------------------------------------------------


def test_a_wrong_password_and_a_missing_user_are_indistinguishable(anon, staff_account):
    """Otherwise the endpoint tells you who works here."""
    wrong = anon.post("/login", json={"username": STAFF_USER, "password": "nope"})
    missing = anon.post("/login", json={"username": "ghost", "password": "nope"})
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()


def test_a_session_opens_the_staff_routes_and_logout_shuts_them(anon, staff_account):
    assert anon.get("/queue").status_code == 401
    assert anon.post(
        "/login", json={"username": STAFF_USER, "password": STAFF_PASSWORD}
    ).status_code == 200
    assert anon.get("/me").json()["username"] == STAFF_USER
    assert anon.get("/queue").status_code == 200

    assert anon.post("/logout").status_code == 204
    assert anon.get("/me").json()["username"] is None
    assert anon.get("/queue").status_code == 401


def test_the_cookie_cannot_be_forged(anon, staff_account):
    from app.security import SESSION_COOKIE

    anon.cookies.set(SESSION_COOKIE, "amFtZXM.9999999999.not-a-signature")
    assert anon.get("/queue").status_code == 401


def test_a_session_expires(anon, staff_account):
    import time

    from app.security import SESSION_COOKIE, SESSION_MAX_AGE_S, sign_session

    stale = sign_session(STAFF_USER, issued_at=time.time() - SESSION_MAX_AGE_S - 60)
    anon.cookies.set(SESSION_COOKIE, stale)
    assert anon.get("/queue").status_code == 401, "a laptop left logged in behind a counter"


def test_the_cookie_is_httponly_and_samesite(anon, staff_account):
    """Not readable from JavaScript, and not sent from another origin."""
    response = anon.post(
        "/login", json={"username": STAFF_USER, "password": STAFF_PASSWORD}
    )
    header = response.headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header


# --------------------------------------------------------------------------
# the hashing
# --------------------------------------------------------------------------


def test_a_password_is_never_stored_in_the_clear(anon, staff_account):
    from app.db import StaffRow, session_scope

    with session_scope() as session:
        row = session.get(StaffRow, STAFF_USER)
    assert STAFF_PASSWORD not in row.hashed_password
    assert row.hashed_password.startswith("scrypt$")


def test_two_identical_passwords_hash_differently(anon):
    """Salted. Otherwise the table tells you who shares a password."""
    from app.security import hash_password, verify_password

    first, second = hash_password("the-same-thing"), hash_password("the-same-thing")
    assert first != second
    assert verify_password("the-same-thing", first)
    assert verify_password("the-same-thing", second)


def test_a_stored_hash_carries_its_own_cost(anon):
    """So raising the cost later does not invalidate anybody's password."""
    from app.security import hash_password

    scheme, n, r, p, _salt, _digest = hash_password("x").split("$")
    assert scheme == "scrypt"
    assert int(n) >= 2**14 and int(r) == 8 and int(p) == 1
