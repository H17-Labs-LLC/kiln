"""The one image-source interface: what a source is asked, what it hands back, and how its images enter a project.

A source that makes new images (`codex`, later `comfy`) is driven by a jobs file (`schemas/jobs-file.schema.json`):
`load_jobs` reads and checks it, resolves each job's references and composes its prompt, and `Source.run(job, ctx)`
returns the files it produced. A source that brings existing images (`files`, `easel-run`) is called from
`kiln images import` instead. Either way the images enter the project the same way:

- `place` copies the bytes into the project's imports root (sha256 checked when the source names a hash); it
  never writes under `images_root` and never replaces a different file of the same name;
- `known_image` answers "are these bytes registered already?" before anything is copied: one image id per bytes
  (`img-<sha256[:12]>`), so a second import is reported and changes nothing;
- registration always goes through `kiln.project.images.register`, so ids, lineage and line order stay right.

Images are never opened for content: hash, size and header only. Every message names paths relative to the
instance root (or the source's own placeholder, such as `<easel_home>`), so output is safe for docs.

References in a jobs file (`refs`, `parent`, `anchors`): an image id; a path relative to the project folder; the
name of an `anchors` entry (expanded in place); or `@<role>`, `@<role>#<k>`, `@<role>*`: the role group's images,
that is the images the operator labelled `anchor:<role>` (`kiln images label <id> --label anchor:<role>`), CORE
tier first, then by id: the primary, the k-th one, or all of them. A reference without a role plays `identity`;
an edit job's parent is always attached first, as Image A, with the role `edit target`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import hashing, ids, store
from kiln.project import images as images_mod
from kiln.project.model import SCHEMAS_DIR, Project, ProjectError, now, why

# Plain words for the jobs file's viewpoints and framings: the prompt states them, a caption draft reuses them.
VIEW_WORDS = {"front": "front view", "front_three_quarter": "three-quarter view", "profile": "side view",
              "back": "back view", "rear_three_quarter": "rear three-quarter view", "unusual": None}
FRAMING_WORDS = {"close": "close-up", "medium": "upper body", "full": "full body", "wide": "wide shot"}
RUN_ID = re.compile(r"^run-\d{8}-[a-z0-9][a-z0-9_-]*$")
RCP_ID = re.compile(r"^rcp-\d{8}-\d{3}$")
_MAX_ANCHOR_DEPTH = 5


# --- what a source works with ------------------------------------------------------------------------------------

@dataclass
class Ref:
    """One resolved reference: what the jobs file named, the file it is, and the role it plays."""

    name: str
    path: Path
    role: str | None = None


@dataclass
class Job:
    """One job of a jobs file, resolved: its fields, references, parent, the composed prompt and any problems."""

    raw: dict
    refs: list[Ref] = field(default_factory=list)
    parent: dict | None = None
    prompt: str = ""
    caption_basis: str = ""
    problems: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return str(self.raw["id"])

    @property
    def want(self) -> int:
        return int(self.raw.get("want") or 1)

    def labels(self, jobs_name: str) -> list[str]:
        """The labels its images get: the job (`job:<file>/<id>`) and the outfit asked for."""
        out = [f"job:{jobs_name}/{self.id}"]
        outfit = self.raw.get("outfit")
        if isinstance(outfit, str) and re.fullmatch(r"\S+", outfit):
            out.append(f"outfit:{outfit}")
        return out


@dataclass
class JobsFile:
    """A loaded jobs file: where it is, its data, the folder its images go to, and its resolved jobs."""

    path: Path
    rel: str
    data: dict
    out_dir: Path
    jobs: list[Job] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return str(self.data["name"])

    @property
    def kind(self) -> str:
        return str(self.data["source"])


@dataclass
class RunContext:
    """What a source's `run` needs besides the job: the project, the jobs file and the source's settings."""

    project: Project
    jobs: JobsFile
    settings: dict = field(default_factory=dict)


@dataclass
class Outcome:
    """What one job produced: a status, the files (outside the project), lines to print and manifest fields.

    status: ok | empty | blocked | refused | quota | failed. `exit_code` is the source's own code for the job.
    """

    status: str
    exit_code: int = 0
    files: list[Path] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)


class Source:
    """The interface every source kind implements. `makes_images` sources run jobs; the others import."""

    kind = ""
    makes_images = False

    def check(self, job: Job, ctx: RunContext) -> list[str]:
        """Problems that stop the job before anything is spent ([] = fine)."""
        return []

    def describe(self, job: Job, ctx: RunContext) -> list[str]:
        """The dry-run lines: what would be sent for this job. Never sends anything."""
        return []

    def run(self, job: Job, ctx: RunContext) -> Outcome:
        """Make the job's images; the caller copies and registers them."""
        raise ProjectError(2, f"the {self.kind} source does not run jobs")


# --- getting bytes in --------------------------------------------------------------------------------------------

def known_image(images: Mapping[str, dict], sha256: str) -> dict | None:
    """The registered record of these bytes, or None."""
    return images.get(ids.image_id(sha256))


def imports_dir(project: Project) -> Path:
    """The project's imports root; refused when it is not set or lies under images_root."""
    folder = project.root_dir("imports")
    if project.instance.under_images_root(folder):
        raise ProjectError(2, [f"roots.imports of {project.slug} lies under images_root, which is read-only",
                               "point roots.imports at projects/<slug>/images in project.yaml"])
    return folder


def inside(child: Path, parent: Path) -> bool:
    child, parent = Path(child).resolve(), Path(parent).resolve()
    return child == parent or parent in child.parents


def place(project: Project, data: bytes, dest: Path) -> str:
    """Write `data` at `dest` inside the imports root: `copied`, or `present` when dest already holds these bytes.

    A different file at `dest` is a conflict (ProjectError 1): nothing is replaced.
    """
    folder = imports_dir(project)
    if not inside(dest, folder) or project.instance.under_images_root(dest):
        raise ProjectError(2, f"refusing to write {project.shown(dest)}: outside the imports root "
                              f"{project.shown(folder)}")
    if dest.exists():
        if dest.is_file() and dest.read_bytes() == data:
            return "present"
        raise ProjectError(1, f"{project.shown(dest)} exists with different bytes; nothing replaced")
    dest.parent.mkdir(parents=True, exist_ok=True)
    store.write_bytes(dest, data)
    return "copied"


def free_name(folder: Path, stem: str, suffix: str, data: bytes) -> Path:
    """`<stem><suffix>`, or `<stem>-2<suffix>`, ... : the first name that is free or already holds `data`."""
    for k in range(1, 1000):
        candidate = folder / (f"{stem}{suffix}" if k == 1 else f"{stem}-{k}{suffix}")
        if not candidate.exists() or (candidate.is_file() and candidate.read_bytes() == data):
            return candidate
    raise ProjectError(1, f"no free file name for {stem}{suffix} in {folder.name}")


def sha_of(data: bytes) -> str:
    return hashing.sha256_bytes(data)


# --- jobs files --------------------------------------------------------------------------------------------------

def _validator():
    from jsonschema import Draft202012Validator

    return Draft202012Validator(json.loads((SCHEMAS_DIR / "jobs-file.schema.json").read_text(encoding="utf-8")))


def find_jobs_file(project: Project, given: Path) -> Path:
    """The jobs file as given, else relative to the project folder, else to the instance root."""
    for candidate in (Path(given), project.folder / given, project.instance.root / given):
        if candidate.is_file():
            return candidate.resolve()
    raise ProjectError(2, f"no jobs file {Path(given).as_posix()} (looked here, in the project folder "
                          f"{project.shown(project.folder)} and in the instance root)")


def load_spec(project: Project) -> dict:
    """The project's subject spec (spec.json), or {} when it has none."""
    spec = project.folder / str(project.data.get("spec") or "spec.json")
    if not spec.is_file():
        return {}
    try:
        return store.read_record(spec)
    except (OSError, ValueError):
        return {}


def load_jobs(project: Project, given: Path) -> JobsFile:
    """Read, check and resolve a jobs file. Schema errors are refused (exit 2); job problems are collected."""
    path = find_jobs_file(project, given)
    rel = project.instance.relpath(path) or path.name
    try:
        rel = path.relative_to(project.folder.resolve()).as_posix()
    except ValueError:
        pass
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise ProjectError(2, f"{rel}: not a readable jobs file: {why(exc, path)}") from exc
    errors = sorted(_validator().iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        lines = [f"{rel}: not a valid jobs file (schemas/jobs-file.schema.json):"]
        lines += [f"  {'.'.join(map(str, e.absolute_path)) or 'top'}: {e.message}" for e in errors[:10]]
        raise ProjectError(2, lines)
    if data["project"] != project.slug:
        raise ProjectError(2, f"{rel} is for project {data['project']}, not {project.slug}: pass --project "
                              f"{data['project']}")
    out_dir = (project.instance.root / data["out_dir"]).resolve()
    jobs = JobsFile(path=path, rel=rel, data=data, out_dir=out_dir)
    folder = imports_dir(project)
    if not inside(out_dir, folder):
        jobs.problems.append(f"out_dir {data['out_dir']} is not inside the imports root {project.shown(folder)}")
    known_groups = set(data.get("groups") or {})
    seen: set[str] = set()
    images = images_mod.records(project)
    spec = load_spec(project)
    age_words = _age_words(project)
    for raw in data["jobs"]:
        job = Job(raw=raw)
        if job.id in seen:
            job.problems.append(f"job {job.id}: the id appears twice")
        seen.add(job.id)
        if raw.get("group") not in known_groups:
            job.problems.append(f"job {job.id}: group {raw.get('group')!r} is not in groups")
        refs = raw.get("refs")
        if refs is not None:
            job.refs, problems = resolve_refs(project, images, data.get("anchors") or {}, refs)
            job.problems += [f"job {job.id}: {p}" for p in problems]
        if raw.get("parent") is not None:
            parents, problems = resolve_refs(project, images, data.get("anchors") or {}, [raw["parent"]])
            job.problems += [f"job {job.id}: parent {p}" for p in problems]
            if parents:
                record = _record_of(images, project, parents[0])
                if record is None:
                    job.problems.append(f"job {job.id}: the parent {parents[0].name} is not a registered image")
                job.parent = record
        if raw.get("mode") == "edit" and raw.get("parent") is None:
            job.problems.append(f"job {job.id}: an edit job names its parent image")
        if job.parent is not None:  # the edit target is always attached first, as Image A
            target = Ref(job.parent["id"], project.image_path(job.parent), "edit target")
            job.refs = [target] + [r for r in job.refs if r.path.resolve() != target.path.resolve()]
        for word in age_words:
            if any(_has_word(str(raw.get(k) or ""), word) for k in ("scene", "intent", "tests")):
                job.problems.append(f"job {job.id}: the word {word!r} makes the subject read young "
                                    "(policy.subjects = fictional-adults-only): rewrite the scene")
        job.prompt, job.caption_basis = compose_prompt(project, spec, raw, job.refs)
        jobs.jobs.append(job)
    return jobs


def _age_words(project: Project) -> list[str]:
    """The caption rules' age words: a prompt carrying one is refused before anything is sent."""
    from kiln.project.captions import load_rules

    try:
        return list(load_rules(project).forbidden.get("age_words") or [])
    except (OSError, ValueError):
        return []


def _has_word(text: str, word: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(word.lower())}(?![\w-])", text.lower()) is not None


def _record_of(images: Mapping[str, dict], project: Project, ref: Ref) -> dict | None:
    if ref.name in images:
        return images[ref.name]
    if ref.path.is_file():
        return known_image(images, hashing.sha256_file(ref.path))
    return None


def _anchor_images(images: Mapping[str, dict], role: str) -> list[dict]:
    wanted = f"anchor:{role}"
    found = [r for r in images.values() if wanted in (r.get("labels") or []) and r.get("tier") != "REJECTED"]
    return sorted(found, key=lambda r: (r.get("tier") != "CORE", r["id"]))


def resolve_refs(project: Project, images: Mapping[str, dict], anchors: Mapping[str, object],
                 refs: object, depth: int = 0) -> tuple[list[Ref], list[str]]:
    """Resolve a jobs file's `refs` (a string or a list) into files; returns (refs, problems)."""
    if depth > _MAX_ANCHOR_DEPTH:
        return [], ["anchors nest too deep (a loop?)"]
    items: Sequence[object]
    if isinstance(refs, str):
        items = [refs]
    elif isinstance(refs, list):
        items = refs
    else:
        return [], [f"refs must be a name or a list: {refs!r}"]
    out: list[Ref] = []
    problems: list[str] = []
    for item in items:
        name, role = (item.get("ref"), item.get("role")) if isinstance(item, dict) else (item, None)
        name = str(name)
        if name in anchors:
            inner, more = resolve_refs(project, images, anchors, anchors[name], depth + 1)
            out += [Ref(r.name, r.path, role or r.role) for r in inner]
            problems += more
        elif name.startswith("img-"):
            record = images.get(name)
            if record is None:
                problems.append(f"reference {name} is not an image of {project.slug}")
                continue
            try:
                out.append(Ref(name, project.image_path(record), role))
            except ProjectError as exc:
                problems.append(f"reference {name}: {exc.lines[0]}")
        elif name.startswith("@"):
            match = re.fullmatch(r"@([a-z][a-z0-9_]*)(?:#(\d+)|(\*))?", name)
            if not match:
                problems.append(f"reference {name}: use @<role>, @<role>#<k> or @<role>*")
                continue
            found = _anchor_images(images, match.group(1))
            if match.group(3):
                chosen = found
            else:
                k = int(match.group(2) or 1)
                chosen = found[k - 1:k] if k >= 1 else []
            if not chosen:
                problems.append(f"reference {name}: no image is labelled anchor:{match.group(1)}"
                                + ("" if not found else f" at position {match.group(2)}"))
            for record in chosen:
                try:
                    out.append(Ref(record["id"], project.image_path(record), role or match.group(1)))
                except ProjectError as exc:
                    problems.append(f"reference {name} ({record['id']}): {exc.lines[0]}")
        else:
            rel = Path(name)
            if rel.is_absolute() or ".." in rel.parts or "\\" in name:
                problems.append(f"reference {name}: a path is relative to the project folder, forward slashes, "
                                "no '..'")
                continue
            path = project.folder / rel
            if not path.is_file():
                problems.append(f"reference {name}: no such file in the project folder")
                continue
            out.append(Ref(name, path, role))
    unique: list[Ref] = []
    seen: set[Path] = set()
    for ref in out:
        key = ref.path.resolve()
        if key not in seen:
            seen.add(key)
            unique.append(ref)
    return unique, problems


def real(value: object) -> str | None:
    """A filled-in string, or None for an empty value or a template placeholder such as `<outfit token>`."""
    if not isinstance(value, str) or not value.strip() or "<" in value:
        return None
    return value.strip()


def class_word(project: Project, spec: Mapping | None = None) -> str | None:
    spec = spec if spec is not None else load_spec(project)
    return real(project.data.get("class_word")) or real((spec.get("caption") or {}).get("class_word"))


def compose_prompt(project: Project, spec: Mapping, job: Mapping, refs: Sequence[Ref]) -> tuple[str, str]:
    """(the prompt a job sends, the caption basis its images keep).

    The first line is the short form: trigger, class word, view, framing, scene. Then, from the approved spec, the
    adult line, every checklist item that can be seen in this view and framing (`skip_when`), the default outfit
    when the job asks for it, and the style. The caption basis is the scene alone: a caption describes what the
    picture shows, never the instructions around it. This is a starting composer: `--dry-run` prints it whole.
    """
    viewpoint, framing = job.get("viewpoint"), job.get("framing")
    scene = (job.get("scene") or "").strip()
    head = ", ".join(x for x in (project.trigger, class_word(project, spec), VIEW_WORDS.get(viewpoint),
                                 FRAMING_WORDS.get(framing), scene) if x)
    lines = [head]
    if job.get("mode") == "edit":
        lines.append(f"EDIT: change the first image only as this request asks ({job.get('intent')}); keep "
                     "everything else as it is.")
    if refs:
        lines.append("REFERENCES: " + "; ".join(f"Image {chr(ord('A') + i)} = {ref.role or 'identity'}"
                                                for i, ref in enumerate(refs)))
    heading = real(spec.get("subject_heading"))
    adult = real(spec.get("adult"))
    if adult:
        lines.append(f"{heading or 'THE SUBJECT'}: {adult}.")
    for item in spec.get("checklist") or []:
        skip = item.get("skip_when") or {}
        if viewpoint in (skip.get("viewpoint") or []) or framing in (skip.get("framing") or []):
            continue
        text = real(item.get("text"))
        if text:
            lines.append(f"{real(item.get('heading')) or str(item.get('key', '')).upper()}: {text.rstrip('.')}.")
    outfit = spec.get("outfit") or {}
    if job.get("outfit") == "default" and isinstance(outfit, dict):
        parts = [real(v) for k, v in outfit.items() if k != "rating"]
        parts = [p for p in parts if p]
        if parts:
            lines.append("OUTFIT: " + "; ".join(parts) + ".")
    style = real((spec.get("caption") or {}).get("style_tag"))
    if style:
        lines.append(f"STYLE: {style}.")
    return "\n".join(lines), scene


def manifest_record(project: Project, jobs: JobsFile, job: Job, outcome: Outcome,
                    registered: Sequence[tuple[Path, dict]]) -> dict:
    """The generation manifest of one job (`schemas/generation-manifest.schema.json`), paths project-relative."""
    spec = project.folder / str(project.data.get("spec") or "spec.json")
    images = []
    for path, record in registered:
        try:
            rel = path.resolve().relative_to(project.folder.resolve()).as_posix()
        except ValueError:
            rel = project.shown(path)
        images.append({"file": rel, "sha256": record["sha256"], "width": record.get("width"),
                       "height": record.get("height"), "bytes": record["bytes"], "image": record["id"]})
    extra = dict(outcome.manifest)
    return {
        "project": project.slug, "generated_at": now(), "backend": jobs.kind,
        "tool_version": extra.get("tool_version"), "model": extra.get("model"), "thread_id": extra.get("thread_id"),
        "job": {"file": jobs.rel, "id": job.id, "group": job.raw.get("group")},
        "spec_sha256": hashing.sha256_file(spec) if spec.is_file() else None,
        "prompt": job.prompt, "references": [ref.name for ref in job.refs], "requested_count": job.want,
        "exit_code": int(extra.get("exit_code", outcome.exit_code)), "images": images,
        "verbatim": extra.get("verbatim"), "refs_sent": extra.get("refs_sent"), "quota": extra.get("quota"),
        "failed_generations": int(extra.get("failed_generations") or 0),
        "moderation_blocked": int(extra.get("moderation_blocked") or 0),
        "errors": [str(e)[:300] for e in extra.get("errors") or []], "note": extra.get("note"),
    }
