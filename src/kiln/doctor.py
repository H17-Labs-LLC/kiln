"""`kiln doctor`: one line per check, then `next:`. Exit 0 when no line is a FAIL, 1 otherwise.

Line shapes (the same as `easel doctor`): `<check>: <what was found> ... ok` and `... warn` (a warn is reported to
the operator; it does not block), and `FAIL: <check>: <what is wrong>` (fix it before any stage). A `fix:` line
under a warn or FAIL says what to do. Instance paths are shown relative to the instance or as a `<key>` token
(`<models_root>`, `<train_python>`), never absolute, so the output is safe for docs.

The checks, in order:
- the install: the version, the shipped family files and LoRA types (the summary lines of `kiln families check`
  and `kiln types check`);
- the instance file: found, valid, every `${VAR}` set (an unset one is a FAIL); native or adopted;
- the folders of `[paths]`: `models_root` (a warn when missing: kiln reads models only from the training stage
  on), `projects/`, `journal/`, `runtime/` (a warn: the first command that writes there creates them) and
  `images_root` when set (a FAIL on a native instance, a warn on an adopted one);
- the trainer runtime: `[train] python` when set, else the interpreter inside `[train] venv_dir`, and the
  `[train] sd_scripts` checkout. Missing is a warn that names the planned installer, never a FAIL;
- the scheduler (free RAM measurable) and the free disk against `limits.disk_floor_gb` (20 GB when unset);
- on an adopted instance the `[legacy]` keys `kiln train plan --emit-legacy` reads;
- the policy and the projects of the instance.

Adopted instances (`instance.adopted = true` or `policy.render = "legacy-runner"`): the trainer, the models and
the image folder belong to the host and are used through its own runner, so a missing piece there is a warn,
never a FAIL. Doctor only reads: it never runs the trainer, starts a process, writes a record or changes the
configuration.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass
from pathlib import Path

from kiln._common import config, scheduler, sysinfo

TOOL = "kiln"
REPO = Path(__file__).resolve().parents[2]  # src/kiln/doctor.py -> the repository checkout
FAMILIES_DIR = REPO / "families"
TYPES_DIR = REPO / "types"
SCHEMAS_DIR = REPO / "schemas"
DEFAULT_DISK_FLOOR_GB = 20.0  # a neutral default when limits.disk_floor_gb is unset; an instance sets its own
INSTANCE_FOLDERS = ("projects", "journal", "runtime")
SD_SCRIPTS_MARKERS = ("sdxl_train_network.py", "train_network.py")
INSTALLER = "training runtime installer: session 2"


@dataclass
class Check:
    state: str  # ok | warn | FAIL
    text: str
    fix: str | None = None

    def lines(self) -> list[str]:
        head = f"FAIL: {self.text}" if self.state == "FAIL" else f"{self.text} ... {self.state}"
        return [head] + ([f"  fix: {self.fix}"] if self.fix else [])


def _version() -> str:
    from kiln import cli

    return cli.version()


def _first_line(exc: BaseException) -> str:
    lines = getattr(exc, "lines", None)
    text = lines[0] if isinstance(lines, list) and lines else str(exc)
    return (text.splitlines() or [type(exc).__name__])[0][:200]


def is_adopted(cfg: config.Config) -> bool:
    """`instance.adopted = true` or `policy.render = "legacy-runner"`."""
    return bool(cfg.get("instance.adopted", False)) or str(cfg.get("policy.render", "native")) == "legacy-runner"


def resolve(root: Path, value: object) -> Path:
    """A path value: absolute as written (`~` expanded), otherwise relative to the instance root."""
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else root / p


def shown(path: Path, root: Path, token: str) -> str:
    """`path` relative to the instance root (forward slashes), or `token` when it lies outside."""
    try:
        return Path(path).resolve().relative_to(root.resolve()).as_posix() or "."
    except (ValueError, OSError):
        return token


# --- the install ------------------------------------------------------------------------------------------------

def families_line() -> Check:
    """The summary of `kiln families check` on the shipped family files."""
    from kiln import families_check

    if not FAMILIES_DIR.is_dir():
        return Check("FAIL", "families: no families/ folder in this checkout",
                     "install kiln from a full checkout (pip install -e .)")
    report = families_check.check_families(FAMILIES_DIR)
    count = len(report.checked)
    if report.problems:
        return Check("FAIL", f"families: {len(report.problems)} problem(s) in {count} family file(s)",
                     "kiln families check (it names them); report them to the maintainer")
    if not count:
        return Check("FAIL", "families: no family files in families/", "install kiln from a full checkout")
    tail = f", {len(report.warnings)} warning(s) (kiln families check lists them)" if report.warnings else ""
    return Check("ok", f"families: {count} family file(s) checked{tail}")


def types_line() -> Check:
    """The summary of `kiln types check` on the shipped LoRA types."""
    from kiln import types_check

    if not TYPES_DIR.is_dir():
        return Check("FAIL", "types: no types/ folder in this checkout",
                     "install kiln from a full checkout (pip install -e .)")
    report = types_check.check_types(TYPES_DIR, SCHEMAS_DIR)
    count = len(report.checked)
    if report.problems:
        return Check("FAIL", f"types: {len(report.problems)} problem(s) in {count} type(s)",
                     "kiln types check (it names them); report them to the maintainer")
    if not count:
        return Check("FAIL", "types: no type folders in types/", "install kiln from a full checkout")
    tail = f", {len(report.warnings)} warning(s) (kiln types check lists them)" if report.warnings else ""
    return Check("ok", f"types: {count} type(s) checked: {', '.join(report.checked)}{tail}")


def install_checks() -> list[Check]:
    checks = [Check("ok", f"{TOOL}: {_version()} (python {platform.python_version()})")]
    for one in (families_line, types_line):
        try:
            checks.append(one())
        except Exception as exc:  # noqa: BLE001  (doctor reports every problem as a line; it never stops early)
            checks.append(Check("warn", f"{one.__name__.removesuffix('_line')}: not checkable ({_first_line(exc)})"))
    return checks


# --- the instance -----------------------------------------------------------------------------------------------

def folder_checks(cfg: config.Config, root: Path, adopted: bool, unset: set[str]) -> list[Check]:
    """models_root, the instance's own folders and images_root."""
    checks: list[Check] = []
    if "paths.models_root" not in unset:
        value = str(cfg.get("paths.models_root", "") or "").strip()
        if not value:
            checks.append(Check("warn", "models: paths.models_root is empty; training and evaluation need it",
                                f"{TOOL} config set paths.models_root <models_root>"))
        elif resolve(root, value).is_dir():
            checks.append(Check("ok", "models: <models_root> exists"))
        else:
            checks.append(Check("warn", "models: paths.models_root does not exist (<models_root>); training and "
                                        "evaluation need it", f"{TOOL} config set paths.models_root <models_root>"))

    present, missing = [], []
    for key in INSTANCE_FOLDERS:
        if f"paths.{key}" in unset:
            continue
        folder = resolve(root, cfg.get(f"paths.{key}", key) or key)
        name = shown(folder, root, f"<{key}>")
        (present if folder.is_dir() else missing).append(name if name.startswith("<") else f"{name}/")
    there = f"{', '.join(present)} {'exists' if len(present) == 1 else 'exist'}"
    if missing:
        checks.append(Check("warn", f"folders: {', '.join(missing)} not there yet (the first command that writes "
                                    f"there creates it){'; ' + there if present else ''}"))
    elif present:
        checks.append(Check("ok", f"folders: {there}"))

    if "paths.images_root" not in unset:
        value = str(cfg.get("paths.images_root", "") or "").strip()
        if value and resolve(root, value).is_dir():
            checks.append(Check("ok", "images_root: <images_root> exists (kiln only reads it)"))
        elif value:
            text = "images_root: paths.images_root does not exist (<images_root>)"
            fix = f"{TOOL} config set paths.images_root <images_root>"
            checks.append(Check("warn", text + "; the host's image folder", fix) if adopted
                          else Check("FAIL", text, fix))
    return checks


def train_checks(cfg: config.Config, root: Path, adopted: bool, unset: set[str]) -> list[Check]:
    """The trainer's interpreter and the sd-scripts checkout; missing is a warn, never a FAIL."""
    fix = ("the operator points train.python and train.sd_scripts at the host's trainer (kiln config set)" if adopted
           else "report it to the operator; they may point train.python and train.sd_scripts at an existing "
                "trainer (kiln config set)")
    checks: list[Check] = []
    python = "" if "train.python" in unset else str(cfg.get("train.python", "") or "").strip()
    if python:
        path = resolve(root, python)
        where = shown(path, root, "<train_python>")
        if path.is_file():
            checks.append(Check("ok", f"train runtime: python {where} (train.python)"))
        else:
            checks.append(Check("warn", f"train runtime: train.python names no file ({where}); {INSTALLER}", fix))
    elif "train.venv_dir" not in unset:
        venv = resolve(root, cfg.get("train.venv_dir", "runtime/venv-train") or "runtime/venv-train")
        where = shown(venv, root, "<venv_dir>")
        if sysinfo.venv_python(venv).is_file():
            checks.append(Check("ok", f"train runtime: {where} has a python (train.venv_dir)"))
        else:
            checks.append(Check("warn", f"train runtime: no python in {where} (train.venv_dir); {INSTALLER}", fix))
    if "train.sd_scripts" not in unset:
        sd = resolve(root, cfg.get("train.sd_scripts", "runtime/sd-scripts") or "runtime/sd-scripts")
        where = shown(sd, root, "<sd_scripts>")
        if sd.is_dir():
            found = next((name for name in SD_SCRIPTS_MARKERS if (sd / name).is_file()), None)
            if found:
                checks.append(Check("ok", f"sd-scripts: {where} ({found} found)"))
            else:
                checks.append(Check("warn", f"sd-scripts: {where} holds no {' or '.join(SD_SCRIPTS_MARKERS)}; "
                                            f"{INSTALLER}", fix))
        else:
            checks.append(Check("warn", f"sd-scripts: {where} does not exist (train.sd_scripts); {INSTALLER}", fix))
    warned = [c for c in checks if c.state == "warn"]
    for later in warned[1:]:  # one fix line for the runtime is enough
        later.fix = None
    return checks


def scheduler_check(cfg: config.Config) -> Check:
    kind = str(cfg.get("scheduler.kind", "none"))
    try:
        scheduler.from_config(cfg)
    except scheduler.SchedulerError as exc:
        return Check("FAIL", f"scheduler: {_first_line(exc)}", "the operator fixes [scheduler] in kiln.toml")
    if kind != "none":
        fail_open = str(bool(cfg.get("scheduler.fail_open", False))).lower()
        return Check("ok", f"scheduler: {kind} (an external scheduler configured in [scheduler] decides; "
                           f"fail_open = {fail_open})")
    try:
        return Check("ok", f"scheduler: none (this machine alone); free RAM {sysinfo.free_ram_gb():.1f} GB")
    except sysinfo.SysinfoUnavailable:
        return Check("warn", "scheduler: " + scheduler.CANNOT_MEASURE, "the operator decides; never set the key yourself")


def disk_check(cfg: config.Config, root: Path) -> Check:
    try:
        raw = cfg.get("limits.disk_floor_gb", DEFAULT_DISK_FLOOR_GB)
        floor = float(DEFAULT_DISK_FLOOR_GB if raw is None else raw)
        free = sysinfo.disk_free_gb(root)
    except (sysinfo.SysinfoUnavailable, OSError, ValueError, TypeError):
        return Check("warn", "disk: free space cannot be measured", "tell the operator")
    if free < floor:
        return Check("warn", f"disk: free {free:.0f} GB < {floor:g} GB floor (limits.disk_floor_gb); downloads and "
                             "training wait", "tell the operator; they decide what is freed (never delete anything "
                                              "yourself)")
    return Check("ok", f"disk: free {free:.0f} GB >= {floor:g} GB floor")


def legacy_check(cfg: config.Config) -> Check:
    """On an adopted instance: does `[legacy]` hold what `kiln train plan --emit-legacy` reads?"""
    from kiln.project.model import ProjectError
    from kiln.train import legacy_emit

    fix = "the operator fills [legacy] in kiln.toml; until then kiln train plan --emit-legacy refuses"
    try:
        legacy_emit.load_legacy(cfg)
    except (ProjectError, config.ConfigError) as exc:
        return Check("warn", f"legacy: {_first_line(exc)}", fix)
    return Check("ok", "legacy: [legacy] names the host's root, experiment, renderer, templates and folders")


def projects_check(cfg: config.Config, root: Path) -> Check:
    folder = resolve(root, cfg.get("paths.projects", "projects") or "projects")
    slugs = sorted(p.name for p in folder.iterdir() if (p / "project.yaml").is_file()) if folder.is_dir() else []
    if not slugs:
        return Check("ok", "projects: none yet")
    names = ", ".join(slugs[:5]) + (", ..." if len(slugs) > 5 else "")
    return Check("ok", f"projects: {len(slugs)} ({names})")


def _guarded(name: str, func, *args) -> list[Check]:
    """Run one check; an unexpected error becomes a warn line (doctor never stops early)."""
    try:
        result = func(*args)
    except Exception as exc:  # noqa: BLE001
        return [Check("warn", f"{name}: not checkable ({_first_line(exc)})")]
    return result if isinstance(result, list) else [result]


def run_checks(tool: str = TOOL, workspace: str | Path | None = None) -> tuple[list[Check], str]:
    """(checks, the next command)."""
    checks = install_checks()
    try:
        cfg = config.load(tool, workspace, strict=False)
    except config.ConfigError as exc:
        checks += [Check("FAIL", f"config: {line}") for line in exc.lines]
        return checks, f"fix the config line above, then `{tool} doctor`"
    if cfg.file is None or cfg.root is None:
        checks.append(Check("FAIL", f"config: no {tool}.toml here, in a parent folder or in the user config folder",
                            f"{tool} init ~/{tool}-home, then work inside it, set {tool.upper()}_HOME to it or pass "
                            "--workspace"))
        return checks, f"{tool} init ~/{tool}-home"
    root = cfg.root
    checks.append(Check("ok", f"config: {cfg.file.name} (found by {cfg.found_by})"))
    for line in cfg.unset_lines():
        key = line.rsplit("(", 1)[-1].rstrip(")")
        checks.append(Check("FAIL", line.removeprefix("FAIL: "), f"set the variable, or {tool} config set {key} <value>"))
    unset = {key for _var, key in cfg.unset}
    try:
        adopted = is_adopted(cfg)
    except config.ConfigError:  # instance.adopted or policy.render names an unset ${VAR}: already a FAIL above
        adopted = False
    if adopted:
        how = ("policy.render = legacy-runner: the host's runner trains" if str(cfg.get("policy.render", "")) ==
               "legacy-runner" else "instance.adopted = true")
        checks.append(Check("ok", f"instance: adopted ({how})"))
    else:
        checks.append(Check("ok", "instance: native"))

    checks += _guarded("folders", folder_checks, cfg, root, adopted, unset)
    checks += _guarded("train runtime", train_checks, cfg, root, adopted, unset)
    checks += _guarded("scheduler", scheduler_check, cfg)
    checks += _guarded("disk", disk_check, cfg, root)
    if adopted:
        checks += _guarded("legacy", legacy_check, cfg)
    checks += _guarded("policy", lambda: Check("ok", (
        f"policy: subjects {cfg.get('policy.subjects', 'fictional-adults-only')}, prompt authoring "
        f"{cfg.get('policy.prompt_authoring', 'sfw-only')}, reviewer {cfg.get('policy.reviewer', 'human-only')}, "
        f"render {cfg.get('policy.render', 'native')}")))
    checks += _guarded("projects", projects_check, cfg, root)

    first = next((c for c in checks if c.state == "FAIL"), None)
    if first is not None:
        return checks, first.fix or f"fix the line above, then `{tool} doctor`"
    return checks, f"{tool} next"


def report(tool: str = TOOL, workspace: str | Path | None = None) -> tuple[int, list[str]]:
    """(exit code, every line incl. `next:`)."""
    checks, nxt = run_checks(tool, workspace)
    lines = [line for check in checks for line in check.lines()]
    lines.append(f"next: {nxt}")
    return (1 if any(c.state == "FAIL" for c in checks) else 0), lines
