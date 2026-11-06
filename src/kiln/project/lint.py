"""`kiln project lint`: is the project folder whole and valid?

Checks, each problem one line `<file>[:<line>]: <what is wrong>`:
- `project.yaml`, `state.json` and every `snapshots/<version>.json` validate against `schemas/`;
- every line of `images.jsonl` and `captions.jsonl` parses and validates (a torn last line is named);
- every image's file exists in its root (existence only: hashes are checked by `dataset materialize`);
- the spec and intake files that project.yaml names exist, and every required intake question has an answer.
An unanswered optional question is a `warn:` line (its default applies); a type file the project has no copy of is
a `note:` line (the shipped type's file is read instead). On an adopted project a spec or intake file that does not
exist yet is a `warn:` line too: an adopted project brings its images and versions, and its spec and intake are
written later. Exit 0 without problems, 1 with any.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

from kiln._common import store

from . import images as images_mod
from . import snapshot as snapshot_mod
from .model import SCHEMAS_DIR, Project, ProjectError, why
from .new import COPIED


@dataclass
class Report:
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@cache
def _validator(name: str):
    path = SCHEMAS_DIR / f"{name}.schema.json"
    if not path.is_file():
        return None
    from jsonschema import Draft202012Validator

    return Draft202012Validator(json.loads(path.read_text(encoding="utf-8")))


def _schema_errors(name: str, data: object) -> list[str]:
    validator = _validator(name)
    if validator is None:
        return []
    out = []
    for error in sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])[:5]:
        where = ".".join(str(p) for p in error.absolute_path) or "top level"
        out.append(f"{where}: {error.message[:160]}")
    return out


def _record(report: Report, project: Project, path: Path, name: str) -> dict | None:
    shown = project.shown(path)
    if not path.is_file():
        report.problems.append(f"{shown}: missing")
        return None
    try:
        data = store.read_record(path)
    except (OSError, ValueError) as exc:
        report.problems.append(f"{shown}: does not parse: {why(exc, path)[:160]}")
        return None
    report.problems += [f"{shown}: {e}" for e in _schema_errors(name, data)]
    return data


def _log(report: Report, project: Project, path: Path, name: str) -> None:
    shown = project.shown(path)
    read = store.read_jsonl(path)
    for number in read.bad_lines:
        torn = read.torn_tail and number == read.bad_lines[-1]
        report.problems.append(f"{shown}:{number}: " + ("a torn last line (a writer stopped mid-line)" if torn
                                                         else "not a JSON object"))
    raw_numbers = [n for n, line in enumerate(path.read_bytes().split(b"\n"), 1) if line.strip()] if path.exists() else []
    good_numbers = [n for n in raw_numbers if n not in set(read.bad_lines)]
    for number, record in zip(good_numbers, read.records, strict=False):
        report.problems += [f"{shown}:{number}: {e}" for e in _schema_errors(name, record)]


def _missing(report: Report, project: Project, path: Path) -> None:
    """A spec or intake file project.yaml names but that does not exist: a problem, a warning when adopted."""
    if project.adopted:
        report.warnings.append(f"{project.shown(path)}: not written yet (adopted project; project.yaml names it)")
    else:
        report.problems.append(f"{project.shown(path)}: missing (project.yaml names it)")


def _intake(report: Report, project: Project) -> None:
    import yaml

    path = project.folder / str(project.data.get("intake") or "intake.yaml")
    if not path.is_file():
        _missing(report, project, path)
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        report.problems.append(f"{project.shown(path)}: does not parse: {why(exc, path)[:160]}")
        return
    for q in data.get("questions") or []:
        if q.get("answer") is not None:
            continue
        label = f"intake question {q.get('n')} {q.get('id')}"
        if q.get("required"):
            report.problems.append(f"{project.shown(path)}: {label} has no answer (required): ask the operator")
        else:
            report.warnings.append(f"{label} has no answer; the default applies: {q.get('default')!r}")


def unanswered_required(project: Project) -> list[str]:
    """The ids of required intake questions without an answer (`kiln next` stays on 00 while any is open)."""
    import yaml

    path = project.folder / str(project.data.get("intake") or "intake.yaml")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ["intake"]
    return [str(q.get("id")) for q in data.get("questions") or [] if q.get("required") and q.get("answer") is None]


def lint(project: Project) -> Report:
    report = Report()
    if _validator("project") is None:
        report.notes.append("schemas/ not found next to the package: records are checked for shape only")
    _record(report, project, project.folder / "project.yaml", "project")
    if project.state_file.exists():
        _record(report, project, project.state_file, "state")
    for version in snapshot_mod.versions(project):
        _record(report, project, snapshot_mod.snapshot_path(project, version), "snapshot")
    if project.images_log.exists():
        _log(report, project, project.images_log, "image-event")
    if project.captions_log.exists():
        _log(report, project, project.captions_log, "caption-event")
    spec = project.folder / str(project.data.get("spec") or "spec.json")
    if not spec.is_file():
        _missing(report, project, spec)
    _intake(report, project)
    for name in COPIED:
        if name not in ("intake.yaml",) and not (project.folder / name).is_file():
            report.notes.append(f"no project copy of {name}: the shipped type's file is read")
    for image_id, record in sorted(images_mod.records(project).items()):
        try:
            path = project.image_path(record)
        except ProjectError as exc:
            report.problems.append(f"{image_id}: {exc.lines[0]}")
            continue
        if not path.is_file():
            report.problems.append(f"{image_id}: file missing ({record.get('root')}: {record.get('file')})")
    return report
