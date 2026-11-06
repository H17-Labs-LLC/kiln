"""`kiln train plan <version> --family <f> (--base <sha16|path> | --base-from-run <run-id>) --steps N`.

What it does: works out everything a training run needs and writes `runs/<run-id>/run.json` with status
`planned`. It starts nothing. The record names the frozen dataset version (`<slug>/<version>`), the family, the
base model with **its licence block copied in** (a release build later reads it), the LoRA the run continues
from (`continue_from`, or null for a fresh run from the base), the trainer recipe from the family file, the
steps, and a wall-clock estimate from the family's measured numbers.

Where the base comes from:
- `--base-from-run <run-id>`: the base block of an earlier run of this project, licence included, with
  `from_run` set (the usual route: `kiln runs list --family <f> --status ok` first);
- `--base <path>`: a checkpoint file; its sha256 is computed and its resource record (in the easel registry
  named by `[sources] easel_home`) gives the label and licence; without a record the licence is `unknown`;
- `--base <sha16>`: a resource id, looked up in that registry, then in this project's runs and project.yaml.
A base nobody knows is refused: kiln never invents a licence.

`--continue-from <lora sha16|path>` records the starting weights: a step checkpoint of one of this project's runs
(by sha16), a resource in the easel registry, or a file. On the native path it becomes sd-scripts'
`--network_weights` (see `native_args`); on an adopted instance `legacy_emit` maps it only when the host's
launcher template has a variable for it.
"""

from __future__ import annotations

import datetime as _dt
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from kiln import families_check
from kiln._common import hashing, store

from ..project import snapshot as snapshot_mod
from ..project.model import Instance, Project, ProjectError, check_version, now
from . import runs as runs_mod

SCHEMA = "kiln/run/1"
SHA16 = re.compile(r"^[0-9a-f]{16}$")
LICENCE_KEYS = ("status", "name", "spdx", "url", "commercial_use", "credit_required", "derivatives_allowed",
                "relicense_allowed", "redistributable", "gated", "source", "checked_at", "note")
LICENCE_STATUSES = ("verified", "unverified", "unknown")
BASE_KEYS = ("resource", "sha256", "label", "file_name", "revision", "from_run", "licence")
ALGOS = {"networks.lora": "lora"}
UNKNOWN_LICENCE_NOTE = "no licence recorded for this base; read its model card before a release"


@dataclass
class Located:
    """A model file as the run record names it (`block`) and where it lies on this machine (`file`, or None)."""

    block: dict
    file: Path | None
    found_by: str


@dataclass
class Prepared:
    """Everything `train plan` resolved before it writes: no file has been touched yet."""

    project: Project
    version: str
    snapshot: dict
    family_id: str
    family: dict
    base: Located
    continue_from: Located | None
    steps: int
    save_every: int | None
    validate_every: int | None
    eval_suite: str | None
    label: str | None
    note: str | None
    estimate: dict | None
    warnings: list[str] = field(default_factory=list)

    @property
    def snapshot_id(self) -> str:
        return f"{self.project.slug}/{self.version}"

    def host_ram(self) -> dict:
        return self.family.get("host_ram_gb") or {}


# --- families ---------------------------------------------------------------------------------------------------

def families_dir() -> Path:
    return families_check.REPO / "families"


def load_families() -> dict[str, dict]:
    return families_check.load_families(families_dir(), families_check.Report())


def chain(fid: str, families: dict[str, dict]) -> list[str]:
    """`fid` and its ancestors, nearest first (stops at a missing parent or a loop)."""
    out: list[str] = []
    current: object = fid
    while isinstance(current, str) and current in families and current not in out:
        out.append(current)
        current = families[current].get("inherits")
    return out


def load_family(fid: str) -> tuple[dict, dict[str, dict]]:
    """The family after inheritance, and every family file; exit 2 when it is unknown or does not train."""
    families = load_families()
    if fid not in families:
        raise ProjectError(2, [f"no family {fid!r} (families/: {', '.join(sorted(families)) or 'none'})",
                               "`kiln families check` lists the families and their status"])
    resolved = families_check.resolve(fid, families, {})
    if resolved is None:
        raise ProjectError(2, f"families/{fid}.yaml: its inherits chain is broken (`kiln families check`)")
    training = resolved.get("training") or {}
    if training.get("supported") is not True:
        raise ProjectError(2, [f"REFUSED: family {fid} has no training recipe kiln supports "
                               "(training.supported is not true in its family file)",
                               "kiln v0.1 trains the SDXL-based families; `kiln families check` shows each status"])
    return resolved, families


def estimate(fid: str, families: dict[str, dict], steps: int) -> dict | None:
    """Wall clock from the nearest `training.measured_8gb` in the family chain; None when nothing is measured."""
    for owner in chain(fid, families):
        measured = (families[owner].get("training") or {}).get("measured_8gb")
        if not isinstance(measured, dict):
            continue
        per_step = measured.get("seconds_per_step")
        load_min = measured.get("load_and_cache_minutes") or 0
        if not isinstance(per_step, int | float) or isinstance(per_step, bool):
            return None
        minutes = round(load_min + steps * per_step / 60)
        # The measurement's date and machine stay in the family file: printed output carries no real dates.
        where = f"families/{owner}.yaml" + (f" (inherited by {fid})" if owner != fid else "")
        basis = f"{where} measured_8gb: {per_step} s per step plus {load_min} min load"
        return {"wall_clock_minutes": minutes, "basis": basis}
    return None


# --- licences and resource records -------------------------------------------------------------------------------

def licence_block(raw: object) -> dict:
    """A run-schema licence block from a resource's or run's licence; `unknown` when there is none."""
    if not isinstance(raw, dict) or not raw:
        return {"status": "unknown", "source": None, "note": UNKNOWN_LICENCE_NOTE}
    out = {}
    for key in LICENCE_KEYS:
        if key in raw:
            value = raw[key]
            out[key] = value.isoformat() if isinstance(value, _dt.date) else value
    if out.get("status") not in LICENCE_STATUSES:
        out["status"] = "unknown"
    return out


def easel_registry(instance: Instance) -> Path | None:
    """`<easel_home>/<registry>` when `[sources] easel_home` names an easel instance, else None."""
    home = str(instance.cfg.get("sources.easel_home", "") or "").strip()
    if not home:
        return None
    root = Instance._path(instance.root, home)
    registry = "registry"
    toml = root / "easel.toml"
    if toml.is_file():
        try:
            paths = tomllib.loads(toml.read_text(encoding="utf-8-sig")).get("paths") or {}
        except (OSError, tomllib.TOMLDecodeError):
            paths = {}
        base = paths.get("root", ".")
        value = paths.get("registry", "registry")
        if isinstance(base, str) and "${" not in base:
            root = Instance._path(toml.parent, base)
        if isinstance(value, str) and "${" not in value:
            registry = value
    return Instance._path(root, registry)


def easel_resource(instance: Instance, sha16: str) -> dict | None:
    """The easel resource record `<sha16>.yaml`, or None."""
    registry = easel_registry(instance)
    if registry is None:
        return None
    path = registry / "resources" / f"{sha16}.yaml"
    if not path.is_file():
        return None
    try:
        return store.read_record(path, "easel/resource/")
    except (OSError, ValueError):
        return None


def models_root(instance: Instance) -> Path | None:
    value = str(instance.cfg.get("paths.models_root", "") or "").strip()
    return Instance._path(instance.root, value) if value else None


def _resource_file(instance: Instance, record: dict) -> Path | None:
    rel = (record.get("identity") or {}).get("path")
    root = models_root(instance)
    return (root / str(rel)).resolve() if rel and root is not None else None


def base_from_resource(instance: Instance, record: dict, sha256: str | None = None) -> dict:
    identity = record.get("identity") or {}
    revision = (record.get("provenance") or {}).get("upstream_revision")
    return {"resource": record.get("id"), "sha256": sha256 or (record.get("integrity") or {}).get("sha256"),
            "label": str(identity.get("title") or identity.get("file_name") or record.get("id")),
            "file_name": identity.get("file_name"), "revision": None if revision is None else str(revision),
            "from_run": None, "licence": licence_block(record.get("licence"))}


def normal_base(block: dict) -> dict:
    out = {key: block.get(key) for key in BASE_KEYS}
    out["label"] = str(out["label"])
    out["licence"] = licence_block(block.get("licence"))
    return out


def find_base_file(instance: Instance, block: dict) -> Path | None:
    """The base checkpoint on this machine: its resource record's path, else the label or file name under
    `<models_root>/checkpoints/` or `<models_root>/` (an exported run's label is such a relative path)."""
    if block.get("resource"):
        record = easel_resource(instance, str(block["resource"]))
        found = _resource_file(instance, record) if record else None
        if found is not None and found.is_file():
            return found
    root = models_root(instance)
    if root is None:
        return None
    for name in (block.get("label"), block.get("file_name")):
        if not name or not Path(str(name)).suffix:
            continue
        for folder in (root / "checkpoints", root):
            candidate = (folder / str(name)).resolve()
            if candidate.is_file():
                return candidate
    return None


# --- base and continue-from ---------------------------------------------------------------------------------------

def _as_file(text: str) -> Path | None:
    path = Path(text).expanduser()
    return path.resolve() if path.is_file() else None


def resolve_base(project: Project, base: str | None, from_run: str | None) -> Located:
    """The run's base block and file (see the module docstring for the three routes)."""
    inst = project.instance
    if from_run:
        run = runs_mod.load(project, from_run)
        raw = run.get("base") or {}
        if not raw.get("label"):
            raise ProjectError(2, f"run {from_run} names no base model; pass --base <sha16|path>")
        block = normal_base(raw)
        block["from_run"] = from_run
        return Located(block, find_base_file(inst, block), f"run {from_run}")
    text = (base or "").strip()
    file = _as_file(text)
    if file is None and SHA16.match(text):
        record = easel_resource(inst, text)
        if record is not None:
            block = base_from_resource(inst, record)
            return Located(block, _resource_file(inst, record), "the easel registry")
        for run in reversed(runs_mod.read_all(project).runs):
            raw = run.get("base") or {}
            if raw.get("resource") == text and raw.get("label"):
                block = normal_base(raw)
                return Located(block, find_base_file(inst, block), f"run {run.get('id')}")
        raw = project.data.get("base") or {}
        if raw.get("resource") == text and raw.get("label"):
            block = normal_base(raw)
            return Located(block, find_base_file(inst, block), "project.yaml")
        raise ProjectError(2, [f"unknown base {text}: no resource record, run or project.yaml base has that id",
                               "pass the checkpoint's path (--base <path>) or --base-from-run <run-id>"])
    if file is None:
        raise ProjectError(2, [f"--base {text}: no such file, and not a 16-hex resource id",
                               "pass a checkpoint path, a resource id, or --base-from-run <run-id>"])
    sha = hashing.sha256_file(file)
    record = easel_resource(inst, sha[:16])
    if record is not None:
        return Located(base_from_resource(inst, record, sha), file, "the easel registry")
    block = {"resource": sha[:16], "sha256": sha, "label": file.stem, "file_name": file.name, "revision": None,
             "from_run": None, "licence": licence_block(None)}
    return Located(block, file, "a file")


def _checkpoint_by(project: Project, match) -> tuple[dict, dict] | None:
    """(run, checkpoint) of the newest run whose checkpoint satisfies `match`, or None."""
    for run in reversed(runs_mod.read_all(project).runs):
        for ckpt in run.get("checkpoints") or []:
            if match(ckpt):
                return run, ckpt
    return None


def resolve_continue(project: Project, arg: str | None, legacy_root: Path | None = None) -> Located | None:
    """The LoRA a run starts from, or None for a fresh run from the base."""
    if not arg:
        return None
    inst = project.instance
    text = arg.strip()
    file = _as_file(text)
    if file is None and SHA16.match(text):
        found = _checkpoint_by(project, lambda c: str(c.get("sha256") or "").startswith(text))
        if found is not None:
            run, ckpt = found
            block = {"resource": text, "path": ckpt.get("file"), "sha256": ckpt.get("sha256"),
                     "from_run": run.get("id"), "step": ckpt.get("step")}
            where = runs_mod.file_of(project, run, ckpt.get("file"), legacy_root)
            return Located(block, where, f"run {run.get('id')} step {ckpt.get('step')}")
        record = easel_resource(inst, text)
        if record is not None:
            where = _resource_file(inst, record)
            block = {"resource": text, "path": inst.relpath(where) if where else None,
                     "sha256": (record.get("integrity") or {}).get("sha256"), "from_run": None, "step": None}
            return Located(block, where, "the easel registry")
        raise ProjectError(2, [f"unknown LoRA {text}: no checkpoint of this project's runs and no resource record "
                               "has that id", "pass the LoRA file's path (--continue-from <path>)"])
    if file is None:
        raise ProjectError(2, [f"--continue-from {text}: no such file, and not a 16-hex id",
                               "pass a LoRA file path or the sha16 of one of this project's checkpoints"])
    sha = hashing.sha256_file(file)
    found = _checkpoint_by(project, lambda c: c.get("sha256") == sha)
    run, ckpt = found if found is not None else ({}, {})
    block = {"resource": sha[:16], "path": inst.relpath(file), "sha256": sha, "from_run": run.get("id"),
             "step": ckpt.get("step")}
    return Located(block, file, "a file")


# --- the plan ---------------------------------------------------------------------------------------------------

def default_suite(project: Project) -> str | None:
    """The project's only eval suite (`evals/suites/<id>.yaml`), or None when there are none or several."""
    folder = project.folder / "evals" / "suites"
    suites = sorted(p.stem for p in folder.glob("*.yaml")) if folder.is_dir() else []
    return suites[0] if len(suites) == 1 else None


def prepare(project: Project, version: str, *, family: str, base: str | None = None,
            base_from_run: str | None = None, continue_from: str | None = None, steps: int,
            save_every: int | None = None, validate_every: int | None = None, eval_suite: str | None = None,
            label: str | None = None, note: str | None = None, legacy_root: Path | None = None) -> Prepared:
    """Resolve a plan without writing anything; exit 2 (ProjectError) on anything that cannot be planned."""
    check_version(version)
    if bool(base) == bool(base_from_run):
        raise ProjectError(2, "pass exactly one of --base <sha16|path> and --base-from-run <run-id>")
    if steps < 1:
        raise ProjectError(2, "--steps is a number of steps, 1 or more")
    snap = snapshot_mod.load(project, version)
    resolved, families = load_family(family)
    training = resolved.get("training") or {}
    if save_every is None:
        save_every = (training.get("steps") or {}).get("save_every") or \
            (training.get("sd_scripts_args") or {}).get("save_every_n_steps")
    if validate_every is None:
        validate_every = save_every
    for flag, value in (("--save-every", save_every), ("--validate-every", validate_every)):
        if value is not None and (not isinstance(value, int) or value < 1):
            raise ProjectError(2, f"{flag} is a number of steps, 1 or more")
    located = resolve_base(project, base, base_from_run)
    starting = resolve_continue(project, continue_from, legacy_root)
    prepared = Prepared(project=project, version=version, snapshot=snap, family_id=family, family=resolved,
                        base=located, continue_from=starting, steps=steps, save_every=save_every,
                        validate_every=validate_every, eval_suite=eval_suite or default_suite(project),
                        label=label, note=note, estimate=estimate(family, families, steps))
    if located.block["licence"].get("status") == "unknown":
        prepared.warnings.append("warn: the base's licence is unknown: tell the operator now; a release build "
                                 "refuses an unknown base licence")
    return prepared


def _weight_decay(args: object) -> float | None:
    for item in args if isinstance(args, list) else [args]:
        match = re.match(r"^\s*weight_decay\s*=\s*([0-9.eE+-]+)\s*$", str(item or ""))
        if match:
            return float(match.group(1))
    return None


def record(prepared: Prepared, run_id: str, *, legacy: dict | None = None) -> dict:
    """The run.json of a planned run, keys in the stored order."""
    training = prepared.family.get("training") or {}
    network = training.get("network") or {}
    optimizer = training.get("optimizer") or {}
    resolution = training.get("resolution") or {}
    args = training.get("sd_scripts_args") or {}
    trainer = training.get("trainer") or {}
    legacy = legacy or {}
    data = {
        "schema": SCHEMA,
        "id": run_id,
        "legacy_id": legacy.get("run_id"),
        "project": prepared.project.slug,
        "label": prepared.label,
        "snapshot": prepared.snapshot_id,
        "family": prepared.family_id,
        "status": "planned",
        "base": prepared.base.block,
        "continue_from": prepared.continue_from.block if prepared.continue_from else None,
        "trainer": {"name": str(trainer.get("name") or "kohya-ss sd-scripts"), "version": trainer.get("version"),
                    "commit": trainer.get("commit")},
        "config": legacy.get("config") or {"path": None, "sha256": None},
        "adapter": {"algo": ALGOS.get(str(network.get("module")), "lora"), "target_preset": None,
                    "rank": network.get("dim"), "alpha": network.get("alpha")},
        "optimizer": {"type": optimizer.get("type"), "lr": optimizer.get("unet_lr", optimizer.get("learning_rate")),
                      "lr_text": None, "weight_decay": _weight_decay(optimizer.get("args")),
                      "scheduler": optimizer.get("lr_scheduler")},
        "precision": args.get("mixed_precision"),
        "batch_size": resolution.get("batch_size"),
        "grad_accum": resolution.get("gradient_accumulation_steps", args.get("gradient_accumulation_steps")),
        "seed": args.get("seed"),
        "text_dropout": None,
        "steps": {"planned": prepared.steps, "done": None, "save_every": prepared.save_every,
                  "validate_every": prepared.validate_every},
        "estimate": prepared.estimate,
        "planned_at": now(),
        "started_at": None,
        "finished_at": None,
        "duration_seconds": None,
        "log": None,
        "output_dir": legacy.get("output_dir"),
        "eval_suite": prepared.eval_suite,
        "selected_checkpoint": None,
        "checkpoints": [],
    }
    if legacy.get("block"):
        data["legacy"] = legacy["block"]
    data["notes"] = prepared.note
    return data


def problems(data: dict) -> list[str]:
    """Why a run record does not validate against schemas/run.schema.json (empty when it does)."""
    from kiln.handoff import schema_problems

    return schema_problems("run", data)


def write(prepared: Prepared, run_dir: Path, data: dict) -> Path:
    """Write run.json into the claimed run folder, under the project lock; refuses an invalid record."""
    bad = problems(data)
    if bad:
        raise ProjectError(2, ["REFUSED: the planned record does not match schemas/run.schema.json "
                               "(a bug in kiln; nothing was written)", *("  " + p for p in bad[:10])])
    path = run_dir / "run.json"
    with prepared.project.lock():
        store.write_record(path, SCHEMA, data)
    return path


def native_args(data: dict, family: dict, *, dataset_config: str, base_file: str, output_dir: str,
                continue_file: str | None = None) -> list[str]:
    """The sd-scripts arguments of a planned run on the native path: the family's audited flags, the run's steps,
    and `--network_weights <file>` when the run continues from a LoRA (kiln's own launcher uses this)."""
    steps = data["steps"]
    flags = dict((family.get("training") or {}).get("sd_scripts_args") or {})
    flags["max_train_steps"] = steps["planned"]
    if steps.get("save_every"):
        flags["save_every_n_steps"] = steps["save_every"]
        if "save_last_n_steps_state" in flags:
            flags["save_last_n_steps_state"] = steps["save_every"]
    if steps.get("validate_every"):
        flags["validate_every_n_steps"] = steps["validate_every"]
    argv = ["--dataset_config", dataset_config, "--pretrained_model_name_or_path", base_file,
            "--output_dir", output_dir, "--output_name", str(data["id"])]
    for key, value in flags.items():
        if value is True:
            argv.append(f"--{key}")
        elif value is False or value is None:
            continue
        else:
            argv += [f"--{key}", str(value)]
    if data.get("continue_from"):
        argv += ["--network_weights", continue_file or str(data["continue_from"].get("path") or "<lora file>")]
    return argv


def summary_lines(prepared: Prepared, data: dict, run_file: Path) -> list[str]:
    """What `train plan` prints about the planned run (the legacy emitter adds its own lines)."""
    base = prepared.base.block
    licence = base["licence"]
    lic_name = licence.get("name") or "no name recorded"
    steps = data["steps"]
    out = [f"planned {data['id']}: {prepared.snapshot_id} on {prepared.family_id}, {steps['planned']} steps "
           f"(save every {steps['save_every'] or '-'}, validate every {steps['validate_every'] or '-'})",
           f"  base       {base['label']} ({base.get('resource') or 'no resource id'}, from {prepared.base.found_by}); "
           f"licence {lic_name} ({licence['status']})"]
    cont = prepared.continue_from
    if cont is None:
        out.append("  continue   none: a fresh run from the base")
    else:
        where = cont.block.get("path") or cont.block.get("resource")
        out.append(f"  continue   {cont.block.get('resource')} ({where}, from {cont.found_by}); "
                   "native path: sd-scripts --network_weights")
    est = prepared.estimate
    if est is None:
        out.append(f"  wall clock unknown: no measured_8gb in families/{prepared.family_id}.yaml or its parents")
    else:
        minutes = est["wall_clock_minutes"]
        out.append(f"  wall clock about {minutes} min ({minutes / 60:.1f} h): {est['basis']}")
    ram = prepared.host_ram()
    server = " (includes the ComfyUI server)" if ram.get("eval_includes_comfyui_server") else ""
    out.append(f"  host RAM   {ram.get('train', '?')} GB to train, {ram.get('eval', '?')} GB for the eval render{server}")
    out.append(f"  eval suite {prepared.eval_suite or 'none chosen (--eval-suite <id>)'}")
    out.append(f"  record     {prepared.project.shown(run_file)}")
    out += ["  " + w for w in prepared.warnings]
    return out
