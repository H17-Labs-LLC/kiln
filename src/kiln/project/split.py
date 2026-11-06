"""`kiln dataset split --holdout <n> [--apply]`: place the unassigned kept images on train or holdout, lineage-safe.

Only kept images with `split: unassigned` are placed; images that already have a side keep it, and their lineage
follows them. A lineage is one observation (a picture, its crops, flips and edits), so it moves whole: the holdout
can come out a little above or below `<n>`, and the proposal says by how much.

`<n>` is the holdout size the person asked for, counting the images already held out. The pick, ported from the
earlier split planner: smallest free lineages first (they cost the training set least); among equals the one
sharing the fewest labels (viewpoint, framing and the namespaced labels) with what is already held out, so no one
kind of image is drained; then the root id, so the plan is stable. A lineage holding a CORE image or an
`anchor:` label is never held out: its identity is in the training set either way.

Without `--apply` the proposal is printed and nothing is written.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field

from . import images as images_mod
from . import lineage
from .captions import training_images
from .model import Project


@dataclass
class Proposal:
    asked: int
    sides: dict[str, str] = field(default_factory=dict)     # image id -> train|holdout, unassigned images only
    lineages: int = 0
    train: int = 0
    holdout: int = 0

    @property
    def new_holdout(self) -> list[str]:
        return sorted(i for i, s in self.sides.items() if s == "holdout")


def _labels(record: Mapping) -> list[str]:
    out = [f"{k}:{record[k]}" for k in ("viewpoint", "framing") if record.get(k)]
    return out + [lab for lab in record.get("labels") or [] if not lab.startswith("check:")]


def _protected(records: list[Mapping]) -> bool:
    return any(r.get("tier") == "CORE" or any(lab.startswith("anchor:") for lab in r.get("labels") or [])
               for r in records)


def propose(project: Project, holdout: int) -> Proposal:
    images = images_mod.records(project)
    kept = training_images(images)
    by_root = lineage.groups(sorted(kept), images)
    side: dict[str, str] = {}
    for root, members in by_root.items():   # an existing assignment decides the lineage
        fixed = {kept[i]["split"] for i in members if kept[i]["split"] in ("train", "holdout")}
        if len(fixed) == 1:
            side[root] = fixed.pop()
        elif fixed:  # already on both sides (a leak): its new images go to train; `check --leaks` names it
            side[root] = "train"
    held = sum(1 for i in kept if kept[i]["split"] == "holdout")
    taken: Counter = Counter(lab for i in kept if kept[i]["split"] == "holdout" for lab in _labels(kept[i]))
    free = sorted((r for r in by_root if r not in side and not _protected([kept[i] for i in by_root[r]])),
                  key=lambda r: (len(by_root[r]), r))
    while held < holdout and free:
        size = len(by_root[free[0]])
        tier = [r for r in free if len(by_root[r]) == size]
        pick = min(tier, key=lambda r: (sum(taken[lab] for i in by_root[r] for lab in _labels(kept[i])), r))
        if abs(held + size - holdout) > abs(held - holdout) and held > 0:
            break  # taking it would land farther from <n> than stopping here
        free.remove(pick)
        side[pick] = "holdout"
        held += size
        taken.update(lab for i in by_root[pick] for lab in _labels(kept[i]))
    proposal = Proposal(asked=holdout, lineages=len(by_root))
    for root, members in by_root.items():
        target = side.get(root, "train")
        for image_id in members:
            if kept[image_id]["split"] == "unassigned":
                proposal.sides[image_id] = target
    final = {i: proposal.sides.get(i, kept[i]["split"]) for i in kept}
    proposal.train = sum(1 for s in final.values() if s == "train")
    proposal.holdout = sum(1 for s in final.values() if s == "holdout")
    return proposal


def apply(project: Project, proposal: Proposal, *, by: str = "kiln dataset split --apply") -> int:
    """Record the proposal as one assign line per placed image; returns how many were written."""
    with project.lock():  # each line builds on the latest record of its image
        images = images_mod.records(project)
        new_lines = [images_mod.changed_line(images[i], "assign", by, {"split": s})
                     for i, s in sorted(proposal.sides.items())]
        if new_lines:
            images_mod.write(project, new_lines)
    return len(new_lines)
