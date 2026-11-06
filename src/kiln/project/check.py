"""The dataset's census and leak checks as functions over the project's files.

- `leaks()`: per snapshot, a lineage root whose members sit on more than one split; plus the current assignment
  (train and holdout of the kept images), which is what the next freeze would see (`kiln dataset check --leaks`).
- `reference_census()`: per tier, how many images, how many with a short side under 768 px, how many with
  unknown dimensions (`kiln dataset census`).
- `split_census()`: per snapshot and split, how many members; plus the current split of the kept images, so an
  `unassigned` one shows before the freeze (`kiln dataset census --split`).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from . import images as images_mod
from . import lineage
from . import snapshot as snapshot_mod
from .captions import training_images
from .model import TIERS, Project

SHORT_SIDE = 768


def leaks(project: Project) -> list[lineage.Leak]:
    images = images_mod.records(project)
    current = {i: r["split"] for i, r in training_images(images).items() if r.get("split") in ("train", "holdout")}
    found = lineage.leaks(current, images, "current", ("train", "holdout"))
    for version in snapshot_mod.versions(project):
        snap = snapshot_mod.load(project, version)
        found += lineage.leaks({m["image"]: m["split"] for m in snap["members"]}, images, snap["id"])
    return found


@dataclass(frozen=True)
class TierRow:
    tier: str
    n: int
    under_768_short_side: int
    unknown_dims: int


def reference_census(project: Project) -> list[TierRow]:
    """One row per tier present, in trust order (CORE first)."""
    rows: dict[str, list[int]] = {}
    for record in images_mod.records(project).values():
        row = rows.setdefault(record.get("tier", "CANDIDATE"), [0, 0, 0])
        row[0] += 1
        width, height = record.get("width"), record.get("height")
        if width is None:
            row[2] += 1
        elif height is not None and min(width, height) < SHORT_SIDE:  # a missing height is unknown, not short
            row[1] += 1
    order = {t: n for n, t in enumerate(TIERS)}
    return [TierRow(t, *v) for t, v in sorted(rows.items(), key=lambda kv: (order.get(kv[0], 99), kv[0]))]


@dataclass(frozen=True)
class SplitRow:
    where: str        # a snapshot id, or "current"
    split: str
    n: int


def split_census(project: Project) -> list[SplitRow]:
    images = images_mod.records(project)
    current = Counter(r.get("split", "unassigned") for r in training_images(images).values())
    rows = [SplitRow("current", s, current[s]) for s in ("train", "holdout", "unassigned") if current[s]]
    for version in snapshot_mod.versions(project):
        snap = snapshot_mod.load(project, version)
        counts = Counter(m["split"] for m in snap["members"])
        rows += [SplitRow(snap["id"], s, counts[s]) for s in sorted(counts)]
    return rows
