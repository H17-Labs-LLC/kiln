"""`kiln dataset freeze <version> [--from <old> | --only-new]`: an immutable dataset version.

Which images a new version holds:
- `--from <old>`: every member of `<old>` (its split, repeat and sha256 as they were) plus every kept image that
  is in no snapshot yet. The default when a goal names `from_version` for this version, and on an adopted
  project (`--from <latest snapshot>`).
- `--only-new`: only the kept images that are in no snapshot yet. The default on a native project without a goal
  (for a first version that is every kept image); when earlier versions exist, the freeze prints a note that their
  members were left out and how to keep them (`--from <old-version>`).
A new image keeps its split; one still `unassigned` joins train, and the freeze prints a note naming those images
(run `kiln dataset split` first to hold some out). Captions: the image's approved caption at freeze time, else the
old version's caption for an old member.

Freeze refuses (exit 2, nothing written) when the version exists (snapshots are immutable), when a train member
has no caption, or when a lineage sits on two sides of the split. The plan and the write happen under the
project's store lock. The record is `snapshots/<version>.json` with `members_sha256` = sha256 of
`json.dumps(members, sort_keys=True, separators=(",", ":"))`, members sorted by image id.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import store

from . import captions as captions_mod
from . import images as images_mod
from . import lineage
from .model import SCHEMA, Project, ProjectError, check_version, now, version_key


def snapshot_path(project: Project, version: str) -> Path:
    return project.snapshots_dir / f"{version}.json"


def versions(project: Project) -> list[str]:
    """Every frozen version, oldest first."""
    if not project.snapshots_dir.is_dir():
        return []
    found = [p.stem for p in project.snapshots_dir.glob("*.json") if ".tmp-" not in p.name]
    return sorted((v for v in found if v.count(".") == 2 and all(x.isdigit() for x in v.split("."))), key=version_key)


def latest_version(project: Project) -> str | None:
    found = versions(project)
    return found[-1] if found else None


def load(project: Project, version: str) -> dict:
    path = snapshot_path(project, version)
    if not path.is_file():
        known = ", ".join(versions(project)) or "none"
        raise ProjectError(2, [f"no snapshot {project.slug}/{version} (frozen versions: {known})",
                               "`kiln project show` lists the snapshots"])
    return store.read_record(path, "kiln/snapshot/")


def members_sha256(members: list[dict]) -> str:
    ordered = sorted(members, key=lambda m: m["image"])
    return hashlib.sha256(json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass
class Plan:
    """What a freeze would write, and why it would refuse."""

    version: str
    mode: str                  # "from" or "only-new"
    from_version: str | None
    why_mode: str
    old: list[dict] = field(default_factory=list)
    new: list[dict] = field(default_factory=list)
    joined_train: list[str] = field(default_factory=list)
    uncaptioned: list[str] = field(default_factory=list)
    holdout_uncaptioned: list[str] = field(default_factory=list)
    no_longer_kept: list[str] = field(default_factory=list)
    leaks: list[lineage.Leak] = field(default_factory=list)
    exists: bool = False

    @property
    def members(self) -> list[dict]:
        return sorted(self.old + self.new, key=lambda m: m["image"])

    def counts(self) -> dict[str, int]:
        members = self.members
        return {"train": sum(m["split"] == "train" for m in members),
                "holdout": sum(m["split"] == "holdout" for m in members),
                "excluded": sum(m["split"] == "excluded" for m in members)}

    def refusals(self) -> list[str]:
        out = []
        if self.exists:
            out.append(f"REFUSED: snapshot {self.version} exists; snapshots are immutable: pick a new version number")
        if not self.members:
            out.append("REFUSED: nothing to freeze: no kept image with a split, and no old version to start from")
        if self.uncaptioned:
            out.append(f"REFUSED: {len(self.uncaptioned)} train image(s) without an approved caption: "
                       + " ".join(self.uncaptioned[:15]) + (" ..." if len(self.uncaptioned) > 15 else ""))
            out.append("  approve their captions first (runbook 05-captions)")
        if self.leaks:
            out.append(f"REFUSED: {len(self.leaks)} lineage leak(s): one lineage on both sides of the split")
            out += ["  " + leak.line() for leak in self.leaks]
            out.append("  fix the split (runbook 06-split), then freeze again")
        return out


def default_mode(project: Project, version: str, from_version: str | None, only_new: bool) -> tuple[str, str | None, str]:
    """(mode, from version, the reason) for a freeze of `version` with the given flags."""
    if from_version and only_new:
        raise ProjectError(2, "--from and --only-new exclude each other")
    if from_version:
        return "from", check_version(from_version), f"--from {from_version}"
    if only_new:
        return "only-new", None, "--only-new"
    goal = project.goal() or {}
    if goal.get("new_version") == version and goal.get("from_version"):
        return "from", goal["from_version"], f"the goal: new version {version} from {goal['from_version']}"
    latest = latest_version(project)
    if project.adopted and latest:
        return "from", latest, f"adopted project: --from the latest snapshot {latest}"
    return "only-new", None, "native project: only images in no snapshot yet (--from <old> keeps the old members)"


def plan(project: Project, version: str, *, from_version: str | None = None, only_new: bool = False) -> Plan:
    """Work out the members of `version` without writing anything."""
    check_version(version)
    mode, old_version, why = default_mode(project, version, from_version, only_new)
    result = Plan(version=version, mode=mode, from_version=old_version, why_mode=why,
                  exists=snapshot_path(project, version).exists())
    images = images_mod.records(project)
    latest_caption = captions_mod.latest(project)
    in_any = set()
    for v in versions(project):
        if v != version:
            in_any |= {m["image"] for m in load(project, v)["members"]}
    if old_version:
        old = load(project, old_version)
        for member in old["members"]:
            record = images.get(member["image"], {})
            caption = captions_mod.approved_text(latest_caption, member["image"]) or member.get("caption")
            entry = {"image": member["image"], "split": member["split"], "repeat": member.get("repeat", 1),
                     "sha256": member["sha256"], "caption": caption}
            if record and not images_mod.is_kept(record):
                result.no_longer_kept.append(member["image"])
            result.old.append(entry)
    for image_id, record in sorted(captions_mod.training_images(images).items()):
        if image_id in in_any:
            continue
        split = record.get("split")
        if split not in ("train", "holdout"):
            split = "train"
            result.joined_train.append(image_id)
        result.new.append({"image": image_id, "split": split, "repeat": 1, "sha256": record["sha256"],
                           "caption": captions_mod.approved_text(latest_caption, image_id)})
    for member in result.members:
        if not member.get("caption"):
            (result.uncaptioned if member["split"] == "train" else result.holdout_uncaptioned).append(member["image"])
    assignment = {m["image"]: m["split"] for m in result.members}
    result.leaks = lineage.leaks(assignment, images, f"{project.slug}/{version}")
    return result


def freeze(project: Project, version: str, *, from_version: str | None = None, only_new: bool = False,
           notes: str | None = None) -> tuple[Plan, dict | None]:
    """Freeze `version` (or refuse); returns the plan and the written record (None when refused)."""
    with project.lock():  # the members are worked out from the same state the record is written on
        return _freeze(project, version, from_version=from_version, only_new=only_new, notes=notes)


def left_out(project: Project, result: Plan) -> list[str]:
    """The earlier versions whose members a default `--only-new` freeze leaves out (empty when --from applies)."""
    if result.mode != "only-new":
        return []
    return [v for v in versions(project) if v != result.version]


def _freeze(project: Project, version: str, *, from_version: str | None, only_new: bool,
            notes: str | None) -> tuple[Plan, dict | None]:
    result = plan(project, version, from_version=from_version, only_new=only_new)
    if result.refusals():
        return result, None
    members = result.members
    record = {
        "id": f"{project.slug}/{version}", "project": project.slug, "version": version,
        "from_version": result.from_version, "legacy_id": None, "frozen_at": now(), "caption_level": "medium",
        "trigger": project.trigger, "members_sha256": members_sha256(members), "counts": result.counts(),
        "members": members, "manifest_sha256": None, "notes": notes,
    }
    path = snapshot_path(project, version)
    with project.lock():
        if path.exists():  # a second freeze raced this one: snapshots are written once
            raise ProjectError(2, f"REFUSED: snapshot {version} exists; snapshots are immutable")
        store.write_record(path, SCHEMA["snapshot"], record)
    return result, {"schema": SCHEMA["snapshot"], **record}
