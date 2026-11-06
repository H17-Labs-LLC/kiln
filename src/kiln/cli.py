"""The `kiln` command line: one table of verbs, each verb in its own module, imported only when it runs."""

# How to add or build a verb (keep this block short and true):
# 1. Every top-level verb has exactly one row in COMMANDS: (verb, "module.path", help, planned-task).
# 2. A built verb is a module that exposes `register(subparsers)`. It calls
#    `subparsers.add_parser("<verb>", help=...)`, adds its subverbs and options, and sets
#    `set_defaults(func=handler)`. `handler(args)` returns an int exit code (None means 0); `args.tool` is "kiln".
# 3. Default home: `kiln/commands/<verb>.py` (thin argparse layer; logic lives in the domain modules). Verbs
#    shared with easel live in `kiln._common.<module>`, written in easel and synced, never edited here.
# 4. Until its module exists, a verb prints `planned: <task> (<help>)` and exits 2. Building it = creating the
#    module; edit the row only to move the module or fix the help. One module per verb; lazy import means a
#    broken verb cannot break the others, so keep module-level imports cheap.
# 5. tests/test_cli.py checks every row. Exit codes: 0 ok, 1 a check found problems, 2 usage error, refusal or
#    planned verb, 3 deferred by a guard, 4 a lock or file is held by another process.

import argparse
import importlib
import importlib.metadata
import importlib.util
import sys
from collections.abc import Sequence
from typing import NamedTuple

from . import __version__

PKG = "kiln"


class Command(NamedTuple):
    verb: str
    module: str
    help: str
    planned: str  # the task that builds it: a runbook task id, "session-2" or "later"


COMMANDS: tuple[Command, ...] = (
    # start, orientation, configuration
    Command("init", "kiln.commands.init", "create a kiln home: kiln.toml, projects/, journal/", "session-2"),
    Command("doctor", "kiln.commands.doctor", "check the install and the instance: ok|warn|FAIL per line, then next:", "session-2"),
    Command("next", "kiln.commands.next", "print the current stage, the runbook to read and the next command", "T7"),
    Command("status", "kiln.commands.status", "show a project's state; --handoff writes the Now block", "T7"),
    Command("config", "kiln._common.config", "show or set configuration values with their source", "T1a"),
    Command("privacy", "kiln._common.privacy", "scan files for private values before they leave the machine", "T1a"),
    Command("journal", "kiln._common.journal", "write the day's journal: note, stuck, report", "T1a"),
    Command("qa", "kiln.commands.qa", "check docs, agent files and tests against the repo's rules", "T13"),
    # shipped data
    Command("families", "kiln.commands.families", "check the model family files: families check", "T6"),
    Command("types", "kiln.commands.types", "check the LoRA type folders: types check", "T6"),
    # project and dataset
    Command("project", "kiln.commands.project",
            "create and inspect a project: new, lint, show, goal set|clear, intake show|answer", "T7"),
    Command("spec", "kiln.commands.spec", "draft, validate and approve the subject spec", "session-2"),
    Command("source", "kiln.commands.source", "run an image source from a jobs file: files, codex, comfy, easel-run", "T8"),
    Command("images", "kiln.commands.images", "list, label, assign, import and promote dataset images", "T7"),
    Command("review", "kiln.commands.review", "triage and vet by sheet: review sheet, review apply --sheet", "T8"),
    Command("captions", "kiln.commands.captions", "propose, approve, apply, lint and track captions", "T7"),
    Command("dataset", "kiln.commands.dataset", "split, freeze, materialize, check and census dataset versions", "T7"),
    Command("coverage", "kiln.commands.coverage", "show the coverage board and the open gaps", "T7b"),
    Command("decide", "kiln.commands.decide", "open, record and list decisions", "T7b"),
    # training, evaluation, release
    Command("train", "kiln.commands.train", "plan and preflight a training run; launch, queue, smoke and tune come later", "T9"),
    Command("runs", "kiln.commands.runs", "list training runs by family and status", "T9"),
    Command("eval", "kiln.commands.eval", "render and score checkpoint evaluations", "session-2"),
    Command("showcase", "kiln.commands.showcase", "render and pick showcase images", "session-2"),
    Command("release", "kiln.commands.release", "build a release folder behind the licence and privacy gates", "session-2"),
    Command("runtime", "kiln.commands.runtime", "install the training runtime and the optional screens", "session-2"),
    # notes and generated files
    Command("note", "kiln.commands.note", "attach a short note to a record", "session-2"),
    Command("index", "kiln.commands.index", "rebuild the generated views: index.jsonl, INDEX.md", "session-2"),
    Command("agents", "kiln.commands.agents", "generate host adapter files from AGENTS.md", "later"),
)

_HELP_FLAGS = {"-h", "--help", "--version"}


def version() -> str:
    """The installed distribution's version, or the source tree's `__version__` when not installed."""
    try:
        return importlib.metadata.version(PKG)
    except importlib.metadata.PackageNotFoundError:
        return __version__


def module_exists(name: str) -> bool:
    """True when the verb's module can be found; a missing parent package (for example `_common`) counts as absent."""
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def _first_verb(argv: Sequence[str]) -> tuple[str | None, bool]:
    """The first non-option word, and whether a top-level -h/--help/--version came before it."""
    for i, word in enumerate(argv):
        if not word.startswith("-"):
            return word, any(a in _HELP_FLAGS for a in argv[:i])
    return None, any(a in _HELP_FLAGS for a in argv)


def build_parser(verb: str | None = None) -> tuple[argparse.ArgumentParser, argparse._SubParsersAction]:
    """The top-level parser. Only `verb`'s module is imported; every other verb gets a help-only placeholder."""
    parser = argparse.ArgumentParser(
        prog=PKG,
        description="kiln builds, trains and evaluates LoRAs with plain files as the registry. Agents: read AGENTS.md first.",
        epilog="A verb marked [planned: <task>] prints that line and exits 2 until it is built.",
    )
    parser.add_argument("--version", action="version", version=f"{PKG} {version()}")
    parser.set_defaults(tool=PKG, func=None)
    sub = parser.add_subparsers(dest="verb", metavar="<verb>", title="verbs")
    for cmd in COMMANDS:
        built = module_exists(cmd.module)
        if built and cmd.verb == verb:
            importlib.import_module(cmd.module).register(sub)
            if verb not in sub.choices:
                raise RuntimeError(f"{cmd.module}.register() did not add the verb {verb!r}")
        else:
            label = cmd.help if built else f"{cmd.help} [planned: {cmd.planned}]"
            sub.add_parser(cmd.verb, help=label, add_help=False)
    return parser, sub


def main(argv: Sequence[str] | None = None) -> int:
    """Run one kiln command and return its exit code."""
    args_list = list(sys.argv[1:] if argv is None else argv)
    by_verb = {cmd.verb: cmd for cmd in COMMANDS}
    verb, help_first = _first_verb(args_list)
    cmd = by_verb.get(verb) if verb else None
    if cmd is not None and not help_first and not module_exists(cmd.module):
        print(f"planned: {cmd.planned} ({cmd.help})")
        return 2
    parser, sub = build_parser(cmd.verb if cmd else None)
    args = parser.parse_args(args_list)
    if args.verb is None:
        parser.print_help()
        return 2
    if args.func is None:  # a built verb called without one of its subverbs
        sub.choices[args.verb].print_help()
        return 2
    code = args.func(args)
    return 0 if code is None else int(code)


if __name__ == "__main__":
    sys.exit(main())
