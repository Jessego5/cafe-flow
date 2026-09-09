"""
This turns a room-schedule export into arrivals.class_blocks. The class blocks
in params/base.yaml were invented, five round numbers ending on the hour that
looked like how a university ought to behave, whereas a real export says what
actually lets out, when, and how many people are in it, which is the one part
of demand you can know without standing in the cafe. It settles two things a
fitted curve cannot. It puts demand at the times classes actually end, which
turn out not to be the tidy :50 boundaries that were guessed, and it covers the
whole day, including an afternoon that was previously carried by a flat
background rate. What it cannot settle is how many of those students walk over,
which stays capture_rate, fitted against a counted queue. Weekdays differ
enough to matter, since Morgridge runs 117 section-meetings on a Thursday and
48 on a Friday, so each weekday is written as its own overlay rather than
averaged into one fictional day. Run it with python -m tools.schedule_to_blocks
--xlsx data/morgridge_hall_fall2026_classes.xlsx.
"""

from __future__ import annotations

import argparse
import re
from collections import defaultdict
from pathlib import Path

import openpyxl
import yaml

from core.params import load_params


class Clock(str):
    """An HH:MM that must survive a YAML round trip as a string."""


yaml.SafeDumper.add_representer(
    Clock, lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", str(v), style="'")
)

__all__ = ["Meeting", "read_meetings", "blocks_for", "DAYS"]

# The export writes Thursday as R, the usual registrar convention.
DAYS: dict[str, str] = {
    "M": "monday", "T": "tuesday", "W": "wednesday",
    "R": "thursday", "F": "friday",
}

# "TR 2:30 PM-3:45 PM Rm 6618", and a cell may hold several separated by "/".
MEETING = re.compile(
    r"\b([MTWRF]{1,5})\s+(\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(\d{1,2}:\d{2}\s*[AP]M)"
)


def _minutes(clock: str) -> int:
    hour, minute = (int(part) for part in clock.split()[0].split(":"))
    meridiem = clock.upper()
    if "PM" in meridiem and hour != 12:
        hour += 12
    if "AM" in meridiem and hour == 12:
        hour = 0
    return hour * 60 + minute


def read_meetings(path: Path) -> list[tuple[str, int, int]]:
    """
    Every (weekday letter, end time in minutes, headcount) the sheet holds.

    A section meeting MWF releases its students three times a week, so it is
    three meetings here. Enrollment is preferred over capacity: a room that
    seats 75 with 30 people in it releases 30.
    """
    sheet = openpyxl.load_workbook(path, data_only=True).active
    header = [str(cell.value or "").strip().lower() for cell in sheet[1]]
    column = {name: index for index, name in enumerate(header)}
    enrolled = column.get("currently enrolled")
    capacity = column.get("class capacity")
    when = column.get("days / time / room")
    if when is None:
        raise SystemExit(f"no 'Days / Time / Room' column in {path}; found {header}")

    out: list[tuple[str, int, int]] = []
    unparsed = 0
    for row in sheet.iter_rows(min_row=2, values_only=True):
        cell = row[when]
        if not cell:
            unparsed += 1
            continue
        found = MEETING.findall(str(cell))
        if not found:
            unparsed += 1
            continue
        head = row[enrolled] if enrolled is not None else None
        if not isinstance(head, (int, float)) or head <= 0:
            head = row[capacity] if capacity is not None else None
        if not isinstance(head, (int, float)) or head <= 0:
            unparsed += 1
            continue
        for days, _start, end in found:
            for letter in days:
                if letter in DAYS:
                    out.append((letter, _minutes(end), int(head)))

    if unparsed:
        print(f"  {unparsed} row(s) had no usable time or headcount")
    return out


def blocks_for(
    meetings: list[tuple[str, int, int]],
    day: str,
    *,
    opens_min: int,
    closes_min: int,
) -> tuple[list[dict], int]:
    """
    One block per distinct end time on that weekday, inside opening hours.

    `sections` and `avg_enrollment` are kept as separate fields because that is
    the shape the model already takes; their product is the headcount, which is
    the only thing `class_block_size` actually uses.

    Evening classes are dropped rather than clipped to closing time. Morgridge
    runs sections until 18:45 and the cafe shuts at 16:30; pinning those to the
    edge would invent a rush at the moment the door locks. Returns the blocks
    and how many students were dropped with them.
    """
    grouped: dict[int, list[int]] = defaultdict(list)
    dropped = 0
    for letter, end, head in meetings:
        if letter != day:
            continue
        if opens_min <= end <= closes_min:
            grouped[end].append(head)
        else:
            dropped += head

    blocks = []
    for end in sorted(grouped):
        heads = grouped[end]
        blocks.append({
            # quoted: bare 10:45 is sexagesimal in YAML 1.1 and loads as 645
            "ends_at": Clock(f"{end // 60:02d}:{end % 60:02d}"),
            "sections": len(heads),
            "avg_enrollment": round(sum(heads) / len(heads), 1),
            "building": "Morgridge Hall",
            "source": "observed",
        })
    return blocks, dropped


HEADER = """\
# {weekday_title} in {building}, from the Fall 2026 room schedule.
#
# Written by `python -m tools.schedule_to_blocks`. Every end time and headcount
# here is read from the registrar's export rather than guessed, which is the
# whole point, because the invented blocks it replaces put the day's peak an
# hour early and stopped at 12:50 while the cafe stays open until 16:30.
#
# What this file does NOT settle is how many of those students walk over.
# That is `capture_rate`, and it is still fitted against a counted queue.
#
#   {sections} section-meetings, {students} students released inside opening
#   hours; {dropped} more are in evening classes the cafe is shut for.
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--xlsx", default="data/morgridge_hall_fall2026_classes.xlsx")
    parser.add_argument("--out", default="params/schedule")
    parser.add_argument("--building", default="Morgridge Hall")
    parser.add_argument(
        "--params", default="params/base.yaml:params/observed.yaml",
        help="colon separated, read only for the opening hours to filter against",
    )
    args = parser.parse_args()

    hours = load_params(*args.params.split(":"))
    opens_min = int(hours.meta.start_s // 60)
    closes_min = int(hours.meta.end_s // 60)
    print(f"  filtering to {opens_min // 60:02d}:{opens_min % 60:02d}"
          f"-{closes_min // 60:02d}:{closes_min % 60:02d} from {args.params}")

    path = Path(args.xlsx)
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Raw schedules live in data/, which is gitignored. "
            "see data/README.md."
        )

    meetings = read_meetings(path)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    for letter, weekday in DAYS.items():
        blocks, dropped = blocks_for(
            meetings, letter, opens_min=opens_min, closes_min=closes_min
        )
        if not blocks:
            print(f"  {weekday}: nothing meets, skipped")
            continue
        students = sum(int(b["sections"] * b["avg_enrollment"]) for b in blocks)
        body = HEADER.format(
            weekday_title=weekday.title(),
            building=args.building,
            sections=sum(b["sections"] for b in blocks),
            students=students,
            dropped=dropped,
        )
        body += yaml.safe_dump(
            {"arrivals": {"model": "class_blocks", "class_blocks": blocks}},
            sort_keys=False, default_flow_style=False,
        )
        target = out_dir / f"{weekday}.yaml"
        target.write_text(body)
        print(f"  wrote {target}  {len(blocks)} blocks, {students} students"
              f"  ({dropped} dropped as after hours)")


if __name__ == "__main__":
    main()
