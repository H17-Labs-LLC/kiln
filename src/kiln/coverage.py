"""The coverage board: what the dataset still needs, per dimension and bucket (`kiln coverage board|needs`).

`projects/<slug>/coverage.yaml` (schema `kiln/coverage/1`) holds one gap per (dimension, bucket): the person's
`target` and `priority`, and `current` and `status`, which `kiln coverage board` refreshes from the images.
Why: without the refresh the board is a hand-kept spreadsheet that drifts the moment an image is promoted.

How an image counts. Only an image a person accepted for training or placed in a split counts: its tier is not
REJECTED, and its disposition is ACCEPT_TRAIN or ACCEPT_CORE or its split is train or holdout. Unreviewed
candidates and untriaged legacy images never fill a bucket. Per dimension:
- `framing` and `viewpoint`: the image's own field of that name, or a label `framing:<bucket>`;
- every other dimension: a label `<dimension>:<bucket>` on the image (for example `outfit:default`).
One image fills one bucket in each dimension it carries, so the numbers are placements, not distinct images.
A dimension that no image carries a label for still counts 0, but the board says so in a `note:` line with
the label names in use: a wrong zero must be visible.

Status after a refresh: `met` when current >= target, `filling` when 0 < current < target, `open` at 0. A
`waived` gap is the person's call: it is left exactly as written.

`needs` lists the open gaps: the gaps with status open or filling and current below target,
blockers first, then high, normal and low, and within a priority the most remaining first (then file order).
It reads the board as stored; when the images say otherwise it adds a note to run `kiln coverage board`.

On the demo, `needs` lists framing/close (high), viewpoint/back (normal) and lighting/night (low).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from kiln._common import store
from kiln.handoff import Refusal, schema_problems
from kiln.project.model import why

TOOL = "kiln"
SCHEMA = "kiln/coverage/1"
SCHEMA_NAME = "coverage"
FILE = "coverage.yaml"
IMAGES = "images.jsonl"
COUNTED_DISPOSITIONS = frozenset({"ACCEPT_TRAIN", "ACCEPT_CORE"})
COUNTED_SPLITS = frozenset({"train", "holdout"})
FIELD_DIMENSIONS = ("framing", "viewpoint")  # image fields that are also coverage dimensions
PRIORITY_RANK = {"blocker": 0, "high": 1, "normal": 2, "low": 3}
OPEN_STATUSES = ("open", "filling")
COLUMNS = ("dimension", "bucket", "target", "current", "remaining", "priority", "status")


@dataclass
class Board:
    """The board as read: its file, the text it was read from, the record, and the image lines that did not parse."""

    path: Path
    text: str
    data: dict
    warnings: list[str] = field(default_factory=list)

    @property
    def gaps(self) -> list[dict]:
        return self.data["gaps"]


@dataclass
class Change:
    dimension: str
    bucket: str
    old_current: int
    new_current: int
    old_status: str
    new_status: str

    def line(self) -> str:
        parts = []
        if self.old_current != self.new_current:
            parts.append(f"current {self.old_current} -> {self.new_current}")
        if self.old_status != self.new_status:
            parts.append(f"status {self.old_status} -> {self.new_status}")
        return f"changed: {self.dimension}/{self.bucket}: " + ", ".join(parts)


# --- reading ----------------------------------------------------------------------------------------------------

def load_board(project_dir: Path) -> Board:
    """Read and check `coverage.yaml`; Refusal (exit 2) when it is missing or not a valid board."""
    path = Path(project_dir) / FILE
    if not path.is_file():
        raise Refusal([f"no {FILE} in this project: the person writes the targets first",
                       "the format, field by field: docs/reference/formats.md, section coverage"])
    try:
        text = path.read_text(encoding="utf-8-sig")
        data = store.read_record(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise Refusal([f"{FILE}: not readable: {why(exc, path).splitlines()[0]}"]) from exc
    problems = _shape_problems(data) or schema_problems(SCHEMA_NAME, data) or _duplicates(data["gaps"])
    if problems:
        raise Refusal([f"{FILE}: {p}" for p in problems[:10]] + ["the targets are the person's: ask them to fix the file"])
    return Board(path, text, data)


def _shape_problems(data: dict) -> list[str]:
    """What the counting needs, checked even where the schema files are not at hand (an installed wheel)."""
    if data.get("schema") != SCHEMA:
        return [f"schema is {data.get('schema')!r}, not {SCHEMA}"]
    if not isinstance(data.get("project"), str) or not isinstance(data.get("gaps"), list):
        return ["a board has a `project` slug and a `gaps` list"]
    problems = []
    for n, gap in enumerate(data["gaps"]):
        if not isinstance(gap, dict):
            problems.append(f"at /gaps/{n}: not a mapping")
            continue
        for key in ("dimension", "bucket", "priority", "status"):
            if not isinstance(gap.get(key), str):
                problems.append(f"at /gaps/{n}: `{key}` is missing or not text")
        for key in ("target", "current"):
            if not isinstance(gap.get(key), int) or isinstance(gap.get(key), bool) or gap[key] < 0:
                problems.append(f"at /gaps/{n}: `{key}` is missing or not a whole number >= 0")
    return problems


def _duplicates(gaps: list[dict]) -> list[str]:
    """One line per (dimension, bucket) that appears more than once (the old table kept them unique)."""
    seen: dict[tuple[str, str], int] = {}
    problems = []
    for n, gap in enumerate(gaps):
        key = (gap["dimension"], gap["bucket"])
        if key in seen:
            problems.append(f"at /gaps/{n}: {key[0]}/{key[1]} is already gap {seen[key]}; one gap per bucket")
        seen.setdefault(key, n)
    return problems


def load_images(project_dir: Path) -> tuple[list[dict], list[str]]:
    """The latest line per image id of `images.jsonl`, and a warning per line that did not parse."""
    read = store.read_jsonl(Path(project_dir) / IMAGES)
    latest: dict[str, dict] = {}
    for record in read.records:
        if isinstance(record.get("id"), str):
            latest[record["id"]] = record
    warnings = [f"warn: {IMAGES} line {n} does not parse; skipped" for n in read.bad_lines]
    if read.torn_tail and warnings:
        warnings[-1] += " (a torn last line: a writer stopped mid-line)"
    return list(latest.values()), warnings


# --- counting ---------------------------------------------------------------------------------------------------

def counts_as_coverage(image: dict) -> bool:
    """True when a person accepted the image for training or placed it in a split (and did not reject it)."""
    if image.get("tier") == "REJECTED":
        return False
    return image.get("disposition") in COUNTED_DISPOSITIONS or image.get("split") in COUNTED_SPLITS


def image_buckets(image: dict) -> set[tuple[str, str]]:
    """Every (dimension, bucket) the image fills: its framing and viewpoint fields and its namespaced labels."""
    out = set()
    for label in image.get("labels") or []:
        if isinstance(label, str) and ":" in label:
            dimension, bucket = label.split(":", 1)
            out.add((dimension, bucket))
    for dimension in FIELD_DIMENSIONS:
        value = image.get(dimension)
        if isinstance(value, str) and value:
            out.add((dimension, value))
    return out


def count(images: list[dict]) -> dict[tuple[str, str], int]:
    """Placements per (dimension, bucket) over the images that count as coverage."""
    totals: dict[tuple[str, str], int] = {}
    for image in images:
        if counts_as_coverage(image):
            for key in image_buckets(image):
                totals[key] = totals.get(key, 0) + 1
    return totals


def status_for(current: int, target: int) -> str:
    if current >= target:
        return "met"
    return "filling" if current > 0 else "open"


def refresh(board: Board, images: list[dict]) -> list[Change]:
    """Set `current` and `status` of every gap that is not waived from `images`; returns what changed."""
    totals = count(images)
    changes = []
    for gap in board.gaps:
        if gap.get("status") == "waived":
            continue
        new_current = totals.get((gap["dimension"], gap["bucket"]), 0)
        new_status = status_for(new_current, int(gap["target"]))
        if new_current != gap["current"] or new_status != gap["status"]:
            changes.append(Change(gap["dimension"], gap["bucket"], gap["current"], new_current, gap["status"],
                                  new_status))
            gap["current"], gap["status"] = new_current, new_status
    return changes


def stale(board: Board, images: list[dict]) -> list[Change]:
    """The gaps whose stored `current` the images no longer support (the board is not changed)."""
    copy = Board(board.path, board.text, {"gaps": [dict(g) for g in board.gaps]})
    return [c for c in refresh(copy, images) if c.old_current != c.new_current]


def unlabelled_dimensions(board: Board, images: list[dict]) -> tuple[list[str], list[str]]:
    """(dimensions of the board that no image carries a label for, the label names in use), both sorted."""
    in_use = {dim for image in images for dim, _ in image_buckets(image)}
    labels = sorted({label.split(":", 1)[0] for image in images for label in image.get("labels") or []
                     if isinstance(label, str) and ":" in label})
    dims = sorted({g["dimension"] for g in board.gaps if g.get("status") != "waived"
                   and g["dimension"] not in FIELD_DIMENSIONS and g["dimension"] not in in_use})
    return dims, labels


# --- rows -------------------------------------------------------------------------------------------------------

def _row(project: str, gap: dict) -> dict:
    return {"project": project, "dimension": gap["dimension"], "bucket": gap["bucket"], "target": gap["target"],
            "current": gap["current"], "remaining": gap["target"] - gap["current"], "priority": gap["priority"],
            "status": gap["status"]}


def board_rows(board: Board) -> list[dict]:
    """Every gap: by priority (blocker first), then dimension and bucket; `remaining` never below 0 here."""
    rows = [_row(board.data["project"], g) for g in board.gaps]
    for row in rows:
        row["remaining"] = max(row["remaining"], 0)
    return sorted(rows, key=lambda r: (PRIORITY_RANK.get(r["priority"], 3), r["dimension"], r["bucket"]))


def open_gaps(board: Board) -> list[dict]:
    """The open gaps of the stored board: open or filling and below target, by priority, then most remaining
    first, ties in file order."""
    rows = [_row(board.data["project"], g) for g in board.gaps
            if g["status"] in OPEN_STATUSES and g["current"] < g["target"]]
    return sorted(rows, key=lambda r: (PRIORITY_RANK.get(r["priority"], 3), -r["remaining"]))


# --- writing ----------------------------------------------------------------------------------------------------

def _header(text: str) -> tuple[str, bool]:
    """(the leading comment block of the file, whether comments appear further down)."""
    lines = text.splitlines(keepends=True)
    head = []
    for line in lines:
        if line.strip() and not line.lstrip().startswith("#"):
            break
        head.append(line)
    later = any(line.lstrip().startswith("#") for line in lines[len(head):])
    return "".join(head), later


def write_board(board: Board) -> bool:
    """Write the refreshed board atomically, keeping its leading comment block; True when comments further down
    were dropped (the caller says so)."""
    problems = schema_problems(SCHEMA_NAME, board.data)
    if problems:  # a bug here, never the person's input: refuse rather than write a bad record
        raise Refusal([f"refused to write {FILE}: " + "; ".join(problems[:3])], code=1)
    head, dropped = _header(board.text)
    record = {"schema": SCHEMA, **{k: v for k, v in board.data.items() if k != "schema"}}
    store.write_text(board.path, head + store.dump_yaml(record))
    return dropped
