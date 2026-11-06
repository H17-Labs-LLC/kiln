"""One training caption per image: status, the sheet round trip (sheet -> approve -> apply), lint.

`captions.jsonl` is an event log; the latest line per image wins and only an `approved` latest line is frozen or
trained on. A later draft therefore replaces an approval until the person approves again.

The person approves captions in a sheet, never the assistant:
1. `kiln captions sheet` writes `sheets/captions-YYYYMMDD-NNN.csv`: one row per kept image still waiting
   (`--all`: every kept image), the current text in `draft` and again in `caption` for the person to edit.
2. The person edits `caption` and writes `approve` or `reject` in `decision` (blank rows change nothing).
3. `kiln captions approve --sheet <file>` checks the sheet against the current captions (a row whose draft
   changed after the sheet was written is skipped) and keeps the checked copy in `sheets/approved/`.
4. `kiln captions apply` records every approved-sheet row as a caption line (`source: operator`, `sheet:`
   the sheet's path), once: a row already recorded is not written again. Until then `status` shows the rows
   as "approved in a sheet, not applied".

`lint` is mechanical: it cannot tell whether a caption is true, only whether it follows the project's caption
rules (`caption_rules.yaml` in the project, else the type's in `types/<type>/`): trigger first, the trained
level's word range, the style tag, no quality, source or age words, the vocabulary's preferred words.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import ids, store

from . import images as images_mod
from .model import SCHEMA, TOOL, TYPES_DIR, Project, ProjectError, event_line, why

SHEET_COLUMNS = ("image", "root", "file", "status", "basis", "draft", "caption", "decision", "note")
APPROVE_WORDS = {"approve", "approved", "yes", "y", "ok"}
REJECT_WORDS = {"reject", "rejected", "no", "n"}
APPROVED_DIR = "approved"
STATES = ("approved", "approved in a sheet, not applied", "draft", "rejected", "none")


# --- reading ----------------------------------------------------------------------------------------------------

def lines(project: Project) -> list[dict]:
    return store.read_jsonl(project.captions_log).records


def latest(project: Project) -> dict[str, dict]:
    """image id -> its latest caption line."""
    return store.latest_by_key(project.captions_log, "image")


def approved_text(latest_lines: Mapping[str, dict], image_id: str) -> str | None:
    line = latest_lines.get(image_id)
    return line["text"] if line and line.get("status") == "approved" else None


def training_images(images: Mapping[str, dict]) -> dict[str, dict]:
    """The images a dataset version would use: kept, and not a legacy row nobody triaged."""
    return {i: r for i, r in images.items() if images_mod.is_kept(r) and not images_mod.is_legacy_untriaged(r)}


# --- caption rules and lint -------------------------------------------------------------------------------------

@dataclass
class Rules:
    """What `lint` checks, gathered from the caption rules, the spec and project.yaml."""

    trigger: str | None = None
    min_words: int = 1
    max_words: int = 10_000
    style_tag: str | None = None
    forbidden: dict[str, list[str]] = field(default_factory=dict)
    vocabulary: dict[str, str] = field(default_factory=dict)
    not_in_captions: list[str] = field(default_factory=list)
    source: str = "none"


def _real(value: object) -> str | None:
    """A configured string, or None for an empty value or a template placeholder such as `<outfit token>`."""
    if not isinstance(value, str) or not value.strip() or "<" in value:
        return None
    return value.strip()


def rules_file(project: Project) -> Path | None:
    own = project.folder / "caption_rules.yaml"
    if own.is_file():
        return own
    shipped = TYPES_DIR / str(project.data.get("type", "")) / "caption_rules.yaml"
    return shipped if shipped.is_file() else None


def load_rules(project: Project) -> Rules:
    import yaml

    rules = Rules(trigger=project.trigger)
    path = rules_file(project)
    data: dict = {}
    if path is not None:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rules.source = project.shown(path) if path.is_relative_to(project.instance.root) else f"types/{project.data.get('type')}/caption_rules.yaml"
    level = (data.get("levels") or {}).get(data.get("train_level") or "medium") or {}
    words = level.get("words") or []
    if len(words) == 2:
        rules.min_words, rules.max_words = int(words[0]), int(words[1])
    style = data.get("style_tag") or {}
    if style.get("applies", True):
        rules.style_tag = _real(style.get("default"))
    rules.forbidden = {k: [str(w) for w in v or []] for k, v in (data.get("forbidden") or {}).items()}
    rules.vocabulary = {str(k): str(v) for k, v in (data.get("vocabulary") or {}).items()}
    rules.not_in_captions = [str(w) for w in data.get("not_in_captions") or []]
    spec = project.folder / str(project.data.get("spec") or "spec.json")
    if spec.is_file():  # the approved spec's conventions win over the type's defaults
        try:
            caption = (store.read_record(spec).get("caption") or {})
        except (OSError, ValueError):
            caption = {}
        rules.style_tag = _real(caption.get("style_tag")) or rules.style_tag
        if isinstance(caption.get("min_words"), int) and isinstance(caption.get("max_words"), int):
            rules.min_words, rules.max_words = caption["min_words"], caption["max_words"]
        rules.vocabulary.update({str(k): str(v) for k, v in (caption.get("words") or {}).items()})
        rules.not_in_captions += [str(w) for w in caption.get("not_in_captions") or [] if _real(w)]
    return rules


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(word.lower())}(?![\w-])", text) is not None


def lint_text(text: str, rules: Rules) -> list[str]:
    """Problems with one caption ([] = fine)."""
    out = []
    low = text.strip().lower()
    n = len(text.split())
    if rules.trigger and not low.startswith(rules.trigger.lower()):
        out.append(f"must start with the trigger {rules.trigger!r}")
    if not rules.min_words <= n <= rules.max_words:
        out.append(f"{n} words; the rules want {rules.min_words}-{rules.max_words}")
    for kind, words in sorted(rules.forbidden.items()):
        what = kind.replace("_words", "").replace("_", " ")
        out += [f"{what} word {w!r}" for w in words if _has_word(low, w)]
    out += [f"use {good!r}, not {bad!r}" for bad, good in rules.vocabulary.items() if _has_word(low, bad)]
    out += [f"{w!r} stays out of captions" for w in rules.not_in_captions if _has_word(low, w)]
    if rules.style_tag and rules.style_tag.lower() not in low:
        out.append(f"missing the style tag {rules.style_tag!r}")
    return out


def lint(project: Project) -> tuple[list[tuple[str, list[str]]], Rules]:
    """(image id, problems) for every kept image with a problem, and the rules used."""
    rules = load_rules(project)
    every = lines(project)
    last = latest(project)
    approved_before: set[str] = {line["image"] for line in every if line.get("status") == "approved"}
    found = []
    for image_id in sorted(training_images(images_mod.records(project))):
        line = last.get(image_id)
        if line is None:
            found.append((image_id, ["no caption"]))
            continue
        problems = lint_text(str(line.get("text", "")), rules)
        if line.get("status") != "approved" and image_id in approved_before:
            problems.append("an approved caption was replaced by a later draft: one caption per image; "
                            "ask which one the operator keeps")
        elif line.get("status") == "rejected":
            problems.append("the operator rejected this caption; it needs a new draft")
        if problems:
            found.append((image_id, problems))
    return found, rules


# --- status -----------------------------------------------------------------------------------------------------

def pending_approvals(project: Project) -> dict[tuple[str, str], dict]:
    """(sheet path, image id) -> the approved-sheet row not yet recorded in captions.jsonl."""
    recorded = {(line.get("sheet"), line.get("image")) for line in lines(project) if line.get("sheet")}
    out: dict[tuple[str, str], dict] = {}
    for sheet_rel, row in approved_rows(project):
        if (sheet_rel, row["image"]) not in recorded:
            out[(sheet_rel, row["image"])] = row
    return out


def status(project: Project) -> dict[str, list[str]]:
    """Each state of STATES -> the kept images in it."""
    last = latest(project)
    pending = {image for _, image in pending_approvals(project)}
    out: dict[str, list[str]] = {state: [] for state in STATES}
    for image_id in sorted(training_images(images_mod.records(project))):
        line = last.get(image_id)
        if line and line.get("status") == "approved":
            out["approved"].append(image_id)
        elif image_id in pending:
            out["approved in a sheet, not applied"].append(image_id)
        elif line and line.get("status") == "rejected":
            out["rejected"].append(image_id)
        elif line:
            out["draft"].append(image_id)
        else:
            out["none"].append(image_id)
    return out


# --- the sheet round trip ---------------------------------------------------------------------------------------

def write_sheet(project: Project, *, every: bool = False, out: Path | None = None) -> tuple[Path, int]:
    """Write a caption sheet for the person; returns (path, rows)."""
    images = images_mod.records(project)
    last = latest(project)
    rows = []
    for image_id, record in sorted(training_images(images).items()):
        line = last.get(image_id)
        if not every and line and line.get("status") == "approved":
            continue
        text = line.get("text", "") if line else ""
        rows.append({"image": image_id, "root": record["root"], "file": record["file"],
                     "status": line.get("status", "none") if line else "none",
                     "basis": (line or {}).get("basis") or "", "draft": text, "caption": text,
                     "decision": "", "note": ""})
    if out is None:
        project.sheets_dir.mkdir(parents=True, exist_ok=True)
        stem = ids.next_seq_id("captions", (p.stem for p in project.sheets_dir.glob("captions-*.csv")), TOOL)
        out = project.sheets_dir / f"{stem}.csv"
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=SHEET_COLUMNS, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    with project.lock():
        store.write_text(out, buffer.getvalue())
    return out, len(rows)


def _read_sheet(path: Path) -> list[dict]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ProjectError(2, f"cannot read the sheet {path.name}: {why(exc, path)}") from exc
    reader = csv.DictReader(io.StringIO(text))
    missing = [c for c in ("image", "draft", "caption", "decision") if c not in (reader.fieldnames or [])]
    if missing:
        raise ProjectError(2, f"{path.name} is not a caption sheet: missing column(s) {', '.join(missing)}")
    return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def decision_of(row: Mapping[str, str]) -> str | None:
    word = row.get("decision", "").strip().lower()
    if word in APPROVE_WORDS:
        return "approved"
    if word in REJECT_WORDS:
        return "rejected"
    return None


@dataclass
class ApproveResult:
    approved: int = 0
    rejected: int = 0
    blank: int = 0
    skipped: list[str] = field(default_factory=list)
    kept_at: Path | None = None


def approve_sheet(project: Project, sheet: Path) -> ApproveResult:
    """Check the person's sheet and keep the checked copy for `apply` (under the project lock: the check against
    the current captions and the kept copy see the same state)."""
    rows = _read_sheet(sheet)
    with project.lock():
        return _approve_rows(project, sheet, rows)


def _approve_rows(project: Project, sheet: Path, rows: list[dict]) -> ApproveResult:
    images = images_mod.records(project)
    last = latest(project)
    result = ApproveResult()
    kept_rows = []
    for row in rows:
        decision = decision_of(row)
        image_id = row.get("image", "")
        if decision is None:
            if row.get("decision"):
                result.skipped.append(f"{image_id}: decision {row['decision']!r} is neither approve nor reject")
            else:
                result.blank += 1
            continue
        if image_id not in images:
            result.skipped.append(f"{image_id or '(no id)'}: not an image of {project.slug}")
            continue
        current = (last.get(image_id) or {}).get("text", "")
        if row.get("draft", "") != current.strip():
            result.skipped.append(f"{image_id}: its caption changed since the sheet was written; write a fresh sheet")
            continue
        if decision == "approved" and not row.get("caption"):
            result.skipped.append(f"{image_id}: approved with an empty caption")
            continue
        kept_rows.append(row)
        result.approved += decision == "approved"
        result.rejected += decision == "rejected"
    if kept_rows:
        target = project.sheets_dir / APPROVED_DIR / sheet.name
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=SHEET_COLUMNS, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(kept_rows)
        with project.lock():
            store.write_text(target, buffer.getvalue())
        result.kept_at = target
    return result


def approved_rows(project: Project) -> list[tuple[str, dict]]:
    """(sheet path relative to the instance root, row) for every decided row of every approved sheet."""
    folder = project.sheets_dir / APPROVED_DIR
    out = []
    for path in sorted(folder.glob("*.csv")) if folder.is_dir() else []:
        original = project.sheets_dir / path.name
        rel = project.instance.relpath(original) or f"sheets/{path.name}"
        for row in _read_sheet(path):
            if decision_of(row):
                out.append((rel, row))
    return out


@dataclass
class ApplyResult:
    applied: int = 0
    rejected: int = 0
    already: int = 0
    lint_notes: list[str] = field(default_factory=list)


def apply_approved(project: Project, *, by: str = "kiln captions apply") -> ApplyResult:
    """Record every approved-sheet row not recorded yet as a caption line; lint findings are noted, not refused.

    Runs under the project lock, so two `apply` runs never record the same row twice and a draft stored at the
    same moment (`captions propose --store`) lands before or after, never in between."""
    with project.lock():
        return _apply_pending(project, by)


def _apply_pending(project: Project, by: str) -> ApplyResult:
    rules = load_rules(project)
    result = ApplyResult()
    pending = pending_approvals(project)
    every_sheet = approved_rows(project)
    result.already = len(every_sheet) - len(pending)
    new_lines = []
    for (sheet_rel, image_id), row in pending.items():
        status_word = decision_of(row) or "approved"
        text = row.get("caption") or row.get("draft") or ""
        if not text:
            continue
        problems = lint_text(text, rules) if status_word == "approved" else []
        if problems:
            result.lint_notes.append(f"{image_id}: approved but " + "; ".join(problems)
                                     + " (stored anyway: the operator's word)")
        basis = row.get("basis") or "manual"
        if text != row.get("draft", ""):
            basis = "manual"
        if basis not in ("prompt", "manual", "assistant", "legacy"):
            basis = "manual"
        data = {"image": image_id, "text": text, "word_count": len(text.split()), "status": status_word,
                "basis": basis, "sheet": sheet_rel, "lint": problems, "legacy_id": None}
        new_lines.append(event_line(SCHEMA["caption"], data, source="operator", by=by))
        result.applied += status_word == "approved"
        result.rejected += status_word == "rejected"
    if new_lines:
        project.append(project.captions_log, new_lines)
    return result
