"""Config loading, validation, merging and provenance.

Ground rules enforced here:
  1. No magic numbers outside config -> everything the app and the simulator
     need is described by the models below.
  3. Every parameter carries provenance (assumed | observed | fitted).
  7. Fail loudly on bad config -> nothing silently defaults; every failure
     names the offending field.

At M8 `params/observed.yaml` is layered on top of `params/base.yaml` and the
touched subtrees flip to `source: observed` / `source: fitted`. No code change.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

__all__ = [
    "ConfigError",
    "Params",
    "ProvenanceReport",
    "Source",
    "load_params",
    "params_from_dict",
    "parse_hhmm",
    "format_hhmm",
    "deep_merge",
]

Source = Literal["assumed", "observed", "fitted"]
SOURCES: tuple[str, ...] = ("assumed", "observed", "fitted")

FROM_STAFFING = "from_staffing"

# Station cost terms. A station's service time is the sum of the terms it
# declares. `scale` names the Task attribute the term is multiplied by, and
# `per` says whether the term is paid once per order, once per batch, or once
# per item. This table is what keeps station behaviour declarative: adding a
# station to the YAML never requires new Python.
PER_ORDER = "order"
PER_BATCH = "batch"
PER_ITEM = "item"

SECONDS_PER_MINUTE = 60
OUNCES_PER_STEAM_UNIT = 6.0  # `per_6oz_s` is quoted per this many ounces

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ConfigError(Exception):
    """Raised for any malformed or nonsensical configuration."""


def parse_hhmm(value: str, *, field: str = "time") -> int:
    """'07:00' -> seconds since midnight. Raises ConfigError, never guesses."""
    if not isinstance(value, str) or not _HHMM.match(value):
        raise ConfigError(f"{field}: {value!r} is not a HH:MM clock time")
    hours, minutes = value.split(":")
    return (int(hours) * 60 + int(minutes)) * SECONDS_PER_MINUTE


def format_hhmm(seconds: float) -> str:
    total = int(seconds) // SECONDS_PER_MINUTE
    return f"{total // 60:02d}:{total % 60:02d}"


def deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive merge. Mappings merge key-wise; every other value is replaced.

    Lists are replaced wholesale on purpose: a half-overridden staffing plan or
    class-block schedule is never what an overlay means.
    """
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], Mapping) and isinstance(value, Mapping):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class _Strict(BaseModel):
    """Unknown keys are errors, not defaults (ground rule 7)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class MetaParams(_Strict):
    scenario: str
    seed: int = Field(ge=0)
    sim_start: str
    sim_end: str
    source: Source

    @property
    def start_s(self) -> int:
        return parse_hhmm(self.sim_start, field="meta.sim_start")

    @property
    def end_s(self) -> int:
        return parse_hhmm(self.sim_end, field="meta.sim_end")

    @model_validator(mode="after")
    def _window_is_forward(self) -> "MetaParams":
        if self.end_s <= self.start_s:
            raise ValueError("sim_end must be after sim_start")
        return self


class CafeParams(_Strict):
    name: str
    timezone: str
    source: Source | None = None

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {value!r}") from exc
        return value


class StationParams(_Strict):
    """A resource with a service-time formula.

    Exactly one capacity, plus whichever cost terms apply. `capacity:
    from_staffing` means the station is manned rather than machine-limited and
    its parallelism follows the staffing plan.
    """

    capacity: int | Literal["from_staffing"]
    base_s: float | None = Field(default=None, ge=0)      # per order
    setup_s: float | None = Field(default=None, ge=0)     # per batch
    per_6oz_s: float | None = Field(default=None, ge=0)   # per item, x oz/6
    shot_s: float | None = Field(default=None, ge=0)      # per item, x shots
    run_s: float | None = Field(default=None, ge=0)       # per batch if batch_size, else per item
    pour_s: float | None = Field(default=None, ge=0)      # per item
    batch_key: str | None = None
    max_batch_oz: float | None = Field(default=None, gt=0)
    batch_size: int | None = Field(default=None, gt=0)
    blocking: bool = False
    source: Source | None = None

    @field_validator("capacity")
    @classmethod
    def _capacity_positive(cls, value: int | str) -> int | str:
        if isinstance(value, int) and value < 1:
            raise ValueError("capacity must be >= 1 or the string 'from_staffing'")
        return value

    @model_validator(mode="after")
    def _has_a_cost(self) -> "StationParams":
        if not self.cost_terms:
            raise ValueError(
                "station declares no cost term "
                "(expected one of base_s, setup_s, per_6oz_s, shot_s, run_s, pour_s)"
            )
        if self.max_batch_oz is not None and self.per_6oz_s is None:
            raise ValueError("max_batch_oz is meaningless without per_6oz_s")
        return self

    @property
    def cost_terms(self) -> dict[str, tuple[float, str, str | None]]:
        """name -> (seconds, per-what, Task attribute it scales with)."""
        run_per = PER_BATCH if self.batch_size is not None else PER_ITEM
        candidates = {
            "base_s": (self.base_s, PER_ORDER, None),
            "setup_s": (self.setup_s, PER_BATCH, None),
            "per_6oz_s": (self.per_6oz_s, PER_ITEM, "oz"),
            "shot_s": (self.shot_s, PER_ITEM, "shots"),
            "run_s": (self.run_s, run_per, None),
            "pour_s": (self.pour_s, PER_ITEM, None),
        }
        return {k: (v, per, attr) for k, (v, per, attr) in candidates.items() if v is not None}

    @property
    def batches(self) -> bool:
        return self.batch_key is not None or self.batch_size is not None or self.base_s is not None


class TaskSpec(_Strict):
    station: str
    oz: float | None = Field(default=None, gt=0)
    shots: int | None = Field(default=None, gt=0)


class MenuItemParams(_Strict):
    price_cents: int = Field(gt=0)
    cogs_cents: int = Field(ge=0)
    requires_milk: bool
    tasks: list[TaskSpec] = Field(min_length=1)
    assembly_s: float = Field(ge=0)
    source: Source | None = None

    @model_validator(mode="after")
    def _margin_is_positive(self) -> "MenuItemParams":
        if self.cogs_cents >= self.price_cents:
            raise ValueError("cogs_cents must be below price_cents")
        return self


class MixParams(_Strict):
    drink: dict[str, float]
    milk: dict[str, float]
    attach_rate: float = Field(ge=0, le=1)
    source: Source | None = None

    @field_validator("drink", "milk")
    @classmethod
    def _is_a_distribution(cls, value: dict[str, float], info) -> dict[str, float]:
        if not value:
            raise ValueError("must not be empty")
        for key, share in value.items():
            if share < 0:
                raise ValueError(f"share for {key!r} is negative")
        total = sum(value.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"shares must sum to 1.0, got {total:.6f}")
        return value


class ClassBlock(_Strict):
    ends_at: str
    sections: int = Field(gt=0)
    avg_enrollment: float = Field(gt=0)
    building: str | None = None
    walk_minutes: float | None = Field(default=None, ge=0)
    source: Source | None = None

    @property
    def ends_at_s(self) -> int:
        return parse_hhmm(self.ends_at, field="arrivals.class_blocks.ends_at")


class ArrivalsParams(_Strict):
    class_blocks: list[ClassBlock] = Field(min_length=1)
    capture_rate: float = Field(gt=0, le=1)
    offset_min: float
    sigma_min: float = Field(gt=0)
    background_per_hour: float = Field(ge=0)
    source: Source | None = None


class LogNormalDist(_Strict):
    dist: Literal["lognormal"]
    median: float = Field(gt=0)
    sigma: float = Field(gt=0)


class NormalDist(_Strict):
    dist: Literal["normal"]
    mean: float
    sigma: float = Field(gt=0)


class ConstantDist(_Strict):
    dist: Literal["constant"]
    value: float


Dist = Annotated[
    LogNormalDist | NormalDist | ConstantDist,
    Field(discriminator="dist"),
]


class CustomersParams(_Strict):
    time_budget_min: Dist
    balk_tolerance_min: Dist
    preorder_adoption: float = Field(ge=0, le=1)
    no_show_rate: float = Field(ge=0, le=1)
    source: Source | None = None


class PolicyParams(_Strict):
    name: Literal["fifo", "batch_milk", "bounded_reorder"]
    lookahead_s: float = Field(ge=0)
    reorder_window_s: float = Field(ge=0)
    starvation_guard_s: float = Field(gt=0)
    source: Source | None = None


class SlotsParams(_Strict):
    enabled: bool
    width_min: float = Field(gt=0)
    walkup_reserve_fraction: float = Field(ge=0, le=1)
    min_lead_time_min: float = Field(ge=0)
    source: Source | None = None


class StaffingBlock(_Strict):
    from_: str = Field(alias="from")
    to: str
    baristas: int = Field(ge=1)
    source: Source | None = None

    @property
    def from_s(self) -> int:
        return parse_hhmm(self.from_, field="staffing.from")

    @property
    def to_s(self) -> int:
        return parse_hhmm(self.to, field="staffing.to")

    @model_validator(mode="after")
    def _forward(self) -> "StaffingBlock":
        if self.to_s <= self.from_s:
            raise ValueError(f"staffing block {self.from_}-{self.to} does not move forward")
        return self


class ProvenanceReport(BaseModel):
    """What fraction of the inputs is still guessed (ground rule 3)."""

    counts: dict[str, int]
    total: int

    def fraction(self, source: str) -> float:
        return self.counts.get(source, 0) / self.total if self.total else 0.0

    @property
    def assumed_fraction(self) -> float:
        return self.fraction("assumed")

    def caption(self) -> str:
        """The string every figure and table has to carry."""
        return f"provenance: {round(100 * self.assumed_fraction)}% assumed"


class Params(_Strict):
    meta: MetaParams
    cafe: CafeParams
    stations: dict[str, StationParams]
    bottleneck_station: str
    staffing: list[StaffingBlock] = Field(min_length=1)
    menu: dict[str, MenuItemParams]
    mix: MixParams
    arrivals: ArrivalsParams
    customers: CustomersParams
    policy: PolicyParams
    slots: SlotsParams

    # populated by params_from_dict, not by YAML
    provenance: dict[str, Source] = Field(default_factory=dict, exclude=True)

    @field_validator("stations", "menu", mode="before")
    @classmethod
    def _drop_block_source(cls, value: Any) -> Any:
        """`source` sits beside the named entries in these blocks; it is
        provenance metadata, not a station or a menu item."""
        if isinstance(value, Mapping):
            return {k: v for k, v in value.items() if k != "source"}
        return value

    @model_validator(mode="after")
    def _cross_checks(self) -> "Params":
        problems: list[str] = []

        if self.bottleneck_station not in self.stations:
            problems.append(
                f"bottleneck_station: {self.bottleneck_station!r} is not a station "
                f"(have {sorted(self.stations)})"
            )

        for name, item in self.menu.items():
            for index, task in enumerate(item.tasks):
                where = f"menu.{name}.tasks[{index}]"
                station = self.stations.get(task.station)
                if station is None:
                    problems.append(f"{where}.station: unknown station {task.station!r}")
                    continue
                if station.per_6oz_s is not None and task.oz is None:
                    problems.append(f"{where}: station {task.station!r} needs `oz`")
                if station.shot_s is not None and task.shots is None:
                    problems.append(f"{where}: station {task.station!r} needs `shots`")
                if task.oz is not None and station.per_6oz_s is None:
                    problems.append(f"{where}: station {task.station!r} does not use `oz`")
                if task.shots is not None and station.shot_s is None:
                    problems.append(f"{where}: station {task.station!r} does not use `shots`")

        mix_only = set(self.mix.drink) - set(self.menu)
        menu_only = set(self.menu) - set(self.mix.drink)
        if mix_only:
            problems.append(f"mix.drink: {sorted(mix_only)} are not on the menu")
        if menu_only:
            problems.append(f"mix.drink: menu items {sorted(menu_only)} have no share")

        blocks = sorted(self.staffing, key=lambda b: b.from_s)
        if blocks[0].from_s != self.meta.start_s:
            problems.append(
                f"staffing: first block starts {blocks[0].from_} but sim_start is {self.meta.sim_start}"
            )
        if blocks[-1].to_s != self.meta.end_s:
            problems.append(
                f"staffing: last block ends {blocks[-1].to} but sim_end is {self.meta.sim_end}"
            )
        for earlier, later in zip(blocks, blocks[1:]):
            if earlier.to_s != later.from_s:
                problems.append(
                    f"staffing: gap or overlap between {earlier.to} and {later.from_}"
                )

        for index, block in enumerate(self.arrivals.class_blocks):
            if not (self.meta.start_s <= block.ends_at_s <= self.meta.end_s):
                problems.append(
                    f"arrivals.class_blocks[{index}].ends_at: {block.ends_at} is outside "
                    f"{self.meta.sim_start}-{self.meta.sim_end}"
                )

        if problems:
            raise ValueError("; ".join(problems))
        return self

    # ---- lookups -------------------------------------------------------

    def station(self, name: str) -> StationParams:
        try:
            return self.stations[name]
        except KeyError:
            raise ConfigError(f"no such station: {name!r}") from None

    def menu_item(self, name: str) -> MenuItemParams:
        try:
            return self.menu[name]
        except KeyError:
            raise ConfigError(f"no such menu item: {name!r}") from None

    @property
    def bottleneck(self) -> StationParams:
        return self.stations[self.bottleneck_station]

    def baristas_at(self, t_s: float) -> int:
        """Staffing level at a time given in seconds since midnight."""
        for block in self.staffing:
            if block.from_s <= t_s < block.to_s:
                return block.baristas
        raise ConfigError(f"no staffing block covers {format_hhmm(t_s)}")

    @property
    def max_baristas(self) -> int:
        return max(block.baristas for block in self.staffing)

    def station_capacity(self, name: str, t_s: float | None = None) -> int:
        """Resolves `from_staffing` against the staffing plan."""
        station = self.station(name)
        if station.capacity == FROM_STAFFING:
            return self.baristas_at(t_s) if t_s is not None else self.max_baristas
        return int(station.capacity)

    # ---- provenance ----------------------------------------------------

    def provenance_report(self, prefix: str | None = None) -> ProvenanceReport:
        counts: dict[str, int] = {}
        total = 0
        for path, source in self.provenance.items():
            if prefix is not None and not path.startswith(prefix):
                continue
            counts[source] = counts.get(source, 0) + 1
            total += 1
        return ProvenanceReport(counts=counts, total=total)

    def source_of(self, path: str) -> Source:
        try:
            return self.provenance[path]
        except KeyError:
            raise ConfigError(f"no parameter at {path!r}") from None


def _walk_provenance(
    node: Any, path: list[str], inherited: str | None, out: dict[str, str]
) -> None:
    if isinstance(node, Mapping):
        source = node.get("source", inherited)
        if source is not None and source not in SOURCES:
            raise ConfigError(
                f"{'.'.join(path + ['source'])}: {source!r} is not one of {SOURCES}"
            )
        for key, value in node.items():
            if key == "source":
                continue
            _walk_provenance(value, path + [str(key)], source, out)
    elif isinstance(node, Sequence) and not isinstance(node, (str, bytes)):
        for index, value in enumerate(node):
            _walk_provenance(value, path + [str(index)], inherited, out)
    else:
        dotted = ".".join(path)
        if inherited is None:
            raise ConfigError(
                f"{dotted}: no provenance. Add `source: assumed|observed|fitted` "
                f"to this parameter or an enclosing block."
            )
        out[dotted] = inherited


def params_from_dict(raw: Mapping[str, Any]) -> Params:
    """Validate a merged config mapping. Raises ConfigError naming the field."""
    if not isinstance(raw, Mapping):
        raise ConfigError(f"config root must be a mapping, got {type(raw).__name__}")

    default_source = None
    meta = raw.get("meta")
    if isinstance(meta, Mapping):
        default_source = meta.get("source")

    provenance: dict[str, str] = {}
    _walk_provenance(dict(raw), [], default_source, provenance)

    try:
        params = Params.model_validate(dict(raw))
    except ValidationError as exc:
        lines = []
        for error in exc.errors():
            where = ".".join(str(part) for part in error["loc"]) or "<root>"
            lines.append(f"  {where}: {error['msg']}")
        raise ConfigError(
            "invalid configuration:\n" + "\n".join(lines)
        ) from None

    params.provenance = provenance  # type: ignore[assignment]
    return params


def load_params(*paths: str | Path) -> Params:
    """Load base config plus overlays, left to right. Later files win.

        load_params("params/base.yaml", "params/observed.yaml")
    """
    if not paths:
        raise ConfigError("load_params needs at least one file")

    merged: dict[str, Any] = {}
    for path in paths:
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config file not found: {p}")
        try:
            loaded = yaml.safe_load(p.read_text())
        except yaml.YAMLError as exc:
            raise ConfigError(f"{p}: not valid YAML: {exc}") from None
        if loaded is None:
            raise ConfigError(f"{p}: file is empty")
        if not isinstance(loaded, Mapping):
            raise ConfigError(f"{p}: top level must be a mapping")
        merged = deep_merge(merged, loaded)

    try:
        return params_from_dict(merged)
    except ConfigError as exc:
        raise ConfigError(f"{' + '.join(str(p) for p in paths)}\n{exc}") from None
