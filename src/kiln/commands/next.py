"""`kiln next [--stage | --json]`: the argparse layer over `kiln.next`."""

import argparse
import json

from kiln import next as next_mod
from kiln.project.model import add_common_args, handled

HELP = "print the current stage, the runbook to read and the next command"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("next", help=HELP, description=(
        "Print three lines: `stage: <id>`, `read: docs/runbooks/<id>.md` (or `read: <id> is planned` while that "
        "runbook is not written), `next: <command>` (ending with `# planned: <task>` when it names something not "
        "built yet: report it once and stop at that stage). With a goal set, the route is 03 -> 05 -> 06 -> 07 -> "
        "08 for that version; without one the stage comes from the files."))
    shape = parser.add_mutually_exclusive_group()
    shape.add_argument("--stage", action="store_true", help="print only the stage id, for example 05-captions")
    shape.add_argument("--json", action="store_true",
                       help="print stage, read, runbook, next, next_planned, why and project as JSON")
    add_common_args(parser)
    parser.set_defaults(func=_next)


@handled
def _next(args: argparse.Namespace) -> int:
    stage = next_mod.decide(args.workspace, args.project)
    if args.stage:
        print(stage.stage)
    elif args.json:
        print(json.dumps(stage.as_dict(), ensure_ascii=False))
    else:
        for line in stage.lines():
            print(line)
    return 0
