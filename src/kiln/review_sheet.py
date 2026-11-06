"""Triage and vetting by sheet: `kiln review sheet` writes the images that wait for a verdict, `kiln review apply
--sheet` reads the operator's answers back. The review app replaces this later (session 2); the sheet stays as the
fallback.

The sheet (`sheets/review-YYYYMMDD-NNN.csv`, one row per image) shows what is known without looking: the image
id, where its file is, its source, tier, current verdict, split, viewpoint, framing and labels, then one
`check:<key>` column per item of the project's checklist (the approved spec's `checklist`, else the project's
`checklist.yaml`), then `verdict` and `note`. The operator fills it; the assistant only runs the two commands.

Which images get a row (without `--all`): every image without a verdict (legacy rows too, unless a goal is set),
and every kept image in no frozen version yet that still lacks a checklist verdict for an item that applies to
it (the vetting of stage 04). An item applies unless its `skip_when` names the image's viewpoint or framing.

- `verdict`: ACCEPT_TRAIN (also `train`, `keep`, `accept`), ACCEPT_CORE (`core`), ACCEPT_REFERENCE_ONLY
  (`reference`, `ref`), REFINE, REJECT (`no`), HOLD; blank = no verdict yet. Recorded with `verdict_source:
  operator`; REJECT also makes the tier REJECTED and the split unassigned, ACCEPT_REFERENCE_ONLY the split
  reference_only (the same rules as `kiln images label --disposition`).
- `check:<key>`: pass, fail or na. pass records the label `check:<key>`, fail `check-fail:<key>`, na
  `check-na:<key>` (the labels an easel import carries too). The sheet starts with the current state, so an
  unchanged cell changes nothing; a blank cell records nothing.
- `viewpoint`, `framing`: a corrected value is recorded; a blank cell never clears one.
- `note`: the operator's words, verbatim, as the image's notes.

`apply` is safe to run twice: a row whose values are already recorded changes nothing. A row whose image got a
different verdict after the sheet was written is skipped (write a fresh sheet), and so is a row with a word
`apply` does not know: it never guesses what the operator meant. The whole apply runs under the project's store
lock, so the check "has this verdict changed since the sheet was written?" and the line it writes see the same
state even when another session records verdicts at the same time.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import ids, store
from kiln.project import captions as captions_mod
from kiln.project import images as images_mod
from kiln.project import snapshot as snapshot_mod
from kiln.project.model import DISPOSITIONS, TOOL, Project, ProjectError, why

FIRST = ("image", "root", "file", "source", "tier", "disposition", "split", "viewpoint", "framing", "labels")
LAST = ("verdict", "note")
WORDS = {
    "accept_train": "ACCEPT_TRAIN", "train": "ACCEPT_TRAIN", "keep": "ACCEPT_TRAIN", "accept": "ACCEPT_TRAIN",
    "accept_core": "ACCEPT_CORE", "core": "ACCEPT_CORE",
    "accept_reference_only": "ACCEPT_REFERENCE_ONLY", "reference_only": "ACCEPT_REFERENCE_ONLY",
    "reference": "ACCEPT_REFERENCE_ONLY", "ref": "ACCEPT_REFERENCE_ONLY",
    "refine": "REFINE", "reject": "REJECT", "no": "REJECT", "hold": "HOLD",
}
CHECK_WORDS = {"pass": "pass", "p": "pass", "ok": "pass", "fail": "fail", "f": "fail", "na": "na", "n/a": "na"}
CHECK_LABELS = {"pass": "check:{}", "fail": "check-fail:{}", "na": "check-na:{}"}
BY = "kiln review apply"


def verdict_of(word: str) -> str | None:
    """The disposition a sheet word means; None for blank; ValueError for a word nobody defined."""
    key = (word or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not key:
        return None
    if key in WORDS:
        return WORDS[key]
    raise ValueError(word)


# --- the checklist and vetting ------------------------------------------------------------------------------------

def checklist_items(project: Project) -> list[dict]:
    """The checklist items ({key, skip_when}), from the approved spec, else the project's checklist.yaml."""
    import yaml

    spec = project.folder / str(project.data.get("spec") or "spec.json")
    items: list[dict] = []
    if spec.is_file():
        try:
            items = [i for i in store.read_record(spec).get("checklist") or [] if isinstance(i, dict)]
        except (OSError, ValueError, TypeError):
            items = []
    if not any(i.get("key") for i in items) and (project.folder / "checklist.yaml").is_file():
        try:
            data = yaml.safe_load((project.folder / "checklist.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            data = {}
        items = [i for i in data.get("items") or [] if isinstance(i, dict)]
    out = []
    for item in items:
        key = str(item.get("key") or "")
        if key and images_mod.LABEL.match(f"check:{key}"):
            skip = item.get("skip_when") if isinstance(item.get("skip_when"), dict) else {}
            out.append({"key": key, "skip_when": skip})
    return out


def checklist_keys(project: Project) -> list[str]:
    """The checklist keys, from the approved spec, else the project's checklist.yaml."""
    return [item["key"] for item in checklist_items(project)]


def applies(item: dict, record: dict) -> bool:
    """False when the item's `skip_when` names the image's viewpoint or framing (it cannot be seen there)."""
    skip = item.get("skip_when") or {}
    for field_name in ("viewpoint", "framing"):
        value = record.get(field_name)
        if value and value in (skip.get(field_name) or []):
            return False
    return True


def missing_checks(record: dict, items: list[dict]) -> list[str]:
    """The keys of the items that apply to the image and carry no verdict yet (pass, fail or na)."""
    labels = set(record.get("labels") or [])
    return [item["key"] for item in items if applies(item, record)
            and not labels & {form.format(item["key"]) for form in CHECK_LABELS.values()}]


def unvetted(project: Project, images: dict[str, dict], items: list[dict] | None = None) -> list[str]:
    """Kept images in no frozen version yet that lack a verdict for a checklist item that applies (stage 04)."""
    items = checklist_items(project) if items is None else items
    if not items:
        return []
    frozen: set[str] = set()
    for version in snapshot_mod.versions(project):
        frozen |= {m["image"] for m in snapshot_mod.load(project, version)["members"]}
    return sorted(i for i, r in captions_mod.training_images(images).items()
                  if i not in frozen and missing_checks(r, items))


# --- the sheet ---------------------------------------------------------------------------------------------------

def _source_text(record: dict) -> str:
    src = record.get("source") or {}
    where = src.get("run") and f"{src['run']}#{src.get('n')}" or src.get("note") or ""
    return f"{src.get('kind', 'unknown')} {where}".strip()


def _check_state(record: dict, key: str) -> str:
    labels = set(record.get("labels") or [])
    for state, form in CHECK_LABELS.items():
        if form.format(key) in labels:
            return state
    return ""


def wanted(project: Project, images: dict[str, dict], every: bool, items: list[dict] | None = None) -> list[dict]:
    """The rows: every image (`every`), else the images without a verdict (legacy rows too, unless a goal is set)
    and the kept images that still wait for their checklist verdicts."""
    if every:
        chosen = list(images.values())
    else:
        goal = project.goal()
        waiting = set(unvetted(project, images, items))
        chosen = [r for r in images.values()
                  if (images_mod.is_untriaged(r) and not (goal and images_mod.is_legacy_untriaged(r)))
                  or r["id"] in waiting]
    return sorted(chosen, key=lambda r: (r["root"], r["file"]))


def write_sheet(project: Project, *, every: bool = False, out: Path | None = None) -> tuple[Path, int]:
    """Write a review sheet for the operator; returns (path, rows)."""
    images = images_mod.records(project)
    items = checklist_items(project)
    keys = [item["key"] for item in items]
    columns = [*FIRST, *[f"check:{k}" for k in keys], *LAST]
    rows = []
    for record in wanted(project, images, every, items):
        row = {"image": record["id"], "root": record["root"], "file": record["file"], "source": _source_text(record),
               "tier": record.get("tier") or "", "disposition": record.get("disposition") or "",
               "split": record.get("split") or "", "viewpoint": record.get("viewpoint") or "",
               "framing": record.get("framing") or "", "labels": " ".join(record.get("labels") or []),
               "verdict": "", "note": ""}
        row.update({f"check:{k}": _check_state(record, k) for k in keys})
        rows.append(row)
    if out is None:
        project.sheets_dir.mkdir(parents=True, exist_ok=True)
        stem = ids.next_seq_id("review", (p.stem for p in project.sheets_dir.glob("review-*.csv")), TOOL)
        out = project.sheets_dir / f"{stem}.csv"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    with project.lock():
        store.write_text(out, buffer.getvalue())
    return out, len(rows)


def read_sheet(path: Path) -> tuple[list[str], list[dict]]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ProjectError(2, f"cannot read the sheet {path.name}: {why(exc, path)}") from exc
    reader = csv.DictReader(io.StringIO(text))
    columns = list(reader.fieldnames or [])
    missing = [c for c in ("image", "disposition", "verdict") if c not in columns]
    if missing:
        raise ProjectError(2, f"{path.name} is not a review sheet: missing column(s) {', '.join(missing)}")
    rows = [{str(k).strip(): (v or "").strip() for k, v in row.items() if k} for row in reader]
    return columns, rows


@dataclass
class ApplyResult:
    verdicts: Counter = field(default_factory=Counter)
    other: int = 0
    blank: int = 0
    unchanged: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def recorded(self) -> int:
        return sum(self.verdicts.values())


def _row_changes(row: dict, current: dict, keys: list[str]) -> dict:
    """The label() arguments one row asks for (only what differs from the record); ValueError names a bad cell."""
    out: dict = {"add": [], "remove": [], "viewpoint": None, "framing": None, "disposition": None, "note": None}
    try:
        verdict = verdict_of(row.get("verdict", ""))
    except ValueError:
        raise ValueError(f"verdict {row.get('verdict')!r} is not one of {', '.join(DISPOSITIONS)} "
                         "(or train, keep, core, reference, refine, reject, hold)") from None
    if verdict is not None and verdict != current.get("disposition"):
        out["disposition"] = verdict
    for name, allowed in (("viewpoint", images_mod.VIEWPOINTS), ("framing", images_mod.FRAMINGS)):
        value = row.get(name, "")
        if value and value != (current.get(name) or ""):
            if value not in allowed:
                raise ValueError(f"{name} {value!r} is not one of {', '.join(allowed)}")
            out[name] = value
    labels = set(current.get("labels") or [])
    for key in keys:
        cell = row.get(f"check:{key}", "").strip().lower()
        if not cell:
            continue
        state = CHECK_WORDS.get(cell)
        if state is None:
            raise ValueError(f"check:{key} {cell!r} is not pass, fail or na")
        want = {CHECK_LABELS[state].format(key)}
        have = labels & {form.format(key) for form in CHECK_LABELS.values()}
        out["add"] += sorted(want - have)
        out["remove"] += sorted(have - want)
    note = row.get("note", "")
    if note and note != (current.get("notes") or ""):
        out["note"] = note
    return out


def apply_sheet(project: Project, sheet: Path) -> ApplyResult:
    """Record the operator's answers of one sheet (see the module docstring)."""
    columns, rows = read_sheet(sheet)
    keys = [c.removeprefix("check:") for c in columns if c.startswith("check:")]
    result = ApplyResult()
    with project.lock():
        for row in rows:
            _apply_row(project, row, keys, result)
    return result


def _apply_row(project: Project, row: dict, keys: list[str], result: ApplyResult) -> None:
    """One sheet row (the caller holds the project lock)."""
    image_id = row.get("image", "")
    images = images_mod.records(project)
    if image_id not in images:
        result.skipped.append(f"{image_id or '(no id)'}: not an image of {project.slug}")
        return
    current = images[image_id]
    filled = bool(row.get("verdict") or row.get("note"))  # check cells start filled with the current state
    try:
        changes = _row_changes(row, current, keys)
    except ValueError as exc:
        result.skipped.append(f"{image_id}: {exc}")
        return
    sheet_verdict = row.get("disposition", "") or None
    if changes["disposition"] is not None and (current.get("disposition") or None) != sheet_verdict:
        result.skipped.append(f"{image_id}: its verdict changed since the sheet was written (now "
                              f"{current.get('disposition')}); write a fresh sheet")
        return
    if not any(changes[k] for k in changes):
        if filled:
            result.unchanged += 1
        else:
            result.blank += 1
        return
    images_mod.label(project, image_id, by=BY, add=changes["add"], remove=changes["remove"],
                     viewpoint=changes["viewpoint"], framing=changes["framing"],
                     disposition=changes["disposition"], note=changes["note"])
    if changes["disposition"]:
        result.verdicts[changes["disposition"]] += 1
    else:
        result.other += 1
