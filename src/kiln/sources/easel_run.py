"""The `easel-run` source: copy the outputs the operator kept in a reviewed easel run into the project.

`kiln images import --from-easel-run <run-id> --keep-only [--easel-home <dir>]` reads, from the easel instance
(`--easel-home`, else `[sources] easel_home`, else `$EASEL_HOME`):
- `runs/<run-id>/run.json`: the run's prompt, recipe and outputs (`n`, `file`, `sha256`);
- `runs/<run-id>/reviews.jsonl`: the verdicts. The current verdict of an output is the operator's latest line for
  its `n` (lines from any other source, such as the assistant's labelled reads, never count; a line with
  `n: null` judges the whole run and keeps nothing). On an adopted host these lines come from
  `easel runs import-legacy <id> --with-reviews`;
- `recipes/<recipe>.json` when present, for the content class (`sfw` stays `sfw`; anything else is `unknown`).

Each output whose current verdict is `keep` is copied, sha256 checked against run.json, to
`<imports root>/<run-id>/NNN.png` and registered with `root: imports`, `source.kind: generated`, the run's prompt
as `caption_basis`, no lineage parent, `tier: CANDIDATE`, `disposition: ACCEPT_TRAIN`, `split: unassigned`,
`labels` from the review's checklist keys (`check:<key>` for pass, `check-fail:<key>` for fail, `check-na:<key>`
for na: the same labels `kiln review apply` records, so an imported image counts as vetted) and
`verdict_source: easel-review:<run-id>`. The last line is
`N images registered as ACCEPT_TRAIN (verdicts from easel run <run-id>)`.

An output already registered (the same bytes) is reported and left as it is: a verdict given in kiln since then
stands. A run imported from a legacy host keeps its outputs where the host wrote them (`imported_from.manifest`,
relative to the host's output root): they are found there by name, else by sha256, under the project's
`roots.legacy_output`, else under the easel instance's `[adopt] output_root`.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import hashing, image_meta, store
from kiln.project import images as images_mod
from kiln.project.model import Project, ProjectError

from . import base

KEEP = "keep"


class EaselRunSource(base.Source):
    kind = "easel-run"
    makes_images = False

    def run(self, job, ctx):  # noqa: ARG002 - the interface; an easel run is imported, not run
        raise ProjectError(2, ["a jobs file with source easel-run has nothing to run",
                               "import the kept outputs: kiln images import --from-easel-run <run-id> --keep-only"])


@dataclass
class EaselRun:
    """One easel run as kiln reads it."""

    run_id: str
    folder: Path
    record: dict
    verdicts: dict[int, dict]
    recipe: dict = field(default_factory=dict)

    @property
    def prompt(self) -> str:
        return str(self.record.get("prompt") or "")

    def kept(self) -> list[dict]:
        """The outputs whose current verdict is keep, by n."""
        outputs = {int(o["n"]): o for o in self.record.get("outputs") or [] if isinstance(o.get("n"), int)}
        return [outputs[n] for n in sorted(outputs) if (self.verdicts.get(n) or {}).get("verdict") == KEEP]


@dataclass
class Imported:
    registered: list[dict] = field(default_factory=list)
    already: list[tuple[int, dict]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    kept: int = 0
    rejected: int = 0
    undecided: int = 0


def easel_home(project: Project, given: Path | None) -> Path:
    """The easel instance folder: --easel-home, else [sources] easel_home, else $EASEL_HOME."""
    if given is not None:
        return Path(given).expanduser().resolve()
    configured = str(project.instance.cfg.get("sources.easel_home", "") or "").strip()
    if configured:
        path = Path(configured).expanduser()
        return (path if path.is_absolute() else project.instance.root / path).resolve()
    env = os.environ.get("EASEL_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve()
    raise ProjectError(2, ["no easel instance named: pass --easel-home <dir>",
                           "or set it once: kiln config set sources.easel_home <dir>"])


def current_verdicts(reviews: Path) -> dict[int, dict]:
    """output n -> the operator's latest review line (append order); other sources never count."""
    out: dict[int, dict] = {}
    for line in store.read_jsonl(reviews).records:
        if line.get("source") != "operator" or not isinstance(line.get("n"), int):
            continue
        out[int(line["n"])] = line
    return out


def load_run(home: Path, run_id: str) -> EaselRun:
    if not base.RUN_ID.match(run_id or ""):
        raise ProjectError(2, f"an easel run id looks like run-YYYYMMDD-NNN: {run_id!r}")
    folder = home / "runs" / run_id
    run_file = folder / "run.json"
    if not run_file.is_file():
        raise ProjectError(2, [f"no easel run {run_id}: <easel_home>/runs/{run_id}/run.json is missing",
                               "check the run id (`easel runs list`) and --easel-home"])
    try:
        record = store.read_record(run_file, "easel/run/")
    except (OSError, ValueError) as exc:  # the message would name the absolute path: say what is wrong instead
        raise ProjectError(2, f"<easel_home>/runs/{run_id}/run.json is not a readable easel run record "
                              "(schema easel/run/<n>)") from exc
    recipe: dict = {}
    rcp = record.get("recipe")
    if isinstance(rcp, str) and base.RCP_ID.match(rcp) and (home / "recipes" / f"{rcp}.json").is_file():
        try:
            recipe = store.read_record(home / "recipes" / f"{rcp}.json")
        except (OSError, ValueError):
            recipe = {}
    return EaselRun(run_id=run_id, folder=folder, record=record,
                    verdicts=current_verdicts(folder / "reviews.jsonl"), recipe=recipe)


def _legacy_root(project: Project, home: Path) -> Path | None:
    try:
        return project.root_dir("legacy_output")
    except ProjectError:
        pass
    toml = home / "easel.toml"
    if not toml.is_file():
        return None
    try:
        value = ((tomllib.loads(toml.read_text(encoding="utf-8")).get("adopt") or {}).get("output_root") or "")
    except (OSError, ValueError):
        return None
    value = os.path.expandvars(str(value)).strip()
    if not value or "$" in value:
        return None
    path = Path(value).expanduser()
    return path if path.is_absolute() else home / path


def locate_output(project: Project, home: Path, run: EaselRun, output: dict) -> Path | None:
    """The file of one output: in the run folder, else (a legacy import) in the host's run folder."""
    own = run.folder / str(output["file"])
    if own.is_file():
        return own
    imported = run.record.get("imported_from") or {}
    if not imported.get("manifest"):
        return None
    root = _legacy_root(project, home)
    if root is None:
        return None
    folder = root / Path(str(imported["manifest"])).parent
    if not folder.is_dir():
        return None
    named = folder / Path(str(output["file"])).name
    if named.is_file():
        return named
    want = output.get("sha256")
    if not want:
        return None
    for path in image_meta.iter_images(folder):
        if hashing.sha256_file(path) == want:
            return path
    return None


def _labels(checks: object) -> list[str]:
    out = []
    for key, value in sorted((checks or {}).items()) if isinstance(checks, dict) else []:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", str(key)):
            continue
        if value == "pass":
            out.append(f"check:{key}")
        elif value == "fail":
            out.append(f"check-fail:{key}")
        elif value == "na":
            out.append(f"check-na:{key}")
    return out


def import_run(project: Project, run_id: str, *, home: Path | None = None, keep_only: bool = True) -> Imported:
    """Copy and register the kept outputs of `run_id`; see the module docstring for every field."""
    if not keep_only:
        raise ProjectError(2, ["only --keep-only imports are supported: kept outputs become ACCEPT_TRAIN",
                               "rejected and undecided outputs stay in easel"])
    folder_home = easel_home(project, home)
    run = load_run(folder_home, run_id)
    result = Imported()
    for n in (int(o["n"]) for o in run.record.get("outputs") or [] if isinstance(o.get("n"), int)):
        verdict = (run.verdicts.get(n) or {}).get("verdict")
        result.kept += verdict == KEEP
        result.rejected += verdict == "reject"
        result.undecided += verdict not in (KEEP, "reject")
    with project.lock():  # the known images are read once; no other session registers in between
        _import_kept(project, folder_home, run, run_id, result)
    return result


def _import_kept(project: Project, folder_home: Path, run: EaselRun, run_id: str, result: Imported) -> None:
    known = images_mod.records(project)
    target = base.imports_dir(project) / run_id
    rating = "sfw" if run.recipe.get("content_class") == "sfw" else "unknown"
    recipe = run.record.get("recipe") if base.RCP_ID.match(str(run.record.get("recipe") or "")) else None
    by = f"kiln images import --from-easel-run {run_id} --keep-only"
    for output in run.kept():
        n = int(output["n"])
        path = locate_output(project, folder_home, run, output)
        if path is None:
            result.problems.append(f"output {n}: its file is missing (<easel_home>/runs/{run_id}/{output['file']}); "
                                   "not imported")
            continue
        data = path.read_bytes()
        sha = base.sha_of(data)
        if output.get("sha256") and sha != output["sha256"]:
            result.problems.append(f"output {n}: sha256 mismatch (the file changed after the run was recorded); "
                                   "not imported")
            continue
        record = base.known_image(known, sha)
        if record is not None:
            result.already.append((n, record))
            continue
        dest = target / f"{n:03d}{path.suffix.lower() or '.png'}"
        try:
            base.place(project, data, dest)
        except ProjectError as exc:
            result.problems += [f"output {n}: {line}" for line in exc.lines]
            continue
        review = run.verdicts.get(n) or {}
        source = {"kind": "generated", "tool": "easel", "run": run_id, "n": n, "recipe": recipe,
                  "prompt": run.prompt or None}
        line, created = images_mod.register(
            project, dest, source=source, by=by, event="import", tier="CANDIDATE", content_rating=rating,
            labels=_labels(review.get("checks")), disposition="ACCEPT_TRAIN", split="unassigned",
            verdict_source=f"easel-review:{run_id}", caption_basis=run.prompt or None,
            notes=review.get("note") or None, images=known)
        if not created:
            result.already.append((n, line))
            continue
        known[line["id"]] = line
        result.registered.append(line)
