"""`kiln train plan ... --emit-legacy`: hand a planned run to an adopted host workspace's own training runner.

On an adopted instance the host workspace keeps training through its own tools until cutover. This module
renders the host's launcher and dataset config for the planned run and writes the host queue file, then the
command prints the two host lines the operator's go unlocks. It never starts anything and never writes the
host's registry.

Every host fact comes from the instance config's `[legacy]` table (nothing host-specific is written in this
repository): `root` (the host workspace; `--legacy-root <dir>` overrides it, for a scratch copy), `exp` (the
host's experiment name), `renderer` (the host's template renderer), `launcher_template`, `dataset_template`,
`generated_dir` (where the host keeps rendered dataset configs), `queue_dir` and `run_registry` (the host's
SQLite run registry). Relative values are relative to the legacy root. Writes go only to `generated_dir`,
`queue_dir` and the run's own `projects/<slug>/runs/<run-id>/legacy/` folder; the first two must lie inside the
legacy root, so a scratch `--legacy-root` can never write into another tree.

Steps, in order (nothing is written until every check passed):
1. read the launcher template's `${VARS}`. `--continue-from` needs a `${NETWORK_WEIGHTS}` variable (sd-scripts'
   `--network_weights`); a template without one prints `continue-from: unsupported by the host template until
   cutover` and exits 2;
2. propose the host run id: the registry's run ids (`training_run.run_id`, opened read-only with
   `file:...?mode=ro`) read as `<prefix><number>`; the next number of the most used prefix, at the same width,
   also above any `<prefix><number>.*` file in `generated_dir`. `--legacy-run-id <id>` overrides; a taken id is
   refused;
3. render the launcher into the run's `legacy/` folder and the dataset config into `generated_dir` by running the
   host renderer as a subprocess (`<renderer> <template> --set KEY=VALUE ... --out <file>`, cwd = the legacy
   root; a `.py` renderer runs with this interpreter). Only the template's own variables are passed, so a
   variable kiln does not know makes the renderer exit 2 (missing) and a literal absolute path in a template
   makes it exit 3; both codes are passed through unchanged. `DATASET_ROOT` is the kiln materialised folder
   (`projects/<slug>/datasets/<version>`) relative to the legacy root; `OUTPUT_DIR` and `LOGGING_DIR` are
   `<experiment folder>/output/<id>` and `/logs/<id>`, the experiment folder being the ancestor of the launcher
   template named `[legacy] exp`;
4. write `queue-<id>.json` in the host queue tool's shape (`experiment`, `min_free_ram_gb_train`,
   `min_free_ram_gb_eval`, `max_attempts`, `jobs[{run, launcher, steps, eval{family, base, steps[, suite]}}]`)
   and validate it with `check_queue`;
5. return the host lines: the run-registry line `run add <exp> --run-id <id> --base-model ... --config <dataset
   config> --steps N --output-dir ... [--eval-suite ...] --notes "dataset: kiln <slug>/<version>
   members_sha256=<sha>"`, **never with `--snapshot`** (the host registry's foreign key would reject a
   kiln-only snapshot; the notes carry it until cutover), and the queue line `run <queue file>`.
If a render fails, the files this call wrote are removed again and nothing is planned.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import config, hashing, store

from ..project import materialize as materialize_mod
from ..project.model import Instance, Project, ProjectError
from ..project.model import why as read_reason  # render() has a local `why`
from .plan import Prepared, models_root
from .runs import LEGACY_RELATIVE

KEYS = ("root", "exp", "renderer", "launcher_template", "dataset_template", "generated_dir", "queue_dir",
        "run_registry")
VAR = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")  # `$${X}` is the host renderer's escaped literal
WEIGHTS_VAR = "NETWORK_WEIGHTS"
UNSUPPORTED = "continue-from: unsupported by the host template until cutover"
REGISTRY_TABLE, REGISTRY_COLUMN = "training_run", "run_id"
ID_SHAPE = re.compile(r"^(?P<prefix>[A-Za-z][A-Za-z_-]*?)(?P<num>\d+)$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
MAX_ATTEMPTS = 8
QUEUE_KEYS = ("experiment", "min_free_ram_gb_train", "min_free_ram_gb_eval", "max_attempts", "jobs")
JOB_KEYS = ("run", "launcher", "steps", "eval")
EVAL_KEYS = ("family", "base", "steps")
SAMPLES_EVAL_KEYS = ("prompts_file", "base")
RENDER_TIMEOUT_S = 300


@dataclass
class Legacy:
    """The host workspace as `[legacy]` describes it, every path resolved."""

    root: Path
    exp: str
    renderer: Path
    launcher_template: Path
    dataset_template: Path
    generated_dir: Path
    queue_dir: Path
    run_registry: Path
    exp_dir: Path

    def rel(self, path: Path) -> str | None:
        """`path` relative to the legacy root (forward slashes), or None when it lies outside."""
        try:
            return Path(path).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return None

    def host_path(self, path: Path) -> str:
        """A path as the host's tools read it: relative to the legacy root when inside, else absolute."""
        return self.rel(path) or Path(path).resolve().as_posix()


def _inside(path: Path, root: Path) -> bool:
    path = Path(path).resolve()
    return path == root or root in path.parents


def load_legacy(cfg: config.Config, override_root: Path | str | None = None) -> Legacy:
    """`[legacy]` resolved against the legacy root (`--legacy-root` wins over `[legacy] root`); exit 2 when a key
    is missing or a write target lies outside the root."""
    raw = cfg.get("legacy", {}) or {}
    raw = raw if isinstance(raw, dict) else {}
    need = [k for k in KEYS if not (override_root is not None and k == "root")]
    missing = [k for k in need if not str(raw.get(k, "") or "").strip()]
    if missing:
        raise ProjectError(2, [f"--emit-legacy needs [legacy] {', '.join(missing)} in kiln.toml; every host fact "
                               "comes from there", "set each one: kiln config set legacy.<key> <value>"])
    if override_root is not None:
        root = Path(override_root).expanduser().resolve()
    else:
        root = Instance._path(cfg.root or Path.cwd(), raw["root"])
    if not root.is_dir():
        raise ProjectError(2, f"the legacy root does not exist: {root.name} ([legacy] root or --legacy-root)")

    def at(key: str) -> Path:
        return Instance._path(root, raw[key])

    paths = {k: at(k) for k in KEYS if k not in ("root", "exp")}
    for key in ("generated_dir", "queue_dir"):
        if not _inside(paths[key], root):
            raise ProjectError(2, [f"REFUSED: [legacy] {key} lies outside the legacy root; kiln writes only inside it",
                                   "point it inside the root, or pass --legacy-root <the folder that holds it>"])
    for key in ("renderer", "launcher_template", "dataset_template"):
        if not paths[key].is_file():
            raise ProjectError(2, f"[legacy] {key} names no file ({paths[key].name} under the legacy root)")
    exp = str(raw["exp"]).strip()
    exp_dir = next((d for d in (*paths["launcher_template"].parents, paths["queue_dir"], *paths["queue_dir"].parents)
                    if d.name == exp and _inside(d, root)), None)
    if exp_dir is None:
        raise ProjectError(2, [f"no folder named {exp!r} ([legacy] exp) above [legacy] launcher_template",
                               "the experiment folder holds the host's output/ and logs/ for each run"])
    return Legacy(root=root, exp=exp, exp_dir=exp_dir, **paths)


def template_variables(path: Path) -> list[str]:
    """The `${VARS}` a template needs, sorted (escaped `$${X}` excluded)."""
    return sorted(set(VAR.findall(Path(path).read_text(encoding="utf-8-sig"))))


# --- the host run id -----------------------------------------------------------------------------------------

def registry_ids(path: Path) -> list[str]:
    """Every run id in the host registry, read with `mode=ro` (never a write, never a PRAGMA)."""
    path = Path(path)
    if not path.is_file():
        raise ProjectError(2, f"the host run registry does not exist ([legacy] run_registry: {path.name})")
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            rows = conn.execute(f"SELECT {REGISTRY_COLUMN} FROM {REGISTRY_TABLE}").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise ProjectError(2, [f"the host run registry cannot be read (mode=ro): {exc}",
                               "pass --legacy-run-id <id> only if the operator names the id"]) from exc
    return [str(r[0]) for r in rows if r[0] is not None]


def _generated_names(legacy: Legacy) -> list[str]:
    folder = legacy.generated_dir
    return sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []


def propose_id(ids: list[str], generated: list[str]) -> str:
    """The next free id after the registry's main `<prefix><number>` series (see the module docstring)."""
    parsed = [m for m in map(ID_SHAPE.match, ids) if m]
    if not parsed:
        raise ProjectError(2, ["the host registry holds no run id of the form <prefix><number> to count from",
                               "pass --legacy-run-id <id> (the operator chooses it)"])
    counts = Counter(m.group("prefix") for m in parsed)
    prefix = max(counts, key=lambda p: (counts[p], max(int(m.group("num")) for m in parsed if m.group("prefix") == p)))
    numbers = [m.group("num") for m in parsed if m.group("prefix") == prefix]
    width = max(len(n) for n in numbers)
    taken = {int(n) for n in numbers}
    for name in generated:
        match = ID_SHAPE.match(name.split(".", 1)[0])
        if match and match.group("prefix") == prefix:
            taken.add(int(match.group("num")))
    return f"{prefix}{max(taken) + 1:0{width}d}"


# --- values and rendering ------------------------------------------------------------------------------------

def intake_flip(project: Project) -> str | None:
    """`true`/`false` from the intake's `symmetric` answer (the dataset template's FLIP_AUG), or None."""
    name = project.data.get("intake")
    if not name or not (project.folder / str(name)).is_file():
        return None
    try:
        import yaml

        data = yaml.safe_load((project.folder / str(name)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if node.get("id") == "symmetric" and str(node.get("answer")).lower() in ("yes", "no", "true", "false"):
                return "true" if str(node["answer"]).lower() in ("yes", "true") else "false"
            stack += list(node.values())
        elif isinstance(node, list):
            stack += node
    return None


def values_for(legacy: Legacy, *, run_id: str, version: str, dataset_root: Path, base_file: Path, steps: int,
               save_every: int | None, validate_every: int | None, rank: object = None, alpha: object = None,
               lr: object = None, flip_aug: str | None = None, network_weights: Path | None = None) -> dict[str, str]:
    """Every variable kiln can supply to a host template, as the strings the renderer substitutes."""
    values = {
        "RUN_ID": run_id,
        "DATASET_ROOT": legacy.host_path(dataset_root),
        "DATASET_VERSION": version,
        "OUTPUT_DIR": legacy.host_path(legacy.exp_dir / "output" / run_id),
        "LOGGING_DIR": legacy.host_path(legacy.exp_dir / "logs" / run_id),
        "BASE_CHECKPOINT": legacy.host_path(base_file),
        "MAX_TRAIN_STEPS": str(steps),
        WEIGHTS_VAR: legacy.host_path(network_weights) if network_weights is not None else "",
    }
    for key, value in (("SAVE_EVERY", save_every), ("VALIDATE_EVERY", validate_every), ("RANK", rank),
                       ("ALPHA", alpha), ("LR", lr), ("FLIP_AUG", flip_aug)):
        if value is not None:
            values[key] = str(value)
    return values


def render(legacy: Legacy, template: Path, values: dict[str, str], out: Path) -> int:
    """Render `template` into `out` through the host renderer; returns the number of variables substituted.

    Exit codes 2 (a missing variable or a usage error) and 3 (a literal absolute path in the template) come back
    unchanged as ProjectError codes, with the renderer's last lines; any other failure is exit 1.
    """
    needed = template_variables(template)
    passed = {k: values[k] for k in needed if k in values}
    cmd = [sys.executable, str(legacy.renderer)] if legacy.renderer.suffix.lower() == ".py" else [str(legacy.renderer)]
    cmd.append(str(template))
    for key, value in passed.items():
        cmd += ["--set", f"{key}={value}"]
    cmd += ["--out", str(out)]
    # The host renderer also reads variables from the environment; drop the ones kiln does not supply so a
    # missing value fails the same way on every machine.
    env = {k: v for k, v in os.environ.items() if not (k in needed and k not in passed)}
    try:
        done = subprocess.run(cmd, cwd=legacy.root, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=RENDER_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired as exc:
        raise ProjectError(1, f"the host renderer did not finish on {template.name} within {RENDER_TIMEOUT_S} s") from exc
    except OSError as exc:
        raise ProjectError(1, f"the host renderer could not run on {template.name}: {read_reason(exc)}") from exc
    if done.returncode != 0:
        tail = [line for line in (done.stdout + done.stderr).splitlines() if line.strip()][-20:]
        why = {2: "a variable is missing or the call was wrong", 3: "a literal absolute path in the template"}
        code = done.returncode if done.returncode in (2, 3) else 1
        raise ProjectError(code, [f"the host renderer exited {done.returncode} on {template.name}: "
                                  f"{why.get(done.returncode, 'see its output')}; nothing was planned",
                                  *("  " + line for line in tail)])
    if not Path(out).is_file():
        raise ProjectError(1, f"the host renderer exited 0 but wrote no {Path(out).name}")
    return len(passed)


# --- the queue -----------------------------------------------------------------------------------------------

def eval_steps(steps: int, save_every: int | None) -> str:
    """The checkpoint steps the host's eval grid renders: every save step, and the last."""
    if not save_every:
        return str(steps)
    marks = list(range(save_every, steps + 1, save_every))
    if not marks or marks[-1] != steps:
        marks.append(steps)
    return ",".join(str(m) for m in marks)


def queue_record(legacy: Legacy, prepared: Prepared, *, run_id: str, launcher: str, eval_base: str,
                 kiln_run: str) -> dict:
    ram = prepared.host_ram()
    job_eval = {"family": prepared.family_id, "base": eval_base,
                "steps": eval_steps(prepared.steps, prepared.save_every)}
    if prepared.eval_suite:
        job_eval["suite"] = prepared.eval_suite
    return {
        "_comment": f"written by kiln train plan --emit-legacy for {kiln_run} ({prepared.snapshot_id}); "
                    "start it only on the operator's go",
        "experiment": legacy.exp,
        "min_free_ram_gb_train": ram.get("train"),
        "min_free_ram_gb_eval": ram.get("eval"),
        "max_attempts": MAX_ATTEMPTS,
        "jobs": [{"run": run_id, "launcher": launcher, "steps": prepared.steps, "eval": job_eval}],
    }


def _number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def check_queue(path: Path, root: Path, run_id: str | None = None) -> tuple[list[str], int]:
    """Problems of a host queue file (keys, types, launchers present under `root`, eval keys), and its job count.

    The private check of an adopted instance also runs the host queue tool's own ordering; this is the part
    that needs no host code.
    """
    path, root = Path(path), Path(root).resolve()
    try:
        queue = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{path.name} does not load as JSON: {read_reason(exc, path)}"], 0
    if not isinstance(queue, dict):
        return [f"{path.name}: the top level is not an object"], 0
    problems = []
    kinds = {"experiment": lambda v: isinstance(v, str) and bool(v), "min_free_ram_gb_train": _number,
             "min_free_ram_gb_eval": _number, "max_attempts": lambda v: isinstance(v, int) and v >= 1,
             "jobs": lambda v: isinstance(v, list) and bool(v)}
    for key in QUEUE_KEYS:
        if key not in queue:
            problems.append(f"missing key {key}")
        elif not kinds[key](queue[key]):
            problems.append(f"{key} has the wrong type or is empty")
    jobs = queue.get("jobs") if isinstance(queue.get("jobs"), list) else []
    seen: set[str] = set()
    for n, job in enumerate(jobs, 1):
        if not isinstance(job, dict):
            problems.append(f"job {n} is not an object")
            continue
        problems += [f"job {n}: missing key {k}" for k in JOB_KEYS if k not in job]
        run = str(job.get("run"))
        if run in seen:
            problems.append(f"job {n}: run {run} appears twice")
        seen.add(run)
        launcher = job.get("launcher")
        if isinstance(launcher, str) and launcher:
            rel = Path(launcher)
            if rel.is_absolute() or ".." in rel.parts or ":" in launcher:
                problems.append(f"job {n}: the launcher must be relative to the host root")
            elif not (root / rel).is_file():
                problems.append(f"job {n}: launcher missing ({rel.name})")
        if not (isinstance(job.get("steps"), int) and job["steps"] >= 1):
            problems.append(f"job {n}: steps must be a whole number of steps")
        ev = job.get("eval")
        if isinstance(ev, dict):
            keys = SAMPLES_EVAL_KEYS if ev.get("from") == "samples" else EVAL_KEYS
            problems += [f"job {n}: eval is missing {k}" for k in keys if not ev.get(k)]
        elif "eval" in job:
            problems.append(f"job {n}: eval is not an object")
    if run_id is not None and run_id not in seen:
        problems.append(f"no job for run {run_id}")
    return problems, len(jobs)


# --- the whole emission ----------------------------------------------------------------------------------------

def quote(value: str) -> str:
    """A shell word: double-quoted when it holds a space or is empty."""
    return f'"{value}"' if not value or re.search(r"\s", value) else value


@dataclass
class Checked:
    """What `precheck` established before anything is written."""

    legacy: Legacy
    run_id: str
    proposed: bool
    launcher_vars: list[str]
    dataset_root: Path
    base_file: Path
    continue_file: Path | None
    launcher_name: str
    dataset_config: Path
    queue_file: Path


def precheck(prepared: Prepared, cfg: config.Config, *, legacy_root: Path | str | None = None,
             legacy_run_id: str | None = None) -> Checked:
    """Every refusal of `--emit-legacy`, before a file is touched (exit 2 with the reason)."""
    project = prepared.project
    legacy = load_legacy(cfg, legacy_root)
    variables = template_variables(legacy.launcher_template)
    if prepared.continue_from is not None and WEIGHTS_VAR not in variables:
        raise ProjectError(2, [UNSUPPORTED,
                               "  the host launcher template has no ${" + WEIGHTS_VAR + "} variable; the operator "
                               "chooses: a fresh run from the base (plan again without --continue-from), or wait "
                               "for the cutover"])
    continue_file = None
    if prepared.continue_from is not None:
        continue_file = prepared.continue_from.file
        if continue_file is None:
            raise ProjectError(2, "the LoRA to continue from cannot be placed on this machine; pass its file "
                                  "(--continue-from <path>)")
    if prepared.host_ram().get("train") is None or prepared.host_ram().get("eval") is None:
        raise ProjectError(2, f"families/{prepared.family_id}.yaml has no host_ram_gb train and eval numbers; the "
                              "host queue waits for both")
    inst = project.instance
    if not _inside(inst.root, legacy.root):
        raise ProjectError(2, ["REFUSED: the kiln instance lies outside the legacy root, so the host tools cannot "
                               "reach its launcher and dataset folder", "keep the instance inside the host workspace"])
    folder = materialize_mod.check_folder(project, prepared.version)
    if not folder.found:
        raise ProjectError(2, [f"REFUSED: {prepared.snapshot_id} is not materialised; the host launcher trains on "
                               f"{project.shown(folder.base)}", f"next: kiln dataset materialize {prepared.version}"])
    if folder.foreign:
        raise ProjectError(2, [f"REFUSED: {prepared.snapshot_id} was materialised outside kiln; the host launcher "
                               "trains only on a kiln copy",
                               f"next: kiln dataset materialize {prepared.version} --into <dir>"])
    if folder.problems:
        raise ProjectError(2, [f"REFUSED: {project.shown(folder.base)} does not match its snapshot: "
                               f"{folder.problems[0]}", f"next: kiln dataset check {prepared.version}"])
    base_file = prepared.base.file
    if base_file is None or not base_file.is_file():
        raise ProjectError(2, [f"the base checkpoint file of {prepared.base.block['label']} is not under "
                               "models_root (checkpoints/ or the top)", "pass its path: --base <path>"])
    ids = registry_ids(legacy.run_registry)
    generated = _generated_names(legacy)
    if legacy_run_id:
        if not SAFE_ID.match(legacy_run_id):
            raise ProjectError(2, f"--legacy-run-id {legacy_run_id!r}: letters, digits, '.', '_' and '-' only")
        run_id, proposed = legacy_run_id, False
    else:
        run_id, proposed = propose_id(ids, generated), True
    if run_id in ids:
        raise ProjectError(2, [f"REFUSED: {run_id} is already in the host registry",
                               "plan again for the next free id, or --legacy-run-id <id> from the operator"])
    if any(name.split(".", 1)[0] == run_id for name in generated):
        raise ProjectError(2, f"REFUSED: the host's generated folder already holds files for {run_id}")
    dataset_config = legacy.generated_dir / f"{run_id}.dataset{legacy.dataset_template.suffix}"
    queue_file = legacy.queue_dir / f"queue-{run_id}.json"
    if queue_file.exists():
        raise ProjectError(2, f"REFUSED: {queue_file.name} exists in the host queue folder")
    return Checked(legacy=legacy, run_id=run_id, proposed=proposed, launcher_vars=variables,
                   dataset_root=folder.base, base_file=base_file, continue_file=continue_file,
                   launcher_name=f"{run_id}{legacy.launcher_template.suffix}", dataset_config=dataset_config,
                   queue_file=queue_file)


@dataclass
class Emitted:
    """What the emitter wrote: the run record's legacy parts and the lines to print."""

    run_id: str
    record: dict
    lines: list[str] = field(default_factory=list)
    registry_line: str = ""
    queue_line: str = ""
    written: list[Path] = field(default_factory=list)


def undo(emitted: Emitted, run_dir: Path) -> None:
    """Remove what `emit` wrote, when the run record could not be written after it."""
    _remove(emitted.written, [run_dir / "legacy", run_dir])


def _eval_base(prepared: Prepared, base_file: Path) -> str:
    root = models_root(prepared.project.instance)
    for folder in ((root / "checkpoints") if root else None, root):
        if folder is None:
            continue
        try:
            return base_file.resolve().relative_to(folder.resolve()).as_posix()
        except ValueError:
            continue
    return str(prepared.base.block["label"])


def _remove(paths: list[Path], folders: list[Path]) -> None:
    """Undo this call's own writes after a failed render (only files it created, only folders left empty)."""
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass
    for folder in folders:
        try:
            folder.rmdir()
        except OSError:
            pass


def emit(prepared: Prepared, checked: Checked, *, kiln_run: str, run_dir: Path) -> Emitted:
    """Render, write the queue and build the host lines (after `precheck`; `run_dir` is the claimed folder)."""
    legacy, run_id = checked.legacy, checked.run_id
    training = prepared.family.get("training") or {}
    network, optimizer = training.get("network") or {}, training.get("optimizer") or {}
    values = values_for(legacy, run_id=run_id, version=prepared.version, dataset_root=checked.dataset_root,
                        base_file=checked.base_file, steps=prepared.steps, save_every=prepared.save_every,
                        validate_every=prepared.validate_every, rank=network.get("dim"), alpha=network.get("alpha"),
                        lr=optimizer.get("learning_rate"), flip_aug=intake_flip(prepared.project),
                        network_weights=checked.continue_file)
    launcher = run_dir / "legacy" / checked.launcher_name
    written: list[Path] = []
    try:
        launcher.parent.mkdir(parents=True, exist_ok=True)
        n_launcher = render(legacy, legacy.launcher_template, values, launcher)
        written.append(launcher)
        render(legacy, legacy.dataset_template, values, checked.dataset_config)
        written.append(checked.dataset_config)
        launcher_rel = legacy.rel(launcher)
        eval_base = _eval_base(prepared, checked.base_file)
        queue = queue_record(legacy, prepared, run_id=run_id, launcher=launcher_rel, eval_base=eval_base,
                             kiln_run=kiln_run)
        store.write_json(checked.queue_file, queue)
        written.append(checked.queue_file)
        bad, n_jobs = check_queue(checked.queue_file, legacy.root, run_id)
        if bad:
            raise ProjectError(1, ["the written queue does not pass check_queue (a bug in kiln):",
                                   *("  " + b for b in bad)])
    except BaseException:
        _remove(written, [launcher.parent, run_dir])
        raise
    config_rel = legacy.rel(checked.dataset_config)
    queue_rel = legacy.rel(checked.queue_file)
    output_rel = values["OUTPUT_DIR"]
    snap = prepared.snapshot
    words = ["run", "add", quote(legacy.exp), "--run-id", run_id, "--base-model", quote(eval_base),
             "--config", quote(config_rel), "--steps", str(prepared.steps), "--output-dir", quote(output_rel)]
    if prepared.eval_suite:
        words += ["--eval-suite", quote(prepared.eval_suite)]
    words += ["--notes", f'"dataset: kiln {prepared.snapshot_id} members_sha256={snap["members_sha256"]}"']
    registry_line = " ".join(words)
    queue_line = f"run {quote(queue_rel)}"
    block = {"paths_relative_to": LEGACY_RELATIVE, "run_id": run_id, "experiment": legacy.exp,
             "launcher": launcher_rel, "dataset_config": config_rel, "queue": queue_rel,
             "registry_line": registry_line, "queue_line": queue_line}
    record = {"run_id": run_id, "block": block, "output_dir": output_rel,
              "config": {"path": config_rel, "sha256": hashing.sha256_file(checked.dataset_config)}}
    how = "next free in the host registry" if checked.proposed else "--legacy-run-id"
    lines = [f"legacy run id: {run_id} ({how})",
             f"  launcher   {prepared.project.shown(launcher)} ({n_launcher} variables substituted)",
             f"  dataset    {config_rel}",
             f"  queue      {queue_rel} ({n_jobs} job; validated by check_queue)"]
    if checked.continue_file is not None:
        lines.append(f"  continue   ${{{WEIGHTS_VAR}}} = {values[WEIGHTS_VAR]}")
    lines += ["host run-registry line (no --snapshot: the notes carry the kiln snapshot until cutover):",
              registry_line,
              "host queue line (trains, then renders the evals; the operator's go first, never run by you):",
              queue_line]
    return Emitted(run_id=run_id, record=record, lines=lines, registry_line=registry_line, queue_line=queue_line,
                   written=written)
