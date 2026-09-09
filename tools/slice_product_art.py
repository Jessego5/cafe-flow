"""
This cuts a sheet of product art into one PNG per menu item. The sheet carries
its own alpha, so there is no matte to reconstruct: each sprite is a connected
run of opaque pixels, and the transparency that ships is the transparency that
was drawn. An earlier version of this script keyed the art off a black
background, because the sheet had reached us as a JPEG with the alpha flattened
away, and everything it did, the thresholds and the fringe removal and a
redrawn outline, was working around that one lost channel; all of it is gone.
Sprites are ordered the way the sheet reads, left to right and top to bottom,
and named for their key in params/base.yaml, so order is the only assumption
here and --dry-run reports the row counts to check it against the picture. Run
it with python tools/slice_product_art.py sheet.png --dry-run, then again with
--out web/student/img.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

import numpy as np
from PIL import Image

ORDER = [
    # row 1: espresso bar
    "espresso", "americano", "cappuccino", "latte", "latte_iced",
    "vanilla_latte", "caramel_macchiato", "mocha", "white_chocolate_mocha",
    # row 2: brew & cold brew
    "drip_coffee", "cold_brew", "cold_brew_oat_latte", "black_tie",
    "sparkling_grapefruit_cold_brew",
    # row 3: tea & matcha, then sparkling & cocoa
    "brewed_tea", "iced_tea", "matcha_latte", "chai_latte",
    "sparkling_passion_fruit_black_tea", "sparkling_lemonade", "cocoa",
    # row 4: breakfast
    "bacon_egg_cheese_bagel", "sausage_egg_cheese", "vegan_sausage_egg_cheese",
    # row 5: sandwiches & salads
    "italian_herb_chicken", "mona_lisa", "monterey_turkey",
    "caesar_salad", "build_your_own_salad", "broccoli_cheese_soup",
    "chicken_dumpling_soup",
]
EXPECTED = [9, 5, 7, 3, 7]

OPAQUE = 8        # alpha above this is drawing rather than a soft edge
SPECK = 2000      # px: below this it is a stray pixel, not a sprite
ROW_GAP = 90      # px between sprite centres that means a new row


def components(mask: np.ndarray) -> dict[int, list[tuple[int, int, int]]]:
    """Connected components of a boolean mask, as row runs keyed by label."""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    spans, previous, nxt = [], [], 0
    for y in range(mask.shape[0]):
        edges = np.diff(np.concatenate(([0], mask[y].view(np.int8), [0])))
        current = []
        for s, e in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]):
            nxt += 1
            parent[nxt] = nxt
            for ps, pe, pl in previous:
                if ps < e and s < pe:
                    union(pl, nxt)
            current.append((s, e, nxt))
            spans.append((y, s, e, nxt))
        previous = current

    grouped = defaultdict(list)
    for y, s, e, label in spans:
        grouped[find(label)].append((y, s, e))
    return grouped


def sprites(alpha: np.ndarray) -> list[list[tuple[int, int, int]]]:
    """
    Every drawn shape on the sheet, in reading order.

    Rows are read off the vertical centres rather than off empty bands: a tall
    sandwich starts above where the bagel over it ends, so the rows overlap and
    no horizontal line separates them.
    """
    shapes = [
        spans
        for spans in components(alpha > OPAQUE).values()
        if sum(e - s for _, s, e in spans) >= SPECK
    ]
    shapes.sort(key=lambda spans: (spans[0][0] + spans[-1][0]) / 2)

    rows: list[list] = []
    for shape in shapes:
        centre = (shape[0][0] + shape[-1][0]) / 2
        if rows and centre - rows[-1][1] < ROW_GAP:
            rows[-1][0].append(shape)
        else:
            rows.append([[shape], centre])
        rows[-1][1] = centre

    out = []
    for group, _ in rows:
        group.sort(key=lambda spans: min(s for _, s, _ in spans))
        out.append(group)
    return out


def cut(sheet: Image.Image, spans: list[tuple[int, int, int]], pad: float) -> Image.Image:
    """
    One sprite, alone on a transparent square.

    Only this shape's own pixels are copied: the crop rectangle of a sandwich
    overlaps the bagel above it, and a neighbour's crust in the corner of the
    frame is not this sandwich.
    """
    left = min(s for _, s, _ in spans)
    right = max(e for _, _, e in spans)
    top = spans[0][0]
    bottom = spans[-1][0] + 1

    keep = np.zeros((bottom - top, right - left), dtype=bool)
    for y, s, e in spans:
        keep[y - top, s - left:e - left] = True

    art = np.asarray(sheet.crop((left, top, right, bottom)).convert("RGBA")).copy()
    art[~keep] = 0
    sprite = Image.fromarray(art)

    side = int(max(sprite.size) / (1 - 2 * pad))
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(sprite, ((side - sprite.width) // 2, (side - sprite.height) // 2))
    return square


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("sheet", type=Path)
    parser.add_argument("--out", type=Path, default=Path("web/student/img"))
    parser.add_argument("--pad", type=float, default=0.05, help="breathing room in the square")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    sheet = Image.open(args.sheet).convert("RGBA")
    if np.asarray(sheet)[:, :, 3].min() == 255:
        print("!! this sheet has no transparency: every pixel is opaque")
        return 1

    rows = sprites(np.asarray(sheet)[:, :, 3])
    counts = [len(row) for row in rows]
    flat = [shape for row in rows for shape in row]
    print(f"{sheet.width}x{sheet.height}  sprites per row: {counts}  total {len(flat)}")

    if counts != EXPECTED or len(flat) != len(ORDER):
        print(f"!! expected {EXPECTED} totalling {len(ORDER)}: check the naming against the sheet")
        if not args.dry_run:
            return 1

    if args.dry_run:
        for name, spans in zip(ORDER, flat):
            left = min(s for _, s, _ in spans)
            top = spans[0][0]
            width = max(e for _, _, e in spans) - left
            print(f"  {name:36} {width:4}x{spans[-1][0] + 1 - top:4} at {left},{top}")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    for name, spans in zip(ORDER, flat):
        square = cut(sheet, spans, args.pad)
        square.save(args.out / f"{name}.png")
        print(f"  {name}.png  {square.width}x{square.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
