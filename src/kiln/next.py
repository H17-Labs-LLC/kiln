"""`kiln next`: the stage a project is in, the runbook to read, and the next command.

Three lines: `stage: <id>`, `read: docs/runbooks/<id>.md`, `next: <command>` (`--stage` prints the id alone,
`--json` all of it plus `why`). Stage ids and their first commands are the stage table of
`docs/runbooks/README.md`. Two rules keep the lines honest for an agent that follows them literally:
- a stage whose runbook is not written yet prints `read: <id> is planned` instead of a path that does not exist;
- a next command that names a verb or subverb not built yet ends with `# planned: <task>` (`kiln.planned`).
A step marked planned is not built: the agent reports it once and stops at that stage.

With a goal set (`kiln project goal set`), the route is fixed for that version and legacy rows nobody triaged
(`legacy_untriaged`) are ignored:
  03-dataset-images   the goal's easel run has no imported image yet, or a non-legacy image has no disposition;
  05-captions         a member of the version would have no caption;
  06-split            a new kept image has no split;
  07-freeze-and-gate  the version is not frozen, or not materialised with counts that match;
  08-train            the version is frozen and materialised.
Without a goal the stage is derived from the files: no instance or project -> 00; an adopted project -> 90 (the
goal comes first); required intake answers missing -> 00; no image -> 01; images without a disposition -> 03;
kept images in no frozen version that lack a checklist verdict for an item that applies to them -> 04; kept
images without an approved caption -> 05; kept images without a split -> 06; kept images not in the latest
version, or the latest version not materialised -> 07; then the runs: none or a planned/running/failed one -> 08,
a finished one -> 09, a picked checkpoint -> 10.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import planned
from . import review_sheet as review_sheet_mod
from .project import captions as captions_mod
from .project import images as images_mod
from .project import materialize as materialize_mod
from .project import snapshot as snapshot_mod
from .project import status as status_mod
from .project.lint import unanswered_required
from .project.model import REPO, Instance, Project, ProjectError

FIRST_COMMAND = {
    "00-new-project": "kiln project new <slug> --type character-anime",
    "01-probe-iterations": "kiln source run <jobs.json> --dry-run",
    "02-anchors": "kiln images list",
    "03-dataset-images": "kiln images list",
    "04-vetting": "kiln review sheet",
    "05-captions": "kiln captions status",
    "06-split": "kiln dataset census",
    "07-freeze-and-gate": "kiln dataset check --leaks",
    "08-train": "kiln runs list --family <family> --status ok",
    "09-evaluate": "kiln eval --help",
    "10-showcase": "kiln showcase --help",
    "11-publish-by-hand": "kiln release --help",
    "12-retrain-from-feedback": "kiln project goal set --new-version <version> --from-easel-run <run-id>",
    "90-adopt-existing": "kiln project show",
}
NO_INSTANCE_COMMAND = "kiln init ~/kiln-home"
# The runbooks written so far. In a checkout the folder docs/runbooks/ itself is the answer (RUNBOOKS_DIR); this
# list answers for an installed package without the docs, and a test keeps it equal to the folder.
WRITTEN_RUNBOOKS = frozenset({"00-new-project", "03-dataset-images", "05-captions", "06-split",
                              "07-freeze-and-gate", "08-train", "90-adopt-existing"})
RUNBOOKS_DIR = REPO / "docs" / "runbooks"


def runbook_written(stage: str) -> bool:
    """True when docs/runbooks/<stage>.md exists (the checkout's folder when present, else WRITTEN_RUNBOOKS)."""
    if (RUNBOOKS_DIR / "README.md").is_file():
        return (RUNBOOKS_DIR / f"{stage}.md").is_file()
    return stage in WRITTEN_RUNBOOKS


@dataclass
class Stage:
    stage: str
    next: str
    why: str
    project: str | None = None
    next_planned: str | None = field(default=None)

    @property
    def runbook(self) -> str | None:
        """The runbook's path, or None while it is planned."""
        return f"docs/runbooks/{self.stage}.md" if runbook_written(self.stage) else None

    @property
    def read(self) -> str:
        return self.runbook or f"{self.stage} is planned"

    def lines(self) -> list[str]:
        return [f"stage: {self.stage}", f"read: {self.read}", f"next: {self.next}"]

    def as_dict(self) -> dict:
        return {"stage": self.stage, "read": self.read, "runbook": self.runbook, "next": self.next,
                "next_planned": self.next_planned, "why": self.why, "project": self.project}


def _stage(stage: str, why: str, project: Project | None = None, command: str | None = None) -> Stage:
    command = command or FIRST_COMMAND[stage]
    task = planned.task_of(command)
    return Stage(stage=stage, next=planned.mark(command), why=why, project=project.slug if project else None,
                 next_planned=task)


def _family(project: Project) -> str:
    families = project.data.get("families") or []
    return str(families[0]) if families else "<family>"


def _version_ready(project: Project, version: str) -> tuple[bool, str]:
    """(frozen and materialised with matching counts, why not)."""
    if not snapshot_mod.snapshot_path(project, version).exists():
        return False, f"version {version} is not frozen"
    folder = materialize_mod.check_folder(project, version)
    if not folder.found:
        return False, f"version {version} is frozen but not materialised"
    if folder.foreign:
        return False, f"version {version} was materialised outside kiln; kiln trains only from its own copy"
    if folder.problems:
        return False, f"version {version} is materialised but does not match: {folder.problems[0]}"
    return True, f"version {version} is frozen and materialised"


def _with_goal(project: Project, goal: dict) -> Stage:
    version = str(goal.get("new_version"))
    images = images_mod.records(project)
    live = {i: r for i, r in images.items() if not images_mod.is_legacy_untriaged(r)}
    run = goal.get("from_easel_run")
    if run and not any((r.get("source") or {}).get("run") == run or r.get("verdict_source") == f"easel-review:{run}"
                       for r in live.values()):
        return _stage("03-dataset-images", f"the goal's easel run {run} has no imported image yet", project,
                      f"kiln images import --from-easel-run {run} --keep-only")
    untriaged = [i for i, r in live.items() if images_mod.is_untriaged(r)]
    if untriaged:
        return _stage("03-dataset-images", f"{len(untriaged)} image(s) without a disposition", project)
    frozen = snapshot_mod.snapshot_path(project, version).exists()
    if not frozen:
        plan = snapshot_mod.plan(project, version)
        if plan.uncaptioned or plan.holdout_uncaptioned:
            n = len(plan.uncaptioned) + len(plan.holdout_uncaptioned)
            return _stage("05-captions", f"{n} member(s) of {version} without an approved caption", project)
        if plan.joined_train:
            return _stage("06-split", f"{len(plan.joined_train)} new kept image(s) without a split", project)
    ready, why = _version_ready(project, version)
    if not ready:
        command = "kiln dataset check --leaks" if not frozen else f"kiln dataset materialize {version}"
        return _stage("07-freeze-and-gate", why, project, command)
    return _stage("08-train", why, project, f"kiln runs list --family {_family(project)} --status ok")


def _without_goal(project: Project) -> Stage:
    if project.adopted:
        return _stage("90-adopt-existing", "an adopted project with no goal: set the goal first", project)
    missing = unanswered_required(project)
    if missing:
        return _stage("00-new-project", f"required intake answers missing: {', '.join(missing)}", project,
                      "kiln project intake show")
    images = images_mod.records(project)
    if not images:
        return _stage("01-probe-iterations", "the project has no image yet", project)
    untriaged = [i for i, r in images.items() if images_mod.is_untriaged(r)]
    if untriaged:
        return _stage("03-dataset-images", f"{len(untriaged)} image(s) without a disposition", project)
    unvetted = review_sheet_mod.unvetted(project, images)
    if unvetted:
        return _stage("04-vetting", f"{len(unvetted)} kept image(s) without a verdict for every checklist item "
                                    "that applies", project)
    kept = captions_mod.training_images(images)
    states = captions_mod.status(project)
    waiting = len(kept) - len(states["approved"])
    if waiting:
        return _stage("05-captions", f"{waiting} kept image(s) without an approved caption", project)
    unassigned = [i for i, r in kept.items() if r.get("split") == "unassigned"]
    if unassigned:
        return _stage("06-split", f"{len(unassigned)} kept image(s) without a split", project)
    latest = snapshot_mod.latest_version(project)
    in_latest = {m["image"] for m in snapshot_mod.load(project, latest)["members"]} if latest else set()
    if kept and set(kept) - in_latest:
        return _stage("07-freeze-and-gate", f"{len(set(kept) - in_latest)} kept image(s) in no frozen version",
                      project)
    if latest:
        ready, why = _version_ready(project, latest)
        if not ready:
            return _stage("07-freeze-and-gate", why, project, f"kiln dataset materialize {latest}")
    runs = [r for r in status_mod.runs(project) if latest and r.get("snapshot") == f"{project.slug}/{latest}"]
    picked = [r for r in runs if r.get("status") == "ok" and r.get("selected_checkpoint") is not None]
    if picked:
        return _stage("10-showcase", f"run {picked[-1]['id']} has a picked checkpoint", project)
    finished = [r for r in runs if r.get("status") == "ok"]
    if finished:
        return _stage("09-evaluate", f"run {finished[-1]['id']} finished; its checkpoints wait for evaluation",
                      project)
    return _stage("08-train", f"version {latest} is frozen and materialised; no finished run on it yet", project,
                  f"kiln runs list --family {_family(project)} --status ok")


def decide(workspace: str | None = None, slug: str | None = None) -> Stage:
    """The stage of `slug` (or the only project) in the instance at `workspace`."""
    try:
        instance = Instance.load(workspace)  # a --workspace or KILN_HOME naming no file stays an error (exit 2)
    except ProjectError:
        return _stage("00-new-project", "no kiln instance found", None, NO_INSTANCE_COMMAND)
    if slug is None and not instance.slugs():
        return _stage("00-new-project", "the instance has no project yet", None)
    project = instance.project(slug)
    goal = project.goal()
    return _with_goal(project, goal) if goal else _without_goal(project)
