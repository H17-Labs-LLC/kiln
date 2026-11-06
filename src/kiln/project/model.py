"""The instance, a project, its paths and roots, and the one write path for a project's files.

An instance is a folder with `kiln.toml` (found by `--workspace`, `$KILN_HOME`, upward from the current folder or
`~/.config/kiln/`). Its projects live under `[paths] projects` (default `projects`), one folder per slug. A verb
works on the project named by `--project <slug>`, or on the only project when the instance has one.

Where image bytes live: every `images.jsonl` line names a root (`images`, `imports`, `legacy_output`) and a file
relative to it. `project.yaml` maps each root to a folder relative to the instance root, or to a config token
such as `{images_root}` (`[paths] images_root`, read-only until cutover). Nothing here ever writes under
`images_root`.

Every write goes through `kiln._common.store` under the project's store lock (`<project>/.lock`): records
atomically, event lines as one append each, with `schema` as the first key and `at`, `v`, `source` on every line.
Errors a person can act on are `ProjectError(code, lines)`: exit 2 for a usage error or refusal, 1 for a check
that found problems. Every message names paths relative to the instance root, so output is safe for docs.
"""

from __future__ import annotations

import functools
import json
import re
import sys
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from kiln._common import config, ids, store

TOOL = "kiln"
REPO = Path(__file__).resolve().parents[3]  # src/kiln/project/model.py -> the repository checkout
TYPES_DIR = REPO / "types"
SCHEMAS_DIR = REPO / "schemas"
# The dispositions that keep an image for training (ACCEPT_REFERENCE_ONLY, REFINE, REJECT and HOLD do not).
KEPT = ("ACCEPT_TRAIN", "ACCEPT_CORE")
SPLITS = ("train", "holdout", "reference_only", "unassigned")
TIERS = ("CORE", "SECONDARY", "DIAGNOSTIC", "CANDIDATE", "REJECTED")
DISPOSITIONS = ("ACCEPT_CORE", "ACCEPT_TRAIN", "ACCEPT_REFERENCE_ONLY", "REFINE", "REJECT", "HOLD")
ROOTS = ("images", "imports", "legacy_output")
SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")

SCHEMA = {
    "project": "kiln/project/1",
    "state": "kiln/state/1",
    "image": "kiln/image-event/1",
    "caption": "kiln/caption-event/1",
    "snapshot": "kiln/snapshot/1",
    "materialized": "kiln/materialized/1",
}
# Config tokens a root may start with, and the config key each one reads.
TOKENS = {"images_root": "paths.images_root", "models_root": "paths.models_root", "output_root": "paths.output_root"}


class ProjectError(Exception):
    """A problem the person can act on: `code` is the exit code, `lines` say what is wrong and what to do."""

    def __init__(self, code: int, lines: list[str] | str) -> None:
        self.lines = [lines] if isinstance(lines, str) else list(lines)
        super().__init__("\n".join(self.lines))
        self.code = code


def version_key(version: str) -> tuple[int, ...]:
    """`0.10.0` sorts after `0.9.0`."""
    return tuple(int(p) for p in version.split("."))


def check_version(version: str) -> str:
    if not VERSION.match(version or ""):
        raise ProjectError(2, f"a version is three numbers, for example 0.3.0: {version!r}")
    return version


def now() -> str:
    """The `at` of every new line: the tool's clock (KILN_NOW freezes it)."""
    return ids.now_iso(TOOL)


def why(exc: BaseException, *paths: Path) -> str:
    """A short reason for a failed read, safe for stdout: never the absolute path a lower layer put in its message.

    An OSError gives its plain reason ("No such file or directory"); any other message keeps its words with each
    of `paths` shortened to its file name.
    """
    if isinstance(exc, OSError):
        return exc.strerror or type(exc).__name__
    text = " ".join(str(exc).split())
    for path in paths:
        for form in {str(path), str(Path(path).resolve()), Path(path).as_posix(), Path(path).resolve().as_posix()}:
            text = text.replace(form, Path(path).name)
    return text[:200] or type(exc).__name__


def create_instance_line() -> str:
    """What to do when no kiln.toml is found; `kiln init` is named as planned until its module exists."""
    from kiln import planned

    task = planned.verb_task("init")
    if task is None:
        return "create one with `kiln init <folder>`, or pass --workspace <folder that holds kiln.toml>"
    return (f"`kiln init <folder>` is planned ({task}): ask the operator for the instance folder, then pass "
            "--workspace <folder that holds kiln.toml> or set KILN_HOME")


# --- the instance -----------------------------------------------------------------------------------------------

@dataclass
class Instance:
    """A loaded kiln instance: its config, root folder, projects folder and the read-only images_root."""

    cfg: config.Config
    root: Path
    projects_dir: Path
    images_root: Path | None

    @classmethod
    def load(cls, workspace: str | Path | None = None) -> Instance:
        """Find and load kiln.toml; exit 2 (ProjectError) when there is none."""
        cfg = config.load(TOOL, workspace, strict=True)
        if cfg.file is None or cfg.root is None:
            raise ProjectError(2, [cfg.not_found_line(), create_instance_line()])
        root = cfg.root
        projects_dir = cls._path(root, cfg.get("paths.projects", "projects") or "projects")
        images = cfg.get("paths.images_root", "") or ""
        return cls(cfg=cfg, root=root, projects_dir=projects_dir,
                   images_root=cls._path(root, images) if str(images).strip() else None)

    @staticmethod
    def _path(root: Path, value: object) -> Path:
        p = Path(str(value)).expanduser()
        return (p if p.is_absolute() else root / p).resolve()

    def shown(self, path: Path) -> str:
        """`path` relative to the instance root (forward slashes), or its name when it lies outside."""
        try:
            return Path(path).resolve().relative_to(self.root).as_posix() or "."
        except ValueError:
            return Path(path).name

    def relpath(self, path: Path) -> str | None:
        """`path` relative to the instance root, or None when it lies outside."""
        try:
            return Path(path).resolve().relative_to(self.root).as_posix()
        except ValueError:
            return None

    def slugs(self) -> list[str]:
        """Every folder under the projects folder that holds a project.yaml."""
        if not self.projects_dir.is_dir():
            return []
        return sorted(p.name for p in self.projects_dir.iterdir() if (p / "project.yaml").is_file())

    def project(self, slug: str | None = None) -> Project:
        """The project `slug`, or the only one; exit 2 when it is missing or ambiguous."""
        slugs = self.slugs()
        if slug is None:
            if not slugs:
                raise ProjectError(2, ["no project in this instance yet",
                                       "create one: kiln project new <slug> --type character-anime"])
            if len(slugs) > 1:
                raise ProjectError(2, [f"this instance has {len(slugs)} projects ({', '.join(slugs)}): "
                                       "pass --project <slug>"])
            slug = slugs[0]
        if slug not in slugs:
            known = ", ".join(slugs) or "none"
            raise ProjectError(2, f"no project {slug!r} in this instance (projects: {known})")
        return Project.open(self, slug)

    def under_images_root(self, path: Path) -> bool:
        """True when `path` is images_root or lies inside it (the read-only legacy dataset folder)."""
        if self.images_root is None:
            return False
        target = Path(path).resolve()
        return target == self.images_root or self.images_root in target.parents


# --- a project --------------------------------------------------------------------------------------------------

@dataclass
class Project:
    """One project folder and its project.yaml."""

    instance: Instance
    slug: str
    folder: Path
    data: dict = field(default_factory=dict)

    @classmethod
    def open(cls, instance: Instance, slug: str) -> Project:
        folder = instance.projects_dir / slug
        path = folder / "project.yaml"
        try:
            data = store.read_record(path, "kiln/project/")
        except (OSError, ValueError) as exc:
            raise ProjectError(2, f"{instance.shown(path)}: not a readable project: {why(exc, path)}") from exc
        return cls(instance=instance, slug=slug, folder=folder, data=data)

    # paths
    @property
    def images_log(self) -> Path:
        return self.folder / "images.jsonl"

    @property
    def captions_log(self) -> Path:
        return self.folder / "captions.jsonl"

    @property
    def state_file(self) -> Path:
        return self.folder / "state.json"

    @property
    def snapshots_dir(self) -> Path:
        return self.folder / "snapshots"

    @property
    def datasets_dir(self) -> Path:
        return self.folder / "datasets"

    @property
    def runs_dir(self) -> Path:
        return self.folder / "runs"

    @property
    def sheets_dir(self) -> Path:
        return self.folder / "sheets"

    @property
    def adopted(self) -> bool:
        return bool(self.data.get("adopted"))

    @property
    def trigger(self) -> str | None:
        return self.data.get("trigger")

    def shown(self, path: Path) -> str:
        return self.instance.shown(path)

    def root_dir(self, name: str) -> Path:
        """The folder of root `name` (images, imports, legacy_output); exit 2 when it is not configured."""
        roots = self.data.get("roots") or {}
        value = roots.get(name)
        if not value:
            raise ProjectError(2, f"project.yaml roots.{name} is not set for {self.slug}")
        text = str(value)
        match = re.match(r"^\{([a-z_]+)\}(?:/(.*))?$", text)
        if match:
            token, rest = match.group(1), match.group(2) or ""
            key = TOKENS.get(token)
            base = self.instance.cfg.get(key, "") if key else ""
            if not base:
                raise ProjectError(2, [f"project.yaml roots.{name} uses {{{token}}}, which this instance does not "
                                       f"configure", f"set it: kiln config set {key or 'paths.' + token} <folder>"])
            folder = Instance._path(self.instance.root, base)
            return (folder / rest).resolve() if rest else folder
        return Instance._path(self.instance.root, text)

    def image_path(self, record: Mapping[str, object]) -> Path:
        """Where an image record's bytes are: its root folder plus its relative file."""
        return self.root_dir(str(record["root"])) / str(record["file"])

    # state
    def state(self) -> dict:
        """state.json; a missing file reads as a planning project with no goal."""
        if not self.state_file.is_file():
            return {"schema": SCHEMA["state"], "project": self.slug, "status": "planning", "phase": None,
                    "goal": None, "updated_at": now(), "legacy_id": None}
        return store.read_record(self.state_file, "kiln/state/")

    def write_state(self, state: Mapping[str, object]) -> None:
        data = {k: v for k, v in state.items() if k != "schema"}
        data["updated_at"] = now()
        with self.lock():
            store.write_record(self.state_file, SCHEMA["state"], data)

    def goal(self) -> dict | None:
        return self.state().get("goal")

    # writing
    @contextmanager
    def lock(self) -> Iterator[Path]:
        """The project's store lock: every write to the project's files happens inside it."""
        with store.lock(self.folder, TOOL) as held:
            yield held

    def append(self, log: Path, lines: list[dict]) -> None:
        """Append event lines to a log under the project lock, one write() per line.

        A caller that reads the log, decides, then appends (a verdict, an approval, a draft) holds `lock()` around
        the read and this call, so a second session cannot slip a line in between (the lock is re-entrant)."""
        with self.lock():
            for line in lines:
                store.append_bytes_locked(log, (json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))


def event_line(schema: str, data: Mapping[str, object], *, source: object, by: str | None) -> dict:
    """An event line in the stored order: schema, at, v, source, by, then the line's own keys."""
    line: dict = {"schema": schema, "at": now(), "v": 1, "source": source}
    if by:
        line["by"] = by
    line.update({k: v for k, v in data.items() if k not in ("schema", "at", "v", "source", "by")})
    return line


# --- the command layer's helpers ---------------------------------------------------------------------------------

def add_common_args(parser) -> None:
    """`--workspace` and `--project`: every project verb takes both."""
    config.add_workspace_arg(parser)
    parser.add_argument("--project", metavar="<slug>", default=None,
                        help="the project to work on; default: the instance's only project")


def open_project(args) -> Project:
    """The instance from --workspace and the project from --project."""
    return Instance.load(getattr(args, "workspace", None)).project(getattr(args, "project", None))


def handled(func: Callable) -> Callable:
    """Turn ProjectError and ConfigError into their lines and exit code (stdout for refusals, stderr for config)."""
    @functools.wraps(func)
    def wrapper(args) -> int:
        try:
            code = func(args)
        except ProjectError as exc:
            for line in exc.lines:
                print(line)
            return exc.code
        except config.ConfigError as exc:
            for line in exc.lines:
                print(line, file=sys.stderr)
            return exc.code
        return 0 if code is None else int(code)

    return wrapper
