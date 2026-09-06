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
def client(app_env):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


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
