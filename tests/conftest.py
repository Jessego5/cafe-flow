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
