"""The `files` source: register the images of a folder you already have (`kiln images import --from-folder <dir>`).

A file inside one of the project's roots (the imports root, or the read-only images_root of an adopted project) is
registered where it lies. Any other file is copied, sha256 checked, into `<imports root>/<folder name>/` under its
path relative to the folder; nothing is ever written under images_root. Every new
image arrives untriaged (`tier: CANDIDATE`, no disposition): the operator's verdict comes later, through
`kiln review sheet` or `kiln images label`.

`--notes notes.csv` carries the operator's words per file. Columns: `file` (the name relative to the folder,
forward slashes) and `note`, and optionally `viewpoint`, `framing` and `labels` (space-separated `ns:value`).
A note is stored as the image's `notes`, verbatim; it is also what `kiln captions propose` drafts from when the
image has no prompt. A viewpoint and framing go into the image's one register line. Files are copied, never moved,
renamed or opened.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import image_meta
from kiln.project import images as images_mod
from kiln.project.model import Project, ProjectError, why

from . import base

KINDS = ("unknown", "upload", "scan", "local-gen", "api", "chatgpt", "codex", "generated")


@dataclass
class Imported:
    """What a folder import did: the new lines, the files already registered, the problems."""

    registered: list[dict] = field(default_factory=list)
    already: list[tuple[str, dict]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes_unused: list[str] = field(default_factory=list)


class FilesSource(base.Source):
    kind = "files"
    makes_images = False

    def run(self, job, ctx):  # noqa: ARG002 - the interface; a folder has no jobs
        raise ProjectError(2, ["a jobs file with source files has nothing to run",
                               "register a folder: kiln images import --from-folder <dir>"])


def read_notes(path: Path) -> dict[str, dict]:
    """file name -> its row of the notes CSV (keys stripped, blank cells empty)."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ProjectError(2, f"cannot read the notes file {path.name}: {why(exc, path)}") from exc
    reader = csv.DictReader(io.StringIO(text))
    if "file" not in (reader.fieldnames or []):
        raise ProjectError(2, f"{path.name} is not a notes file: it needs a `file` column (and `note`)")
    out = {}
    for row in reader:
        clean = {str(k).strip(): (v or "").strip() for k, v in row.items() if k}
        if clean.get("file"):
            out[clean["file"].replace("\\", "/")] = clean
    return out


def _row_fields(row: dict, name: str) -> tuple[dict, list[str]]:
    """viewpoint, framing, labels and note of one notes row, checked; returns (fields, problems)."""
    out: dict = {"note": row.get("note") or None, "viewpoint": None, "framing": None, "labels": []}
    problems = []
    if row.get("viewpoint"):
        if row["viewpoint"] in images_mod.VIEWPOINTS:
            out["viewpoint"] = row["viewpoint"]
        else:
            problems.append(f"{name}: viewpoint {row['viewpoint']!r} is not one of {', '.join(images_mod.VIEWPOINTS)}")
    if row.get("framing"):
        if row["framing"] in images_mod.FRAMINGS:
            out["framing"] = row["framing"]
        else:
            problems.append(f"{name}: framing {row['framing']!r} is not one of {', '.join(images_mod.FRAMINGS)}")
    for label in (row.get("labels") or "").split():
        if images_mod.LABEL.match(label):
            out["labels"].append(label)
        else:
            problems.append(f"{name}: label {label!r} is not namespace:value")
    return out, problems


def _in_root(project: Project, path: Path) -> bool:
    try:
        images_mod.locate(project, path)
    except ProjectError:
        return False
    return True


def import_folder(project: Project, folder: Path, *, notes: Path | None = None, kind: str = "unknown",
                  by: str = "kiln images import --from-folder") -> Imported:
    """Register every image file under `folder` (sorted); copy it into the imports root first unless it lies in a root."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ProjectError(2, f"no such folder: {folder.name}")
    if kind not in KINDS:
        raise ProjectError(2, f"--kind is one of {', '.join(KINDS)}: {kind!r}")
    rows = read_notes(notes) if notes is not None else {}
    files = list(image_meta.iter_images(folder))
    if not files:
        raise ProjectError(1, f"no image files (png, jpeg, webp) in {folder.name}")
    result = Imported()
    with project.lock():  # the known images are read once; no other session registers in between
        _import_files(project, folder, files, rows, kind, by, result)
    return result


def _import_files(project: Project, folder: Path, files: list[Path], rows: dict[str, dict], kind: str, by: str,
                  result: Imported) -> None:
    known = images_mod.records(project)
    target_dir = base.imports_dir(project) / folder.resolve().name
    used: set[str] = set()
    for path in files:
        name = path.relative_to(folder).as_posix()
        row = rows.get(name) or rows.get(path.name) or {}
        if row:
            used.add(row["file"])
        fields, problems = _row_fields(row, name)
        if problems:
            result.problems += problems
            continue
        data = path.read_bytes()
        record = base.known_image(known, base.sha_of(data))
        if record is not None:
            result.already.append((name, record))
            continue
        if not image_meta.probe(path, sha256=False).get("readable"):
            result.problems.append(f"{name}: not a readable PNG, JPEG or WebP header; skipped")
            continue
        dest = path
        if not _in_root(project, path):  # a file already in a project root is registered where it lies
            dest = target_dir / name
            try:
                base.place(project, data, dest)
            except ProjectError as exc:
                result.problems += exc.lines
                continue
        source = {"kind": kind, "tool": "kiln images import", "note": folder.resolve().name}
        line, created = images_mod.register(project, dest, source=source, by=by, event="import",
                                            labels=fields["labels"], notes=fields["note"],
                                            viewpoint=fields["viewpoint"], framing=fields["framing"], images=known)
        if not created:
            result.already.append((name, line))
            continue
        known[line["id"]] = line
        result.registered.append(line)
    result.notes_unused = sorted(set(rows) - used)
