"""Staff login. One cookie, one dependency, no roles.

Customers need no account and have none: a phone remembers its own receipts and
the name on an order is a label the bar calls out, not an identity. Staff are
different, because `/queue` lists every order in the shop and the write routes
move other people's coffee around.

Deliberately plain. A four-role permission model that is designed and never
wired to a route protects nothing; one login that actually guards the writes
protects everything that needed guarding.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Cookie, HTTPException, Response
from pydantic import BaseModel
from sqlmodel import select

from app.config import now_utc, settings
from app.db import StaffRow, session_scope
from app.security import (
    SESSION_COOKIE,
    SESSION_MAX_AGE_S,
    read_session,
    sign_session,
    verify_password,
)

__all__ = ["router", "require_staff", "current_staff"]

router = APIRouter()


class LoginIn(BaseModel):
    username: str
    password: str


def current_staff(cafe_session: str | None = Cookie(default=None)) -> str | None:
    """Who is logged in, or None. Never raises: some routes only want to know."""
    return read_session(cafe_session)


def require_staff(cafe_session: str | None = Cookie(default=None)) -> str:
    """Guard a route. 401 with nothing useful in it."""
    username = read_session(cafe_session)
    if username is None:
        raise HTTPException(401, "staff login required")
    return username


@router.post("/login")
async def login(body: LoginIn, response: Response) -> dict:
    """One message for every failure.

    A missing user and a wrong password answer identically, so the endpoint
    cannot be used to find out who works here. The hash is verified even when
    the user does not exist, so the two do not take measurably different times.
    """
    with session_scope() as session:
        row = session.exec(
            select(StaffRow).where(StaffRow.username == body.username)
        ).first()

    stored = row.hashed_password if row else "scrypt$16384$8$1$AAAA$AAAA"
    if not verify_password(body.password, stored) or row is None:
        raise HTTPException(401, "wrong username or password")

    response.set_cookie(
        SESSION_COOKIE,
        sign_session(row.username),
        max_age=SESSION_MAX_AGE_S,
        httponly=True,
        samesite="lax",
        # A cookie marked secure never reaches an http:// dev server, so this
        # follows the environment rather than being hardcoded either way.
        secure=settings.env.name.lower() == "pilot",
        path="/",
    )
    return {"username": row.username}


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    """Clears the cookie. A logout route that does not is a bug people find late."""
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
async def me(cafe_session: str | None = Cookie(default=None)) -> dict:
    """Who the browser is, so the bar screen can show a login form or not."""
    return {"username": read_session(cafe_session)}
