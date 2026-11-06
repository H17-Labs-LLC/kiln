"""`kiln status [--json] [--handoff [--into <file>]]`: each project's health, goal and stage; or the handoff Now block.

The health is counted from the project's files (`kiln.project.status`). `--handoff`
belongs to `kiln.handoff` (task T7b), wired in with its `add_handoff_args` and `run_handoff`; while that module
is absent, `--handoff` prints `planned: T7b (...)` and exits 2.
"""

import argparse
import importlib
import importlib.util
import json

from kiln import next as next_mod
from kiln.project import model, status
from kiln.project.model import add_common_args, handled

HELP = "show a project's state; --handoff writes the Now block"
HANDOFF_MODULE = "kiln.handoff"
HANDOFF_PLANNED = "planned: T7b (status --handoff: regenerate the Now block of the handoff)"


def _handoff_available() -> bool:
    try:
        return importlib.util.find_spec(HANDOFF_MODULE) is not None
    except ModuleNotFoundError:
        return False


def _handoff():
    return importlib.import_module(HANDOFF_MODULE) if _handoff_available() else None


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("status", help=HELP, description=(
        "One block per project: title, status, phase, CORE and total images, open coverage gaps, open "
        "decisions, runs, snapshots, the goal and the stage `kiln next` derives. --handoff prints the Now "
        "block of a hand-off instead."))
    parser.add_argument("--json", action="store_true", help="one JSON object per project")
    add_common_args(parser)
    handoff = _handoff()
    if handoff is not None:
        handoff.add_handoff_args(parser)
    else:
        parser.add_argument("--handoff", action="store_true", help="the Now block of a hand-off [planned: T7b]")
    parser.set_defaults(func=_status)


@handled
def _status(args: argparse.Namespace) -> int:
    handoff = _handoff()
    if handoff is not None and handoff.wants_handoff(args):
        return handoff.run_handoff(args)
    if handoff is None and getattr(args, "handoff", False):
        print(HANDOFF_PLANNED)
        return 2
    instance = model.Instance.load(args.workspace)
    slugs = [args.project] if args.project else instance.slugs()
    if not slugs:
        print("no project in this instance yet; next: kiln project new <slug> --type character-anime")
        return 0
    for n, slug in enumerate(slugs):
        project = instance.project(slug)
        row = status.status(project)
        stage = next_mod.decide(args.workspace, slug).stage
        if args.json:
            print(json.dumps({**row.as_dict(), "stage": stage}, ensure_ascii=False))
            continue
        if n:
            print()
        print(f"project: {row.project} ({row.title})")
        print(f"status: {row.status}")
        print(f"phase: {row.phase or 'none'}")
        print(f"core_refs: {row.core_refs}")
        print(f"total_refs: {row.total_refs}")
        print(f"open_gaps: {row.open_gaps}")
        print(f"open_decisions: {row.open_decisions}")
        print(f"runs: {row.runs}")
        print(f"snapshots: {row.snapshots}")
        print(f"goal: {status.goal_text(row.goal)}")
        print(f"stage: {stage}")
    return 0
