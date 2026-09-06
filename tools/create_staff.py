"""Make a staff account.

    python -m tools.create_staff jess

Prompts for the password rather than taking it as an argument, because an
argument lands in the shell history and in the process list. There is no
registration endpoint on purpose: the cafe has one bar and the people behind it
are known to each other, so accounts are made by somebody with a shell.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app.config import now_utc
from app.db import StaffRow, get_engine, init_db, session_scope
from app.security import hash_password

MIN_LENGTH = 10


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("username")
    parser.add_argument(
        "--reset", action="store_true", help="set a new password for an existing account"
    )
    args = parser.parse_args()

    init_db(get_engine())
    with session_scope() as session:
        existing = session.get(StaffRow, args.username)
        if existing is not None and not args.reset:
            raise SystemExit(f"{args.username} already exists; --reset to change it")

        password = getpass.getpass("password: ")
        if len(password) < MIN_LENGTH:
            raise SystemExit(f"at least {MIN_LENGTH} characters, please")
        if password != getpass.getpass("again: "):
            raise SystemExit("they did not match")

        if existing is not None:
            existing.hashed_password = hash_password(password)
            session.add(existing)
            what = "reset"
        else:
            session.add(
                StaffRow(
                    username=args.username,
                    hashed_password=hash_password(password),
                    created_at=now_utc(),
                )
            )
            what = "created"
        session.commit()

    print(f"{what} {args.username}", file=sys.stderr)


if __name__ == "__main__":
    main()
