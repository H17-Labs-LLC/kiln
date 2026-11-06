"""The runs of a project: `runs/<run-id>/run.json`, read, filtered and found by id.

`kiln runs list --family <f> --status ok` answers "which base do I train on": a finished run's base (with its
licence) is what `kiln train plan --base-from-run <run-id>` copies. A record that does not read is skipped and
named, never guessed at.

Paths inside a run record are relative to the project folder, except on a run exported from (or emitted for) a
host workspace: there `legacy.paths_relative_to` says they are relative to the host's root.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import store

from ..project.model import Instance, Project, ProjectError, why

STATUSES = ("planned", "running", "ok", "failed", "oom", "aborted")
LEGACY_RELATIVE = "the legacy workspace root"


@dataclass
class Listing:
    """The runs that read, sorted by id, and the run.json files that did not."""

    runs: list[dict] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)


def run_file(project: Project, run_id: str) -> Path:
    return project.runs_dir / run_id / "run.json"


def read_all(project: Project) -> Listing:
    """Every `runs/*/run.json` of the project."""
    out = Listing()
    if not project.runs_dir.is_dir():
        return out
    for path in sorted(project.runs_dir.glob("*/run.json")):
        try:
            out.runs.append(store.read_record(path, "kiln/run/"))
        except (OSError, ValueError):
            out.unreadable.append(project.shown(path))
    return out


def select(runs: list[dict], *, family: str | None = None, status: str | None = None) -> list[dict]:
    """The runs of `family` (exact id) with `status`; None means any."""
    if status is not None and status not in STATUSES:
        raise ProjectError(2, f"--status is one of {', '.join(STATUSES)}, not {status!r}")
    return [r for r in runs if (family is None or r.get("family") == family)
            and (status is None or r.get("status") == status)]


def load(project: Project, run_id: str) -> dict:
    """One run by id; exit 2 naming the known runs when it is missing."""
    path = run_file(project, run_id)
    if not path.is_file():
        known = ", ".join(r["id"] for r in read_all(project).runs) or "none"
        raise ProjectError(2, [f"no run {run_id} in {project.slug} (runs: {known})",
                               "`kiln runs list` lists them"])
    try:
        return store.read_record(path, "kiln/run/")
    except (OSError, ValueError) as exc:
        raise ProjectError(2, f"{project.shown(path)}: not a readable run record: {why(exc, path)}") from exc


def legacy_root_of(project: Project, override: Path | str | None = None) -> Path | None:
    """The host workspace root: `--legacy-root` when given, else `[legacy] root` (relative to the instance root),
    else None (not an adopted instance)."""
    if override is not None:
        return Path(override).expanduser().resolve()
    value = str(project.instance.cfg.get("legacy.root", "") or "").strip()
    return Instance._path(project.instance.root, value) if value else None


def file_of(project: Project, run: dict, rel: str | None, legacy_root: Path | None) -> Path | None:
    """Where a path recorded in `run` lies on this machine (None when it cannot be placed)."""
    if not rel:
        return None
    if (run.get("legacy") or {}).get("paths_relative_to") == LEGACY_RELATIVE:
        return (legacy_root / rel).resolve() if legacy_root is not None else None
    return (project.folder / rel).resolve()


def row(run: dict) -> dict:
    """The fields `runs list` shows for one run."""
    steps = run.get("steps") or {}
    base = run.get("base") or {}
    return {"id": run.get("id"), "status": run.get("status"), "family": run.get("family"),
            "snapshot": run.get("snapshot"), "base": base.get("label"), "base_resource": base.get("resource"),
            "licence": (base.get("licence") or {}).get("status"), "steps_planned": steps.get("planned"),
            "steps_done": steps.get("done"), "selected_checkpoint": run.get("selected_checkpoint"),
            "continue_from": (run.get("continue_from") or {}).get("resource"), "legacy_id": run.get("legacy_id")}


def line(r: dict) -> str:
    """One `runs list` line: id | status | family | snapshot | base | steps done/planned | picked step."""
    done = "-" if r["steps_done"] is None else r["steps_done"]
    picked = "-" if r["selected_checkpoint"] is None else r["selected_checkpoint"]
    return " | ".join([str(r["id"]), str(r["status"]), str(r["family"]), str(r["snapshot"] or "-"),
                       f"base {r['base'] or '-'} (licence {r['licence'] or '-'})",
                       f"steps {done}/{r['steps_planned']}", f"picked {picked}"])
