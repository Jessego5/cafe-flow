"""Guards on the deployment configuration.

These are the invariants that are cheap to hold and expensive to lose: they
cannot be caught by the app's own tests because the failure happens on a
machine, at boot, in front of a cafe.
"""

from __future__ import annotations

import stat
import tomllib

import pytest

DEPLOY_FILES = ["Dockerfile", "entrypoint.sh", "fly.toml", "litestream.yml", "README.md"]


@pytest.fixture(scope="module")
def deploy(request):
    return request.config.rootpath / "deploy"


@pytest.fixture(scope="module")
def fly(deploy):
    """fly.toml, parsed.

    These used to match strings, which tied them to whichever quote style the
    `fly` CLI last rewrote the file in and broke on a reformat that changed
    nothing. Assert on values instead.
    """
    with (deploy / "fly.toml").open("rb") as handle:
        return tomllib.load(handle)


def test_the_deploy_directory_is_complete(deploy):
    assert sorted(p.name for p in deploy.glob("*")) == sorted(
        DEPLOY_FILES + ["verify-restore.sh"]
    )


def test_the_entrypoint_is_executable(deploy):
    for name in ("entrypoint.sh", "verify-restore.sh"):
        mode = (deploy / name).stat().st_mode
        assert mode & stat.S_IXUSR, f"{name} is not executable"


def test_fly_pins_one_machine(fly):
    """SQLite in WAL mode is single-writer; two machines silently corrupt state."""
    service = fly["http_service"]
    assert service["min_machines_running"] == 1
    assert service["auto_start_machines"] is False
    # fly turned this into an enum; 'off' is what false used to mean, and the
    # other values ('stop', 'suspend') are exactly what must not happen here.
    assert service["auto_stop_machines"] in (False, "off")


def test_fly_mounts_a_volume_and_checks_health(fly):
    assert any(mount["destination"] == "/data" for mount in fly["mounts"])
    assert any(
        check["path"] == "/healthz" for check in fly["http_service"]["checks"]
    )


def test_the_heartbeat_stays_under_the_proxy_idle_timeout(fly):
    """A proxy that times out before the next heartbeat kills every stream."""
    heartbeat = int(fly["env"]["CAFE_SSE_HEARTBEAT_S"])
    assert 0 < heartbeat <= 30


def test_the_entrypoint_restores_before_it_migrates(deploy):
    """Restore, migrate, then start. Migrating first would create an empty
    database and make the replica unrestorable."""
    script = (deploy / "entrypoint.sh").read_text()
    restore = script.index("litestream restore")
    migrate = script.index("python -m app.migrate")
    serve = script.index("uvicorn app.main:app")
    assert restore < migrate < serve
    assert "set -eu" in script  # a failed restore must stop the boot


def test_params_are_read_from_the_volume_not_the_image(deploy):
    """M8 drops observed.yaml on the volume; that must not need a rebuild."""
    script = (deploy / "entrypoint.sh").read_text()
    assert "$DATA_DIR/params/base.yaml" in script
    assert "observed.yaml" in script
    assert "CAFE_PARAMS" in script


def test_the_image_serves_the_built_views(deploy):
    dockerfile = (deploy / "Dockerfile").read_text()
    assert "npm run build" in dockerfile
    assert "COPY --from=web /web/dist ./web/dist" in dockerfile
    assert "litestream" in dockerfile
    assert "/healthz" in dockerfile          # container health check


def test_litestream_replicates_the_order_log(deploy):
    config = (deploy / "litestream.yml").read_text()
    assert "${CAFE_DB}" in config
    assert "retention" in config and "snapshot-interval" in config


def test_ci_gates_the_deploy_on_green(request):
    workflow = (request.config.rootpath / ".github" / "workflows" / "ci.yml").read_text()
    assert "needs: [test, web, image, drift]" in workflow
    assert "pytest -q" in workflow

    # the two checks that only mean anything against a running app
    assert "python -m sim.client --drift-check" in workflow
    assert "python -m sim.client --concurrency" in workflow
