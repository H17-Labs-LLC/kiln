"""`kiln source run <jobs.json> [--dry-run]`: run a jobs file through its source, or show what it would send.

The dry run resolves every job (references, parent, the composed prompt, the count) and prints it; nothing is
sent and nothing is written. A real run first refuses the whole file when any job has a problem (an unknown
reference, too many attachments, an age word in a scene), so nothing is spent on a half-valid batch. Then, job by
job: the source makes its images; each new file is copied into the jobs file's `out_dir` as
`<job id>-<prefix>-<k>.png` and registered as an untriaged candidate (`source.kind` = the source, the job's
labels, viewpoint and framing, an edit job's image as a child of its parent with `synthetic_detail_added = 1`, the
scene as `caption_basis`); `<out_dir>/<job id>.manifest.json` records what was asked and what came back. A
`quota` outcome stops the batch (exit 3); a blocked, refused or empty job is recorded and the batch goes on
(exit 1 at the end). Images are never opened for content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import store
from kiln.project import images as images_mod
from kiln.project.model import Project, ProjectError

from . import base, get

MANIFEST_SCHEMA = "kiln/generation-manifest/1"


@dataclass
class Report:
    lines: list[str] = field(default_factory=list)
    registered: list[str] = field(default_factory=list)
    code: int = 0


def _settings(project: Project, kind: str) -> dict:
    if kind == "codex":
        from .codex import load_settings

        return load_settings(project)
    return {}


def prepare(project: Project, given: Path) -> tuple[base.JobsFile, base.Source, base.RunContext]:
    jobs = base.load_jobs(project, given)
    source = get(jobs.kind)
    if not source.makes_images:
        source.run(jobs.jobs[0], base.RunContext(project=project, jobs=jobs))  # refuses, naming the command to use
    ctx = base.RunContext(project=project, jobs=jobs, settings=_settings(project, jobs.kind))
    for job in jobs.jobs:
        job.problems += source.check(job, ctx)
    return jobs, source, ctx


def dry_run(project: Project, given: Path) -> Report:
    jobs, source, ctx = prepare(project, given)
    report = Report()
    asked = sum(job.want for job in jobs.jobs)
    report.lines.append(f"jobs file {jobs.rel}: {len(jobs.jobs)} job(s), source {jobs.kind}, "
                        f"out {project.shown(jobs.out_dir)}")
    for problem in jobs.problems:
        report.lines.append(f"  problem: {problem}")
    for job in jobs.jobs:
        raw = job.raw
        report.lines.append(f"  {job.id}  {raw['mode']:<8}  {raw['viewpoint']}/{raw['framing']}  want {job.want}  "
                            f"refs {len(job.refs)}  -> {job.id}-{raw['prefix']}-<k>.png")
        report.lines += [f"    {line}" for line in source.describe(job, ctx)]
        report.lines += [f"    prompt| {line}" for line in job.prompt.splitlines()]
        report.lines += [f"    problem: {p}" for p in job.problems]
    problems = len(jobs.problems) + sum(len(job.problems) for job in jobs.jobs)
    report.lines.append(f"dry run: nothing sent, nothing written; {asked} image(s) would be asked for"
                        + (f"; {problems} problem(s) to fix first" if problems else ""))
    report.code = 2 if problems else 0
    return report


def _register(project: Project, jobs: base.JobsFile, job: base.Job, files: list[Path], manifest_rel: str,
              known: dict) -> tuple[list[tuple[Path, dict]], list[str]]:
    """Copy and register one job's files; returns ((path, record) per image, problem lines)."""
    by = f"kiln source run {jobs.rel}"
    out, problems = [], []
    for path in files:
        data = path.read_bytes()
        sha = base.sha_of(data)
        record = base.known_image(known, sha)
        if record is not None:
            problems.append(f"{job.id}: {path.name} is already registered as {record['id']}; not registered again")
            continue
        dest = base.free_name(jobs.out_dir, f"{job.id}-{job.raw['prefix']}-{len(out) + 1}",
                              path.suffix.lower() or ".png", data)
        try:
            base.place(project, data, dest)
        except ProjectError as exc:
            problems += [f"{job.id}: {line}" for line in exc.lines]
            continue
        source = {"kind": jobs.kind, "tool": "kiln source run", "manifest": manifest_rel, "prompt": job.prompt,
                  "note": f"{jobs.name}/{job.id}"}
        lineage = {"parent": job.parent, "transform": "edit"} if job.parent is not None else {}
        line, created = images_mod.register(project, dest, source=source, by=by, labels=job.labels(jobs.name),
                                            caption_basis=job.caption_basis or None,
                                            viewpoint=job.raw.get("viewpoint"), framing=job.raw.get("framing"),
                                            images=known, **lineage)
        if not created:
            continue
        known[line["id"]] = line
        out.append((dest, line))
    return out, problems


def _manifest_path(out_dir: Path, job_id: str) -> Path:
    for k in range(1, 1000):
        path = out_dir / (f"{job_id}.manifest.json" if k == 1 else f"{job_id}-{k}.manifest.json")
        if not path.exists():
            return path
    raise ProjectError(1, f"no free manifest name for job {job_id}")


def run(project: Project, given: Path) -> Report:
    jobs, source, ctx = prepare(project, given)
    problems = jobs.problems + [p for job in jobs.jobs for p in job.problems]
    if problems:
        raise ProjectError(2, [f"REFUSED: {jobs.rel} has {len(problems)} problem(s); nothing was sent"]
                           + [f"  {p}" for p in problems] + [f"see them in context: kiln source run {jobs.rel} "
                                                              "--dry-run"])
    report = Report()
    stopped = False
    failed = 0
    for job in jobs.jobs:
        outcome = source.run(job, ctx)  # the source works without the lock; registering takes it
        report.lines += [f"{job.id}: {line}" for line in outcome.lines]
        manifest_path = _manifest_path(jobs.out_dir, job.id)
        manifest_rel = project.instance.relpath(manifest_path) or manifest_path.name
        with project.lock():
            known = images_mod.records(project)
            registered, more = _register(project, jobs, job, outcome.files, manifest_rel, known)
        report.lines += more
        record = base.manifest_record(project, jobs, job, outcome, registered)
        with project.lock():
            store.write_record(manifest_path, MANIFEST_SCHEMA, record)
        ids = [r["id"] for _, r in registered]
        report.registered += ids
        report.lines.append(f"{job.id}: {len(ids)} image(s) registered" + (f" ({' '.join(ids)})" if ids else "")
                            + f"; manifest {project.shown(manifest_path)}")
        if outcome.status != "ok":
            failed += 1
        if outcome.status == "quota":
            stopped = True
            report.lines.append(f"stopped after {job.id}: the usage window is spent; the jobs after it were not sent")
            break
    report.lines.append(f"{len(report.registered)} images registered from {jobs.rel} ({jobs.kind}); "
                        f"{failed} job(s) without images")
    report.lines.append("next: kiln review sheet (the operator triages the new candidates)")
    report.code = 3 if stopped else (1 if failed else 0)
    return report
