"""Lineage closure: which images are one observation, so a split never puts one family on both sides.

A crop, flip, edit or upscale of an image restates its parent; it is not new evidence. Every image therefore
belongs to one lineage, named by its root: `lineage.root` when the record carries it, else the top of its
`lineage.parent` chain, else the image itself.

A leak is one lineage with members on more than one side of a split: in a snapshot (any two of train, holdout,
excluded) or in the current assignment (train and holdout).

Used by `kiln dataset split|freeze|check` and `kiln images assign`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


def root_of(image_id: str, images: Mapping[str, Mapping]) -> str:
    """The lineage root of `image_id`: lineage.root, else the top of the parent chain, else itself."""
    seen: set[str] = set()
    current = image_id
    while current not in seen:
        seen.add(current)
        record = images.get(current)
        if record is None:
            return current
        lineage = record.get("lineage") or {}
        if lineage.get("root"):
            return str(lineage["root"])
        parent = lineage.get("parent")
        if not parent:
            return current
        current = str(parent)
    return current  # a parent cycle: the first image met twice names the lineage


def roots(images: Mapping[str, Mapping]) -> dict[str, str]:
    """Every image id -> its lineage root."""
    return {image_id: root_of(image_id, images) for image_id in images}


def groups(image_ids: Iterable[str], images: Mapping[str, Mapping]) -> dict[str, list[str]]:
    """lineage root -> its image ids among `image_ids` (sorted), in order of first appearance."""
    out: dict[str, list[str]] = {}
    for image_id in image_ids:
        out.setdefault(root_of(image_id, images), []).append(image_id)
    return {root: sorted(members) for root, members in out.items()}


@dataclass(frozen=True)
class Leak:
    """One lineage on more than one side: `where` is a snapshot id or `current`."""

    where: str
    root: str
    splits: tuple[str, ...]
    members: tuple[str, ...]

    def line(self) -> str:
        return (f"leak: {self.where} lineage {self.root} on {', '.join(self.splits)} "
                f"({len(self.members)} images: {' '.join(self.members)})")


def leaks(assignment: Mapping[str, str], images: Mapping[str, Mapping], where: str,
          sides: Iterable[str] = ("train", "holdout", "excluded")) -> list[Leak]:
    """The lineages of `assignment` (image id -> split) that sit on more than one of `sides`."""
    wanted = set(sides)
    by_root: dict[str, dict[str, str]] = {}
    for image_id, split in assignment.items():
        if split in wanted:
            by_root.setdefault(root_of(image_id, images), {})[image_id] = split
    out = []
    for root, members in sorted(by_root.items()):
        splits = tuple(sorted(set(members.values())))
        if len(splits) > 1:
            out.append(Leak(where=where, root=root, splits=splits, members=tuple(sorted(members))))
    return out
