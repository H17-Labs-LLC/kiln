"""`kiln status`: a project's one-line health, counted from the project's files.

Per project: title, status and phase (state.json); `core_refs` (images with tier CORE), `total_refs` (images not
REJECTED), `open_gaps` (coverage.yaml gaps open or filling with current < target), `open_decisions` (decisions
whose front matter says open), `runs` (runs/<id>/run.json), `snapshots` (snapshots/<version>.json); then the goal.

The gap and decision counts come from `kiln.coverage` and `kiln.decisions`, which own those formats and their
verbs, so `kiln status` and `kiln coverage needs` / `kiln decide list --open` can never disagree. A board or
decision file that does not read counts 0 here; `kiln coverage board` and `kiln decide list` name the problem.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from kiln._common import store

from . import images as images_mod
from . import snapshot as snapshot_mod
from .model import Project


@dataclass
class Status:
    project: str
    title: str
    status: str
    phase: str | None
    core_refs: int
    total_refs: int
    open_gaps: int
    open_decisions: int
    runs: int
    snapshots: int
    goal: dict | None

    def as_dict(self) -> dict:
        return asdict(self)


def open_gaps(project: Project) -> int:
    """How many gaps `kiln coverage needs` lists (0 without a readable board)."""
    from kiln import coverage
    from kiln.handoff import Refusal

    if not (project.folder / coverage.FILE).is_file():
        return 0
    try:
        return len(coverage.open_gaps(coverage.load_board(project.folder)))
    except Refusal:
        return 0


def open_decisions(project: Project) -> int:
    """How many decisions `kiln decide list --open` lists (unreadable files are skipped)."""
    from kiln import decisions

    found, _problems = decisions.load_all(project.folder)
    return len(decisions.open_rows(found))


def runs(project: Project) -> list[dict]:
    """Every runs/<id>/run.json that reads, sorted by id."""
    out = []
    if project.runs_dir.is_dir():
        for path in sorted(project.runs_dir.glob("*/run.json")):
            try:
                out.append(store.read_record(path, "kiln/run/"))
            except (OSError, ValueError):
                continue
    return out


def status(project: Project) -> Status:
    state = project.state()
    images = images_mod.records(project).values()
    return Status(
        project=project.slug,
        title=str(project.data.get("title", project.slug)),
        status=str(state.get("status", "planning")),
        phase=state.get("phase"),
        core_refs=sum(1 for r in images if r.get("tier") == "CORE"),
        total_refs=sum(1 for r in images if r.get("tier") != "REJECTED"),
        open_gaps=open_gaps(project),
        open_decisions=open_decisions(project),
        runs=len(runs(project)),
        snapshots=len(snapshot_mod.versions(project)),
        goal=state.get("goal"),
    )


def goal_text(goal: dict | None) -> str:
    if not goal:
        return "none"
    text = f"new version {goal.get('new_version')}"
    if goal.get("from_version"):
        text += f" from {goal['from_version']}"
    if goal.get("from_easel_run"):
        text += f" (easel run {goal['from_easel_run']})"
    return text
