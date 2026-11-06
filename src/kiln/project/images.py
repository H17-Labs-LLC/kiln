"""Image records: register, label, assign a split, promote a tier, register a child; read the latest state.

`images.jsonl` is an event log: each line is the image's complete record after one event, and the latest line
per id wins. Images are never opened for content: a tool reads the bytes' sha256, size and the header's
dimensions, nothing else. Verdicts (dispositions) are the person's; a command records them only when told
(`kiln images label --disposition ...`, the triage sheet, or an easel review), with `verdict_source` saying who.

An image id is `img-<sha256[:12]>`: the same bytes registered twice are one image (the second time is a no-op).
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TypeVar

from kiln._common import hashing, ids, store

from . import lineage
from .model import KEPT, SCHEMA, Project, ProjectError, event_line, now

TRANSFORMS = ("crop", "flip", "edit", "enhance", "upscale")
# A generative step invents pixels: such a child is marked synthetic_detail_added = 1.
GENERATIVE = ("edit", "enhance")
VIEWPOINTS = ("front", "front_three_quarter", "profile", "back", "rear_three_quarter", "unusual")
FRAMINGS = ("close", "medium", "full", "wide")
_FORMATS = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}
LABEL = re.compile(r"^[a-z][a-z0-9_-]*:\S+$")
T = TypeVar("T")


# --- reading ----------------------------------------------------------------------------------------------------

def records(project: Project) -> dict[str, dict]:
    """image id -> its latest record."""
    return store.latest_by_key(project.images_log, "id")


def get(images: Mapping[str, dict], image_id: str) -> dict:
    if image_id not in images:
        raise ProjectError(2, f"unknown image {image_id!r}: `kiln images list` shows the ids")
    return images[image_id]


def is_kept(record: Mapping) -> bool:
    """Kept for training: an accepting disposition, not reference-only, not rejected."""
    return (record.get("disposition") in KEPT and record.get("split") != "reference_only"
            and record.get("tier") != "REJECTED")


def is_untriaged(record: Mapping) -> bool:
    """No verdict yet: no disposition and not rejected by tier."""
    return record.get("disposition") is None and record.get("tier") != "REJECTED"


def is_legacy_untriaged(record: Mapping) -> bool:
    return bool(record.get("legacy_untriaged")) and record.get("disposition") is None


# --- writing ----------------------------------------------------------------------------------------------------

def _next_line(current: Mapping, event: str, by: str, changes: Mapping[str, object]) -> dict:
    """The record after `event`: the current record with `changes`, a fresh `at` and the command in `by`."""
    data = {k: v for k, v in current.items() if k not in ("schema", "at", "v", "source", "by", "event")}
    data.update(changes)
    return event_line(SCHEMA["image"], {"event": event, **data}, source=current["source"], by=by)


def write(project: Project, lines: list[dict]) -> None:
    project.append(project.images_log, lines)


def change(project: Project, image_id: str, event: str, by: str, changes: Mapping[str, object],
           images: Mapping[str, dict] | None = None) -> dict:
    """Append one event that changes one image; returns the new line.

    The image's current record is read under the project lock, so the new line builds on the latest state even
    when another session wrote in between. A caller that passes `images` read them itself; it holds the lock
    around that read and this call (`with project.lock():`)."""
    with project.lock():
        current = get(images if images is not None else records(project), image_id)
        line = _next_line(current, event, by, changes)
        write(project, [line])
    return line


def changed_line(current: Mapping, event: str, by: str, changes: Mapping[str, object]) -> dict:
    """The line `change` would append, for callers that write many lines in one go (`dataset split --apply`)."""
    return _next_line(current, event, by, changes)


def probe(path: Path) -> dict:
    """sha256, size and the header's dimensions; the pixels are never decoded."""
    info: dict = {"sha256": hashing.sha256_file(path), "bytes": path.stat().st_size,
                  "width": None, "height": None, "format": None, "color_mode": None}
    try:
        from PIL import Image  # reads the header only until pixel data is asked for

        with Image.open(path) as image:
            info["width"], info["height"] = image.size
            info["format"] = _FORMATS.get(str(image.format))
            info["color_mode"] = image.mode
    except Exception:  # noqa: BLE001 - an unreadable header leaves the dimensions unknown, never fails the call
        pass
    return info


def locate(project: Project, path: Path) -> tuple[str, str]:
    """(root name, file relative to that root) for a file inside one of the project's roots."""
    target = Path(path).resolve()
    for name in ("imports", "images", "legacy_output"):
        try:
            folder = project.root_dir(name)
        except ProjectError:
            continue
        try:
            rel = target.relative_to(folder)
        except ValueError:
            continue
        return name, rel.as_posix()
    raise ProjectError(2, [f"{project.shown(target)} is not inside one of the project's image roots",
                           "copy it under the imports root first: " + project.shown(project.root_dir("imports"))])


def check_view(viewpoint: str | None, framing: str | None) -> None:
    """Exit 2 on a viewpoint or framing outside the vocabulary (None means unknown and is fine)."""
    if viewpoint is not None and viewpoint not in VIEWPOINTS:
        raise ProjectError(2, f"a viewpoint is one of {', '.join(VIEWPOINTS)}: {viewpoint!r}")
    if framing is not None and framing not in FRAMINGS:
        raise ProjectError(2, f"a framing is one of {', '.join(FRAMINGS)}: {framing!r}")


def register(project: Project, path: Path, *, source: Mapping[str, object], by: str, event: str = "register",
             tier: str = "CANDIDATE", parent: Mapping | None = None, transform: str = "none",
             grid_cell: list[int] | None = None, content_rating: str | None = None,
             labels: Iterable[str] = (), disposition: str | None = None, split: str = "unassigned",
             verdict_source: str | None = None, caption_basis: str | None = None,
             notes: str | None = None, viewpoint: str | None = None, framing: str | None = None,
             images: Mapping[str, dict] | None = None) -> tuple[dict, bool]:
    """Register a file that lies in one of the project's roots; returns (record, created).

    The same bytes already registered return the existing record and `created = False` (nothing is written).
    `viewpoint` and `framing` (what the job asked for, or the operator's notes) go into the one register line, so
    a source never needs a second label line for them. Without `images` the known records are read under the
    project lock; a caller registering many files reads them once itself, inside `with project.lock():`.
    """
    path = Path(path)
    if not path.is_file():
        raise ProjectError(2, f"no such file: {project.shown(path)}")
    check_view(viewpoint, framing)
    info = probe(path)
    image_id = ids.image_id(info["sha256"])
    with project.lock():
        known = images if images is not None else records(project)
        if image_id in known:
            return known[image_id], False
        return _register_new(project, path, info, image_id, known, source=source, by=by, event=event, tier=tier,
                             parent=parent, transform=transform, grid_cell=grid_cell,
                             content_rating=content_rating, labels=labels, disposition=disposition, split=split,
                             verdict_source=verdict_source, caption_basis=caption_basis, notes=notes,
                             viewpoint=viewpoint, framing=framing), True


def _register_new(project: Project, path: Path, info: dict, image_id: str, known: Mapping[str, dict], *,
                  source: Mapping[str, object], by: str, event: str, tier: str, parent: Mapping | None,
                  transform: str, grid_cell: list[int] | None, content_rating: str | None, labels: Iterable[str],
                  disposition: str | None, split: str, verdict_source: str | None, caption_basis: str | None,
                  notes: str | None, viewpoint: str | None, framing: str | None) -> dict:
    """Write the register line of a new image (the caller holds the project lock)."""
    root_name, rel = locate(project, path)
    if parent is not None:
        root = lineage.root_of(parent["id"], known)
        lin = {"parent": parent["id"], "root": root, "transform": transform, "grid_cell": grid_cell,
               "synthetic_detail_added": 1 if transform in GENERATIVE else 0}
    else:
        lin = {"parent": None, "root": image_id, "transform": "none", "grid_cell": None,
               "synthetic_detail_added": 0}
    rating = content_rating or (parent or {}).get("content_rating") or project.data.get("content_rating", "unknown")
    record = {
        "event": event, "id": image_id, "legacy_id": None, "root": root_name, "file": rel,
        "sha256": info["sha256"], "bytes": info["bytes"], "width": info["width"], "height": info["height"],
        "format": info["format"], "color_mode": info["color_mode"], "tier": tier, "disposition": disposition,
        "split": split, "viewpoint": viewpoint, "framing": framing, "labels": sorted(set(labels)), "lineage": lin,
        "scores": None, "content_rating": rating, "verdict_source": verdict_source, "legacy_untriaged": False,
        "caption_basis": caption_basis, "created_at": now(), "notes": notes,
    }
    line = event_line(SCHEMA["image"], record, source=dict(source), by=by)
    write(project, [line])
    return line


def _locked(func: Callable[..., T]) -> Callable[..., T]:
    """Run a read-decide-append function under the project lock (re-entrant), so its read is never stale."""
    @functools.wraps(func)
    def wrapper(project: Project, *args, **kwargs) -> T:
        with project.lock():
            return func(project, *args, **kwargs)

    return wrapper


@_locked
def child(project: Project, parent_id: str, path: Path, transform: str, *, by: str,
          grid_cell: list[int] | None = None, notes: str | None = None) -> tuple[dict, bool]:
    """Register a derived image as a child of `parent_id`; it joins the parent's lineage."""
    if transform not in TRANSFORMS:
        raise ProjectError(2, f"a transform is one of {', '.join(TRANSFORMS)}: {transform!r}")
    images = records(project)
    parent = get(images, parent_id)
    source = dict(parent.get("source") or {"kind": "unknown"})
    source["note"] = f"{transform} of {parent_id}"
    return register(project, path, source=source, by=by, event="child", parent=parent, transform=transform,
                    grid_cell=grid_cell, notes=notes, images=images)


@_locked
def label(project: Project, image_id: str, *, by: str, add: Iterable[str] = (), remove: Iterable[str] = (),
          viewpoint: str | None = None, framing: str | None = None, disposition: str | None = None,
          note: str | None = None) -> dict:
    """Record the person's labels (and, with `disposition`, their verdict) on one image.

    A verdict also settles what follows from it: REJECT moves the tier to REJECTED and the split back to
    unassigned (a rejected image never trains); ACCEPT_REFERENCE_ONLY moves the split to reference_only.
    """
    check_view(viewpoint, framing)
    images = records(project)
    current = get(images, image_id)
    changes: dict = {}
    labels = set(current.get("labels") or [])
    for item in add:
        if not LABEL.match(item):
            raise ProjectError(2, f"a label is namespace:value, for example outfit:default: {item!r}")
        labels.add(item)
    labels -= set(remove)
    if labels != set(current.get("labels") or []):
        changes["labels"] = sorted(labels)
    if viewpoint is not None:
        changes["viewpoint"] = viewpoint
    if framing is not None:
        changes["framing"] = framing
    if note is not None:
        changes["notes"] = note
    event = "label"
    if disposition is not None:
        changes["disposition"] = disposition
        changes["verdict_source"] = "operator"
        changes["legacy_untriaged"] = False
        if disposition == "REJECT":
            changes["tier"] = "REJECTED"
            changes["split"] = "unassigned"
        elif disposition == "ACCEPT_REFERENCE_ONLY":
            changes["split"] = "reference_only"
        event = "triage"
    if not changes:
        raise ProjectError(2, "nothing to record: pass --label, --remove-label, --viewpoint, --framing, "
                              "--disposition or --note")
    return change(project, image_id, event, by, changes, images)


@_locked
def assign(project: Project, image_id: str, split: str, *, by: str,
           images: Mapping[str, dict] | None = None) -> tuple[dict, list[str]]:
    """Assign one image to a split; returns the new line and warning lines. A rejected image never trains."""
    images = images if images is not None else records(project)
    current = get(images, image_id)
    if split in ("train", "holdout") and (current.get("tier") == "REJECTED" or current.get("disposition") == "REJECT"):
        raise ProjectError(2, f"{image_id} is rejected: refusing to assign it to {split}")
    warnings = []
    if split in ("train", "holdout") and current.get("disposition") not in KEPT:
        warnings.append(f"note: {image_id} has disposition {current.get('disposition')}; only "
                        f"{' and '.join(KEPT)} images are frozen into a version")
    if split in ("train", "holdout"):
        assignment = {i: r.get("split") for i, r in images.items() if r.get("split") in ("train", "holdout")}
        assignment[image_id] = split
        if lineage.leaks(assignment, images, "current", ("train", "holdout")):
            root = lineage.root_of(image_id, images)
            warnings.append(f"warn: lineage {root} now sits on train and holdout; `kiln dataset check --leaks` "
                            "will refuse it until the whole lineage is on one side")
    line = change(project, image_id, "assign", by, {"split": split}, images)
    return line, warnings


@_locked
def promote(project: Project, image_id: str, tier: str, *, by: str, reason: str | None = None) -> tuple[str, dict]:
    """Change an image's trust tier; the reason goes into its notes. Returns (old tier, new line)."""
    images = records(project)
    current = get(images, image_id)
    old = current.get("tier")
    notes = current.get("notes") or ""
    entry = f"[{now()}] tier {old} -> {tier}" + (f" ({reason})" if reason else "")
    changes: dict = {"tier": tier, "notes": (notes + "\n" + entry).strip()}
    return old, change(project, image_id, "promote", by, changes, images)
