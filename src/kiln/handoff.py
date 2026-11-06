"""`kiln status --handoff`: the Now block of a hand-off, regenerated from the project's files.

Why: the hand-off message that ends a session (`docs/HANDOFF-TEMPLATE.md`) starts with a Now block that the next
reader acts on first. Filled from memory it drifts; regenerated from the files it says what the files say:

    ## Now
    stage: <the line `kiln next --stage` prints>
    next: <the line `kiln next` prints after "next:">
    running: <run ids in state planned or running, read once from runs/<run-id>/run.json, or "nothing">
    waiting on: <operator yes, for the oldest open decision | nothing (no open decision)>

`stage` and `next` come from `kiln next` itself (called in-process), so the hand-off and `next` can never
disagree; while `next` is not built, or fails, the line says `unknown (...)` with what `next` printed. `--into
<file>` replaces the `## Now` section of an existing hand-off file (or starts a new one) instead of only printing.

The `status` verb belongs to the project store; it wires this in with `add_handoff_args(parser)` (adds
`--handoff` unless the parser has it, and `--into`) and, in its handler, `if handoff.wants_handoff(args): return
handoff.run_handoff(args)` (`handoff.run` is the same function). The handler reads `args.workspace`,
`args.project` and `args.into` when present.

The module also holds the few helpers `kiln coverage`, `kiln decide` and this block share: finding the project
in the instance (`locate_project`), checking a record against its schema (`schema_problems`), the plain
`a | b | c` table lines the three print, and the exit-4 journal line. Example, on the demo instance:
`kiln status --handoff` prints `waiting on: operator yes, for dec-0002 (Retrain 0.3.0: continue or start fresh)`.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from kiln._common import config, ids, journal, store

TOOL = "kiln"
REPO = Path(__file__).resolve().parents[2]  # src/kiln/handoff.py -> the repository root (a checkout)
SCHEMAS = REPO / "schemas"
PROJECT_FILE = "project.yaml"
SLUG = re.compile(r"^[a-z][a-z0-9_]*$")
STAGE = re.compile(r"^\d{2}-[a-z0-9][a-z0-9-]*$")
NOW_HEADING = "## Now"
ACTIVE_RUN_STATES = ("planned", "running")


class Refusal(Exception):
    """A command cannot go on: `.lines` say why and what to run instead; `.code` is the exit code (2 or 1)."""

    def __init__(self, lines: Sequence[str], code: int = 2) -> None:
        super().__init__("\n".join(lines))
        self.lines = list(lines)
        self.code = code


# --- finding the project ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Project:
    """One project of the instance: its slug, its folder `projects/<slug>/` and the instance it lives in."""

    slug: str
    folder: Path
    cfg: config.Config

    @property
    def root(self) -> Path:
        return self.cfg.root if self.cfg.root is not None else self.folder.parent

    def rel(self, path: Path) -> str:
        """`path` relative to the instance root, with forward slashes (no absolute path reaches the output)."""
        return rel(path, self.root)


def rel(path: Path, root: Path | None) -> str:
    if root is not None:
        try:
            return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
        except ValueError:
            pass
    return Path(path).name


def projects_dir(cfg: config.Config) -> Path:
    """The instance's projects folder: `paths.projects` (default `projects`), relative to the instance root."""
    folder = Path(str(cfg.get("paths.projects", "projects"))).expanduser()
    if folder.is_absolute():
        return folder
    if cfg.root is None:
        raise config.ConfigError(2, [cfg.not_found_line(), f"create one with `{cfg.tool} init <folder>`"])
    return cfg.root / folder


def project_slugs(folder: Path) -> list[str]:
    """The slugs of the projects under `folder`: sub-folders holding a project.yaml, sorted."""
    if not folder.is_dir():
        return []
    return sorted(p.name for p in folder.iterdir() if p.is_dir() and (p / PROJECT_FILE).is_file())


def locate_project(tool: str, workspace: str | Path | None, slug: str | None) -> Project:
    """The project a command works on: `--project <slug>`, else the instance's only project.

    Raises config.ConfigError (exit 2) when there is no instance or a `${VAR}` is unset, and Refusal (exit 2)
    when there is no project, the slug is unknown, or several projects exist and none was named.
    """
    cfg = config.load(tool, workspace, strict=True)
    if cfg.file is None:
        raise config.ConfigError(2, [cfg.not_found_line(), "pass --workspace <instance folder>"])
    folder = projects_dir(cfg)
    slugs = project_slugs(folder)
    if slug is not None:
        if not SLUG.match(slug):
            raise Refusal([f"not a project slug (lowercase letters, digits, underscores): {slug!r}"])
        if slug not in slugs:
            have = ", ".join(slugs) or "none"
            raise Refusal([f"no project {slug} in this instance (projects: {have})"])
        return Project(slug, folder / slug, cfg)
    if not slugs:
        raise Refusal(["no project in this instance yet",
                       f"next: {tool} project new <slug> --type character-anime"])
    if len(slugs) > 1:
        raise Refusal([f"this instance has {len(slugs)} projects: {', '.join(slugs)}", "pass --project <slug>"])
    return Project(slugs[0], folder / slugs[0], cfg)


def add_project_args(parser: argparse.ArgumentParser) -> None:
    """`--project` and `--workspace`, the options every project verb of this lane takes."""
    parser.add_argument("--project", metavar="<slug>", default=None,
                        help="the project to work on; default: the instance's only project")
    config.add_workspace_arg(parser)


# --- small shared helpers ---------------------------------------------------------------------------------------

@cache
def _validator(name: str):
    """The validator for `schemas/<name>.schema.json` of this checkout, or None when the checkout has none."""
    path = SCHEMAS / f"{name}.schema.json"
    if not path.is_file():
        return None
    schema = json.loads(path.read_text(encoding="utf-8"))
    if schema.get("$id") != f"urn:{TOOL}:{name}:1":
        return None
    from jsonschema import Draft202012Validator

    return Draft202012Validator(schema)


def schema_problems(name: str, data: object) -> list[str]:
    """Why `data` is not a valid `kiln/<name>/1` record: one `at /path: message` per error (empty when valid).

    Without the repo's schemas folder (an installed wheel) nothing is checked here; the writers build their
    records field by field, and the tests validate every written file against the schema.
    """
    validator = _validator(name)
    if validator is None:
        return []
    errors = sorted(validator.iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])
    return [f"at /{'/'.join(str(p) for p in e.absolute_path)}: {e.message[:200]}" for e in errors]


def cell(value: object) -> str:
    """One table cell: `-` for nothing, whitespace collapsed, `|` escaped so a row stays one row."""
    if value is None or value == "":
        return "-"
    return " ".join(str(value).split()).replace("|", "\\|")


def table_lines(columns: Sequence[str], rows: Iterable[Mapping[str, object]]) -> list[str]:
    """A header line and one `a | b | c` line per row (the shape every list verb of kiln prints)."""
    return [" | ".join(columns), *(" | ".join(cell(row.get(c)) for c in columns) for row in rows)]


def print_lines(lines: Iterable[str], stream=None) -> None:
    for line in lines:
        print(line, file=stream or sys.stdout)


def fail(exc: config.ConfigError | Refusal) -> int:
    """Print a refusal's lines on stderr and return its exit code."""
    print_lines(exc.lines, sys.stderr)
    return exc.code


def journal_held(project: Project, held: store.Held) -> None:
    """Best effort: put the exit-4 line into today's journal of the instance (never raises)."""
    try:
        journal_dir = Path(str(project.cfg.get("paths.journal", "journal"))).expanduser()
        if not journal_dir.is_absolute():
            journal_dir = project.root / journal_dir
        journal.note(f"stuck: {held.line}", tool=project.cfg.tool, journal_dir=journal_dir, timeout=2.0)
    except (config.ConfigError, OSError, ValueError, store.Held):
        pass


# --- the Now block ----------------------------------------------------------------------------------------------

@dataclass
class NowBlock:
    """The four lines of the Now block, in the template's order."""

    stage: str
    next: str
    running: str
    waiting_on: str

    def lines(self) -> list[str]:
        return [NOW_HEADING, f"stage: {self.stage}", f"next: {self.next}", f"running: {self.running}",
                f"waiting on: {self.waiting_on}"]

    def text(self) -> str:
        return "\n".join(self.lines()) + "\n"


def ask_next(argv: Sequence[str]) -> tuple[int, str, str]:
    """Run `kiln next <argv>` in this process and return (exit code, stdout, stderr); never raises.

    `next` is called through the command line, not imported, so the hand-off follows its printed contract
    (`--stage` prints only the stage id; plain `next` prints `stage:`, `read:` and `next:` lines).
    """
    from kiln import cli

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(["next", *argv])
        except SystemExit as exc:  # argparse errors and store.Held
            code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 2)
        except Exception as exc:  # a broken `next` must not break the hand-off; the line shows the failure
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
            code = 1
    return code, out.getvalue(), err.getvalue()


def _first_line(*texts: str) -> str:
    for text in texts:
        for line in text.splitlines():
            if line.strip() and not line.startswith("clock frozen"):
                return " ".join(line.split())
    return "no output"


def _unknown(argv: Sequence[str], code: int, out: str, err: str) -> str:
    shown = " ".join(["kiln next", *argv]).strip()
    return f"unknown (`{shown}` exit {code}: {_first_line(out, err)})"


def next_lines(workspace: str | Path | None, slug: str | None, asker=None) -> tuple[str, str]:
    """(stage, next) as `kiln next --stage` and `kiln next` print them, or `unknown (...)` lines."""
    asker = asker or ask_next
    base: list[str] = []
    if workspace is not None:
        base += ["--workspace", str(workspace)]
    if slug is not None:
        base += ["--project", slug]
    code, out, err = asker(["--stage", *base])
    printed = [line.strip() for line in out.splitlines() if line.strip()]
    if code == 0 and len(printed) == 1 and STAGE.match(printed[0]):
        stage = printed[0]
    else:
        stage = _unknown(["--stage"], code, out, err)
    code, out, err = asker(base)
    found = [line.split(":", 1)[1].strip() for line in out.splitlines() if line.startswith("next:")]
    nxt = found[0] if code == 0 and found and found[0] else _unknown([], code, out, err)
    return stage, nxt


def active_runs(project: Project) -> list[str]:
    """`<run-id> (<state>)` for every run whose run.json says planned or running, by run id."""
    out = []
    for path in sorted((project.folder / "runs").glob("*/run.json")):
        try:
            record = store.read_record(path, f"{TOOL}/run/")
        except (OSError, ValueError) as exc:
            out.append(f"{path.parent.name} (run.json unreadable: {type(exc).__name__})")
            continue
        if record.get("status") in ACTIVE_RUN_STATES:
            out.append(f"{record.get('id') or path.parent.name} ({record.get('status')})")
    return out


def waiting_on(project: Project) -> str:
    """`operator yes, for <id> (<title>)` for the oldest open decision, or `nothing (no open decision)`."""
    from kiln import decisions

    found, problems = decisions.load_all(project.folder)
    rows = decisions.open_rows(found)
    broken = (f"; {len(problems)} decision file{'' if len(problems) == 1 else 's'} unreadable "
              "(kiln decide list)") if problems else ""
    if not rows:
        return f"nothing (no open decision{broken})"
    first = rows[0]
    more = f" and {len(rows) - 1} more (kiln decide list --open)" if len(rows) > 1 else ""
    return f"operator yes, for {first['id']} ({' '.join(str(first['title']).split())}){more}{broken}"


def now_block(project: Project, workspace: str | Path | None = None, slug: str | None = None,
              asker=None) -> NowBlock:
    """The Now block for `project`, read from its files and from `kiln next`."""
    stage, nxt = next_lines(workspace, slug, asker)
    running = ", ".join(active_runs(project)) or "nothing"
    return NowBlock(stage=stage, next=nxt, running=running, waiting_on=waiting_on(project))


def replace_now(text: str, block: NowBlock) -> str:
    """`text` with its `## Now` section (up to the next heading) replaced by `block`; inserted after the title
    when there is none."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == NOW_HEADING), None)
    if start is None:  # no Now section yet: it goes right below the title line, or at the top
        title = next((i for i, line in enumerate(lines) if line.startswith("# ")), None)
        cut = 0 if title is None else title + 1
        head, tail = lines[:cut] + ([""] if cut else []), lines[cut:]
    else:  # the old section runs to the next heading
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("#")), len(lines))
        head, tail = lines[:start], lines[end:]
    while tail and not tail[0].strip():
        tail = tail[1:]
    merged = head + block.lines() + ([""] + tail if tail else [])
    return "\n".join(merged) + "\n"


def write_into(target: Path, block: NowBlock, project: Project) -> Path:
    """Replace the Now block in `target`, or start a new hand-off file there with the template's title line."""
    if target.exists():
        text = target.read_text(encoding="utf-8-sig")
    else:
        when = ids.now(project.cfg.tool).strftime("%Y-%m-%d %H:%M")
        text = f"# Hand-off {when} {project.cfg.tool} {project.slug}\n"
    return store.write_text(target, replace_now(text, block))


# --- wiring into `kiln status` ----------------------------------------------------------------------------------

def add_handoff_args(parser: argparse.ArgumentParser) -> None:
    """Add `--handoff` and `--into <file>` to the `status` parser (skips an option the parser already has)."""
    have = set(getattr(parser, "_option_string_actions", {}))
    if "--handoff" not in have:
        parser.add_argument("--handoff", action="store_true",
                            help="print the Now block of a hand-off, regenerated from the project's files and "
                                 "`kiln next` (docs/HANDOFF-TEMPLATE.md)")
    if "--into" not in have:
        parser.add_argument("--into", metavar="<file>", default=None,
                            help="with --handoff: replace the Now block in this hand-off file (relative to the "
                                 "instance root), or start the file when it does not exist")


def wants_handoff(args: argparse.Namespace) -> bool:
    """True when the command line asked for the Now block (`--handoff`, or `--into` alone)."""
    return bool(getattr(args, "handoff", False) or getattr(args, "into", None))


def run_handoff(args: argparse.Namespace, asker=None) -> int:
    """The `status --handoff` handler: print the Now block, and with `--into` write it into a hand-off file."""
    tool = getattr(args, "tool", None) or TOOL
    workspace, slug = getattr(args, "workspace", None), getattr(args, "project", None)
    into = getattr(args, "into", None)
    try:
        project = locate_project(tool, workspace, slug)
    except (config.ConfigError, Refusal) as exc:
        return fail(exc)
    ids.now(tool)  # the clock-frozen warning, if any, reaches stderr before `next`'s output is captured
    block = now_block(project, workspace, slug, asker)
    written = None
    if into:
        target = Path(into).expanduser()
        if not target.is_absolute():
            target = project.root / target
        try:
            written = write_into(target, block, project)
        except store.Held as held:
            journal_held(project, held)
            raise
    print(block.text(), end="")
    if written is not None:
        print(f"Now block written to {project.rel(written)}")
    return 0


run = run_handoff  # the name a `status` handler may look up: `handoff.run(args)`
