"""`kiln runs list [--family <f>] [--status <s>] [--json]`: the training runs of a project.

Answers "which base do I train on": `kiln runs list --family <f> --status ok` names the finished runs whose base
(and its licence) `kiln train plan --base-from-run <run-id>` copies. `runs leaderboard` and `runs unevaluated`
are planned (session 2): they print `planned: session-2 (...)` and exit 2.
"""

from __future__ import annotations

import argparse
import json

from kiln.project.model import add_common_args, handled, open_project

HELP = "list training runs by family and status"


def register(subparsers: argparse._SubParsersAction) -> None:
    from kiln.train.runs import STATUSES

    parser = subparsers.add_parser("runs", help=HELP, description=(
        "The training runs of a project (projects/<slug>/runs/<run-id>/run.json)."))
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")
    listing = actions.add_parser(
        "list", help="one line per run: id | status | family | snapshot | base (licence) | steps | picked step",
        description="List the project's runs, oldest first. --family and --status filter (exact family id).")
    listing.add_argument("--family", default=None, metavar="<family>")
    listing.add_argument("--status", default=None, choices=STATUSES, metavar="<status>",
                         help=f"one of {', '.join(STATUSES)}")
    listing.add_argument("--json", action="store_true", help="the rows as JSON")
    add_common_args(listing)
    listing.set_defaults(func=_list)

    from kiln.commands.train import add_planned

    add_planned(actions, "leaderboard", "session-2", "checkpoints ranked by their eval scores")
    add_planned(actions, "unevaluated", "session-2", "checkpoints that have no eval yet")


@handled
def _list(args: argparse.Namespace) -> int:
    from kiln.train import runs as runs_mod

    project = open_project(args)
    found = runs_mod.read_all(project)
    rows = [runs_mod.row(r) for r in runs_mod.select(found.runs, family=args.family, status=args.status)]
    if args.json:
        print(json.dumps({"project": project.slug, "family": args.family, "status": args.status, "runs": rows,
                          "unreadable": found.unreadable}, indent=1, ensure_ascii=False))
        return 0
    for r in rows:
        print(runs_mod.line(r))
    for name in found.unreadable:
        print(f"warn: {name} does not read as a run record; it is not listed")
    which = ", ".join(x for x in (f"family {args.family}" if args.family else "",
                                  f"status {args.status}" if args.status else "") if x)
    print(f"{len(rows)} run(s)" + (f" ({which})" if which else ""))
    if rows and args.status == "ok":
        family = args.family or rows[-1]["family"]
        print(f"next: kiln train plan <version> --family {family} --base-from-run {rows[-1]['id']} --steps <n>")
    elif not rows and args.status == "ok":
        print("next: ask the operator for the base: kiln train plan <version> --family <family> "
              "--base <sha16|path> --steps <n>")
    return 0
