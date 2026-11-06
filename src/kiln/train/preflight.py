"""`kiln train preflight <version>`: every gate before a training run starts, one line each, then `next:`.

Why: a training round has about ten gates (images triaged, captions approved, the split, a frozen and
materialised version with no lineage leak, disk, RAM, a planned run, on an adopted instance the rendered launcher
and the host queue, and the operator's "train"). Checking them by hand is slow and easy to get wrong; this reads
the project's files and the machine and changes nothing.

Each gate is a plug-in: a function of the `Context` that returns a `Gate` with status `ok`, `TODO` (you can fix it
with the command in `todo`) or `WAIT` (it waits for the operator or for the machine). `GATES` is the list every
project runs; `ADOPT_GATES` are added on an adopted instance (or for a run planned with `--emit-legacy`): the
launcher exists, and the queue file is validated by `check_queue`. The last gate is always the operator's go,
which reads `WAIT` until the operator says "train" (this command never records that word).

Before the version is frozen, triage, captions, split and leaks are computed from what a freeze would take in;
once it is frozen they are computed from its members (images added since wait for the next version).
Exit code: 0 when every gate but the operator's go is ok, 1 otherwise.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import scheduler, sysinfo

from ..project import images as images_mod
from ..project import lineage
from ..project import materialize as materialize_mod
from ..project import snapshot as snapshot_mod
from ..project.model import Project, ProjectError, check_version
from . import runs as runs_mod

OUTPUTS_GB = 10  # room for one run's checkpoints and states on top of the disk floor
GO_GATE = "operator's go"


@dataclass
class Gate:
    name: str
    status: str  # ok | TODO | WAIT
    detail: str
    todo: str = ""

    def line(self) -> str:
        return f"  {self.status:<5} {self.name:<18} {self.detail}"

    def as_dict(self) -> dict:
        return {"gate": self.name, "status": self.status, "detail": self.detail, "todo": self.todo}


def ok(name: str, detail: str) -> Gate:
    return Gate(name, "ok", detail)


@dataclass
class Context:
    """What the gates read: the project, the version, its snapshot (None before the freeze) and the run."""

    project: Project
    version: str
    family: str
    run: dict | None
    snapshot: dict | None
    plan: snapshot_mod.Plan | None
    legacy_root: Path | None
    adopted: bool
    extra: dict = field(default_factory=dict)

    @property
    def frozen(self) -> bool:
        return self.snapshot is not None

    @property
    def snapshot_id(self) -> str:
        return f"{self.project.slug}/{self.version}"


# --- the gates -----------------------------------------------------------------------------------------------

def gate_snapshot(ctx: Context) -> Gate:
    if ctx.frozen:
        c = ctx.snapshot["counts"]
        return ok("snapshot", f"{ctx.snapshot_id} frozen: {len(ctx.snapshot['members'])} members "
                              f"({c['train']} train, {c['holdout']} holdout)")
    return Gate("snapshot", "TODO", f"{ctx.snapshot_id} is not frozen", f"kiln dataset freeze {ctx.version}")


def gate_triage(ctx: Context) -> Gate:
    images = images_mod.records(ctx.project)
    waiting = sorted(i for i, r in images.items() if images_mod.is_untriaged(r) and not images_mod.is_legacy_untriaged(r))
    if ctx.frozen:
        return ok("triage", f"the version is frozen; {len(waiting)} later image(s) without a disposition wait for "
                            "the next version")
    if waiting:
        return Gate("triage", "WAIT", f"{len(waiting)} image(s) without a disposition",
                    "the operator triages them (runbook 03-dataset-images): " + " ".join(waiting[:12])
                    + (" ..." if len(waiting) > 12 else ""))
    return ok("triage", "every image has a disposition (legacy rows nobody triaged are not counted)")


def gate_captions(ctx: Context) -> Gate:
    if ctx.frozen:
        missing = [m["image"] for m in ctx.snapshot["members"] if m["split"] == "train" and not m.get("caption")]
        if missing:
            return Gate("captions", "TODO", f"{len(missing)} train member(s) of {ctx.version} without a caption",
                        "freeze a new version once their captions are approved (runbook 05-captions)")
        return ok("captions", "every train member has its caption")
    missing = ctx.plan.uncaptioned
    if missing:
        return Gate("captions", "TODO", f"{len(missing)} train image(s) without an approved caption",
                    "kiln captions status (runbook 05-captions)")
    return ok("captions", "every train image has an approved caption")


def gate_split(ctx: Context) -> Gate:
    if ctx.frozen:
        return ok("split", "fixed by the frozen version")
    if ctx.plan.joined_train:
        return Gate("split", "TODO", f"{len(ctx.plan.joined_train)} kept image(s) without a split",
                    "kiln dataset split --holdout <n> (dry run first; runbook 06-split)")
    return ok("split", "every kept image is in train or holdout")


def gate_leaks(ctx: Context) -> Gate:
    if ctx.frozen:
        assignment = {m["image"]: m["split"] for m in ctx.snapshot["members"]}
        found = lineage.leaks(assignment, images_mod.records(ctx.project), ctx.snapshot_id)
    else:
        found = ctx.plan.leaks
    if found:
        return Gate("leaks", "TODO", f"{len(found)} lineage leak(s) in {ctx.snapshot_id}",
                    "kiln dataset check --leaks, then fix the split (runbook 06-split)")
    return ok("leaks", "no lineage on both sides of the split")


def gate_materialised(ctx: Context) -> Gate:
    if not ctx.frozen:
        return Gate("materialised", "TODO", "nothing to materialise before the freeze",
                    f"kiln dataset freeze {ctx.version}, then kiln dataset materialize {ctx.version}")
    folder = materialize_mod.check_folder(ctx.project, ctx.version)
    shown = ctx.project.shown(folder.base)
    if not folder.found:
        return Gate("materialised", "TODO", f"{shown} has no materialised record",
                    f"kiln dataset materialize {ctx.version}")
    if folder.foreign:
        return Gate("materialised", "TODO", f"{shown} holds a record written outside kiln",
                    f"kiln dataset materialize {ctx.version} --into <dir>")
    if folder.problems:
        return Gate("materialised", "TODO", f"{shown} does not match its snapshot: {folder.problems[0]}",
                    f"kiln dataset check {ctx.version}")
    return ok("materialised", f"{shown} matches the snapshot ({folder.counts.get('train', 0)} train, "
                              f"{folder.counts.get('holdout', 0)} holdout)")


DEFAULT_DISK_FLOOR_GB = 20.0  # a neutral default when limits.disk_floor_gb is unset; an instance sets its own


def gate_disk(ctx: Context) -> Gate:
    floor = float(ctx.project.instance.cfg.get("limits.disk_floor_gb", DEFAULT_DISK_FLOOR_GB) or 0)
    where = ctx.legacy_root if ctx.adopted and ctx.legacy_root is not None else ctx.project.instance.root
    need = floor + OUTPUTS_GB
    try:
        free = sysinfo.disk_free_gb(where)
    except sysinfo.SysinfoUnavailable as exc:
        return Gate("disk", "WAIT", f"free disk cannot be measured: {exc}", "tell the operator")
    if free >= need:
        return ok("disk", f"{free:.0f} GB free (need {floor:g} floor + {OUTPUTS_GB} for the run)")
    return Gate("disk", "TODO", f"{free:.0f} GB free, need {floor:g} floor + {OUTPUTS_GB} for the run",
                "tell the operator: they decide what is freed; never delete anything yourself")


def gate_ram(ctx: Context) -> Gate:
    try:
        need = scheduler.family_host_ram(ctx.family, "train")
    except scheduler.SchedulerError as exc:
        return Gate("RAM", "TODO", "; ".join(exc.lines), "kiln families check")
    if need is None:
        return Gate("RAM", "TODO", f"families/{ctx.family}.yaml has no host_ram_gb.train", "kiln families check")
    if ctx.project.instance.cfg.get("limits.skip_ram_check", False):
        return ok("RAM", f"not measured (limits.skip_ram_check); training waits for {need:g} GB")
    try:
        free = sysinfo.free_ram_gb()
    except sysinfo.SysinfoUnavailable:
        return Gate("RAM", "WAIT", "cannot measure free RAM; set limits.skip_ram_check = true to skip",
                    "tell the operator; never set the key yourself")
    if free >= need:
        return ok("RAM", f"{free:.1f} GB free (training starts at {need:g})")
    return Gate("RAM", "WAIT", f"{free:.1f} GB free, training starts at {need:g}",
                "wait and tell the operator; never free memory or lower a threshold yourself")


def gate_run(ctx: Context) -> Gate:
    if ctx.run is None:
        return Gate("run planned", "TODO", f"no planned run on {ctx.snapshot_id}",
                    f"kiln train plan {ctx.version} --family {ctx.family} --base-from-run <run-id> --steps <n>"
                    + (" --emit-legacy" if ctx.adopted else ""))
    steps = (ctx.run.get("steps") or {}).get("planned")
    licence = ((ctx.run.get("base") or {}).get("licence") or {}).get("status", "unknown")
    return ok("run planned", f"{ctx.run['id']}: {ctx.run.get('family')}, {steps} steps, base licence {licence}")


def gate_launcher(ctx: Context) -> Gate:
    block = (ctx.run or {}).get("legacy") or {}
    if not block.get("launcher"):
        return Gate("launcher", "TODO", "no host launcher rendered for this run",
                    f"kiln train plan {ctx.version} --family {ctx.family} ... --emit-legacy")
    if ctx.legacy_root is None:
        return Gate("launcher", "TODO", "the legacy root is not configured", "kiln config set legacy.root <folder>")
    path = ctx.legacy_root / block["launcher"]
    if not path.is_file():
        return Gate("launcher", "TODO", f"{Path(block['launcher']).name} is missing", "plan again with --emit-legacy")
    return ok("launcher", f"{Path(block['launcher']).name} rendered for {block.get('run_id')}")


def gate_queue(ctx: Context) -> Gate:
    from .legacy_emit import check_queue

    block = (ctx.run or {}).get("legacy") or {}
    if not block.get("queue") or ctx.legacy_root is None:
        return Gate("queue", "TODO", "no host queue file for this run", "plan again with --emit-legacy")
    path = ctx.legacy_root / block["queue"]
    problems, jobs = check_queue(path, ctx.legacy_root, block.get("run_id"))
    if problems:
        return Gate("queue", "TODO", f"{path.name}: {problems[0]}" + (f" (+{len(problems) - 1} more)"
                                                                       if len(problems) > 1 else ""),
                    "plan again with --emit-legacy; never edit the queue file by hand")
    return ok("queue", f"queue validated by check_queue: {path.name}, {jobs} job(s)")


def gate_go(ctx: Context) -> Gate:
    return Gate(GO_GATE, "WAIT", "training starts only on the operator's word \"train\"",
                "ask the operator (runbook 08-train, section 5)")


GATES: list[Callable[[Context], Gate]] = [gate_snapshot, gate_triage, gate_captions, gate_split, gate_leaks,
                                          gate_materialised, gate_disk, gate_ram, gate_run]
ADOPT_GATES: list[Callable[[Context], Gate]] = [gate_launcher, gate_queue]


# --- running them --------------------------------------------------------------------------------------------

def _pick_run(project: Project, snapshot_id: str, run_id: str | None) -> dict | None:
    if run_id:
        run = runs_mod.load(project, run_id)
        if run.get("snapshot") != snapshot_id:
            raise ProjectError(2, f"run {run_id} trains {run.get('snapshot')}, not {snapshot_id}")
        return run
    planned = [r for r in runs_mod.read_all(project).runs
               if r.get("snapshot") == snapshot_id and r.get("status") in ("planned", "running")]
    return planned[-1] if planned else None


def context(project: Project, version: str, *, family: str | None = None, run_id: str | None = None,
            legacy_root: Path | str | None = None) -> Context:
    check_version(version)
    snapshot_id = f"{project.slug}/{version}"
    frozen = snapshot_mod.snapshot_path(project, version).exists()
    snap = snapshot_mod.load(project, version) if frozen else None
    plan = None if frozen else snapshot_mod.plan(project, version)
    run = _pick_run(project, snapshot_id, run_id)
    fam = family or (run or {}).get("family") or next(iter(project.data.get("families") or []), None)
    if not fam:
        raise ProjectError(2, "no family: pass --family <family> (or plan the run first)")
    adopted = bool(project.adopted or project.instance.cfg.get("instance.adopted", False)
                   or (run or {}).get("legacy"))
    return Context(project=project, version=version, family=str(fam), run=run, snapshot=snap, plan=plan,
                   legacy_root=runs_mod.legacy_root_of(project, legacy_root), adopted=adopted)


def gates(ctx: Context) -> list[Gate]:
    """Every gate's result, in order; the operator's go last."""
    chosen = list(GATES) + (list(ADOPT_GATES) if ctx.adopted else [])
    return [g(ctx) for g in chosen] + [gate_go(ctx)]


def next_line(results: list[Gate]) -> str:
    first = next((g for g in results if g.status != "ok" and g.name != GO_GATE), None)
    if first is None:
        return "next: all gates pass; ask the operator for the go and wait for the word \"train\""
    return f"next: {first.name}: {first.todo}"


def exit_code(results: list[Gate]) -> int:
    return 0 if all(g.status == "ok" for g in results if g.name != GO_GATE) else 1
