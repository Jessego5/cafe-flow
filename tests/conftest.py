import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASE_YAML = ROOT / "params" / "base.yaml"


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def raw_base() -> dict:
    return yaml.safe_load(BASE_YAML.read_text())


@pytest.fixture
def params():
    from core.params import load_params

    return load_params(BASE_YAML)


@pytest.fixture
def corrupt(raw_base):
    """Deep-copy the base config, apply an edit, and validate it.

    `edit(cfg)` mutates the copy in place; the fixture returns the resulting
    ConfigError message so tests can assert the bad field is named.
    """
    import copy

    from core.params import ConfigError, params_from_dict

    def run(edit):
        cfg = copy.deepcopy(raw_base)
        edit(cfg)
        try:
            params_from_dict(cfg)
        except ConfigError as exc:
            return str(exc)
        raise AssertionError("expected ConfigError, config validated cleanly")

    return run


# --------------------------------------------------------------------------
# app fixtures
# --------------------------------------------------------------------------

STAFF_USER = "barista"
STAFF_PASSWORD = "a-password-for-tests"

FROZEN_NOW_S = 8 * 3600.0  # 08:00 local, inside the staffing plan


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """A fresh database, fresh params cache and a frozen wall clock.

    The app runs on wall-clock time, so without freezing it the tests would be
    flaky at the edges of the cafe day.
    """
    from app import config
    from app.config import Env, get_params
    import app.db as db

    monkeypatch.setattr(config.settings, "db_path", tmp_path / "cafe.db")
    monkeypatch.setattr(config.settings, "env", Env.DEV)
    monkeypatch.setattr(config.settings, "param_files", [BASE_YAML])
    monkeypatch.setattr(config.settings, "web_dist", tmp_path / "no-dist")
    # short heartbeat so a stopping test does not wait out a 15s ping
    monkeypatch.setattr(config.settings, "heartbeat_s", 1.0)
    # A signing key that does not change between the two apps a test may build.
    monkeypatch.setenv("CAFE_SECRET_KEY", "tests-do-not-need-a-real-secret")
    import app.security as security
    monkeypatch.setattr(security, "_key", None)
    db._engine = None
    get_params.cache_clear()

    # Every route module that reads the clock. `app.routes.menu` was missing,
    # so /menu alone ran on the real wall clock while /queue ran at 08:00 --
    # which made the one test comparing them pass only when the suite happened
    # to run inside the 07:30-10:00 staffing block, and fail the rest of the day
    # on numbers that were both correct.
    for module in ("app.routes.orders", "app.routes.slots", "app.routes.barista",
                   "app.routes.common", "app.routes.menu", "app.routes.held"):
        monkeypatch.setattr(f"{module}.day_seconds", lambda params, moment=None: FROZEN_NOW_S)

    yield config.settings

    db._engine = None
    get_params.cache_clear()


@pytest.fixture
def client(app_env, staff_account):
    """A logged-in bar. Most tests here are the cafe operating, not a stranger
    poking at it, so the default client is staff. `anon` is the one that is
    not, and it is what the guard is tested with."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        response = test_client.post(
            "/login", json={"username": STAFF_USER, "password": STAFF_PASSWORD}
        )
        assert response.status_code == 200, response.text
        yield test_client


def ensure_staff():
    """One account, made the way `tools/create_staff.py` makes it. Idempotent."""
    from app.config import now_utc
    from app.db import StaffRow, get_engine, init_db, session_scope
    from app.security import hash_password

    init_db(get_engine())
    with session_scope() as session:
        if session.get(StaffRow, STAFF_USER) is None:
            session.add(
                StaffRow(
                    username=STAFF_USER,
                    hashed_password=hash_password(STAFF_PASSWORD),
                    created_at=now_utc(),
                )
            )
            session.commit()
    return STAFF_USER


def sign_in(test_client):
    """Log a bespoke TestClient in.

    Tests that build their own app -- a different policy, a different env --
    still need a session for the staff routes, and this is the one line that
    gives them one rather than each rediscovering the credentials.
    """
    ensure_staff()
    response = test_client.post(
        "/login", json={"username": STAFF_USER, "password": STAFF_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return test_client


@pytest.fixture
def anon(app_env):
    """Nobody. What a stranger with the URL gets."""
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def staff_account(app_env):
    return ensure_staff()


@pytest.fixture
def with_slots(app_env, tmp_path, monkeypatch):
    """Turn slots on, the way M10 would if the M7 findings supported it.

    The cafe day is widened so a bookable slot exists whatever time the suite
    runs at; everything else stays as configured.
    """
    from app.config import get_params

    overlay = tmp_path / "slots_on.yaml"
    overlay.write_text(
        'meta:\n  sim_start: "00:00"\n  sim_end: "23:00"\n'
        "staffing:\n"
        '  - { from: "00:00", to: "23:00", baristas: 2, source: assumed }\n'
        "slots:\n  enabled: true\n"
    )
    monkeypatch.setattr(app_env, "param_files", [BASE_YAML, overlay])
    get_params.cache_clear()
    yield app_env
    get_params.cache_clear()
