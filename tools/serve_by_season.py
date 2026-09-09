"""
This turns Madison's climate normals into mix.serve, one overlay a month.
mix.serve is the hot/iced split, and it used to be one number standing for the
whole year, which it cannot be: Madison runs from a 74F September to a 29F
January inside a single academic year, an iced latte never touches the steam
wand, and so the split decides how much of the menu milk batching can reach at
all, meaning one figure leaves every run outside the month it was formed in
quietly wrong about the bar. This does not make the split observed. It makes an
assumption that was hidden in one number explicit in nine and gives each one a
temperature to argue with, and both ends of the line are still assumed: the
warm end is an impression at the handoff shelf during the observation window
rather than a count, which is still the better anchor of the two because it is
at least this cafe, while the cold end is the year-round cold share the chains
report nationally, because cold drinks stopped being seasonal some years ago
and what survives is a much smaller swing than the temperature range suggests,
a national figure and not this cafe's, which is exactly why the floor is set
high. Linear in the monthly normal high is a choice and not a finding, nothing
here is fitted to anything, and one count at the shelf in cold weather would
replace the whole construction with two measured points. Run it with python -m
tools.serve_by_season.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

# Normal daily maximum temperature, degrees F, Dane County Regional Airport,
# NOAA 1991-2020. Only the months a semester runs through are written; the
# cafe keeps summer hours the room schedule does not cover.
NORMAL_HIGH_F: dict[str, float] = {
    "september": 74.4,
    "october": 60.6,
    "november": 45.5,
    "december": 33.6,
    "january": 28.6,
    "february": 32.9,
    "march": 45.0,
    "april": 58.1,
    "may": 70.2,
}

# The two anchors, and the temperatures they sit at. Between them the iced
# share moves linearly; outside them it is clamped, because neither anchor was
# measured far enough out to extrapolate from.
WARM_F, WARM_ICED = 74.0, 0.80
COLD_F, COLD_ICED = 30.0, 0.60


def iced_share(high_f: float) -> float:
    """
    The assumed iced share at a monthly normal high, rounded to a percent.

    Rounded because a third decimal on a number nobody counted reads as
    precision that is not there.
    """
    span = (high_f - COLD_F) / (WARM_F - COLD_F)
    span = min(1.0, max(0.0, span))
    return round(COLD_ICED + (WARM_ICED - COLD_ICED) * span, 2)


def overlay(month: str, high_f: float) -> str:
    iced = iced_share(high_f)
    body = yaml.safe_dump(
        {"mix": {"serve": {"hot": round(1.0 - iced, 2), "iced": iced},
                 "source": "assumed"}},
        sort_keys=False,
    )
    return (
        f"# {month.capitalize()} in Madison: normal daily high {high_f:.1f}F.\n"
        f"#\n"
        f"# Written by `python -m tools.serve_by_season`. The iced share is\n"
        f"# interpolated between {WARM_ICED:.0%} iced at {WARM_F:.0f}F and "
        f"{COLD_ICED:.0%} at {COLD_F:.0f}F,\n"
        f"# and both of those are assumed rather than counted. See the module\n"
        f"# docstring for where each anchor comes from and what would replace it.\n"
        f"#\n"
        f"# This moves load off the steam wand and onto the cold bar. It does not\n"
        f"# move the register, which is the constraint.\n\n"
        + body
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Write params/season/*.yaml from Madison's climate normals."
    )
    parser.add_argument("--out", default="params/season")
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for month, high_f in NORMAL_HIGH_F.items():
        target = out / f"{month}.yaml"
        target.write_text(overlay(month, high_f))
        print(f"{month:<10} {high_f:5.1f}F   iced {iced_share(high_f):.0%}   {target}")


if __name__ == "__main__":
    main()
