"""The hot/iced split across a Madison academic year.

Done when: every month overlay loads on top of the observed config, the split
stays a distribution, it moves the right way with the temperature, and it stays
`assumed` -- because nobody has counted cups at the shelf in either season.
"""

from __future__ import annotations

import pytest

from core.params import load_params
from tools.serve_by_season import COLD_ICED, NORMAL_HIGH_F, WARM_ICED, iced_share

OBSERVED = ("params/base.yaml", "params/observed.yaml", "params/fitted_arrivals.yaml")


@pytest.mark.parametrize("month", sorted(NORMAL_HIGH_F))
def test_every_month_loads_and_stays_a_distribution(month):
    params = load_params(*OBSERVED, f"params/season/{month}.yaml")
    serve = params.mix.serve
    assert set(serve) == {"hot", "iced"}
    assert sum(serve.values()) == pytest.approx(1.0)


@pytest.mark.parametrize("month", sorted(NORMAL_HIGH_F))
def test_a_constructed_split_is_never_observed(month):
    """The curve is interpolated between two anchors nobody counted. Marking it
    `observed` would be the exact failure the provenance rules exist to catch."""
    params = load_params(*OBSERVED, f"params/season/{month}.yaml")
    assert params.source_of("mix.serve.iced") == "assumed"


def test_the_split_follows_the_weather():
    """Warmer months are more iced, and no month leaves the two anchors."""
    by_month = {month: iced_share(high) for month, high in NORMAL_HIGH_F.items()}

    assert by_month["september"] > by_month["november"] > by_month["january"]
    assert by_month["january"] < by_month["march"] < by_month["may"]
    assert all(COLD_ICED <= share <= WARM_ICED for share in by_month.values())


def test_the_curve_is_clamped_rather_than_extrapolated():
    """Neither anchor was measured far enough out to run a line past it."""
    assert iced_share(120.0) == pytest.approx(WARM_ICED)
    assert iced_share(-40.0) == pytest.approx(COLD_ICED)


def test_the_files_on_disk_match_the_generator():
    """`params/season/*.yaml` is written by the tool, so a hand edit that drifts
    from the normals it claims to come from should show up here."""
    for month, high_f in NORMAL_HIGH_F.items():
        params = load_params(*OBSERVED, f"params/season/{month}.yaml")
        assert params.mix.serve["iced"] == pytest.approx(iced_share(high_f))


def test_more_iced_never_moves_the_constraint():
    """The split governs the steam wand, and the wand is not what gates this
    cafe. If a month ever puts it above the register, that is a finding and this
    test should fail rather than be updated."""
    from analysis.metrics import station_utilisation
    from sim.engine import run

    for month in ("september", "january"):
        params = load_params(*OBSERVED, f"params/season/{month}.yaml")
        busy = station_utilisation(run(params, 0).log, params)
        assert busy["steam_wand"] < busy["register"]
