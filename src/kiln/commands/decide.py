"""`kiln decide open | <id> "<answer>" | list [--open]`: the argparse layer over `kiln.decisions`.

The record step has two spellings: `kiln decide dec-0002 "<answer>"` (the short one the docs use) and
`kiln decide decide dec-0002 "<answer>"` (the plan's action name). A decision id where the action belongs is
read as the second form.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

HELP = "open, record and list decisions"
_DEC_ID = re.compile(r"dec-[0-9]{4}")


class _Actions(dict):
    """The action table: a decision id also counts as a choice (it stands for `decide <id>`)."""

    def __contains__(self, key: object) -> bool:
        return dict.__contains__(self, key) or (isinstance(key, str) and _DEC_ID.fullmatch(key) is not None)


class _DecideActions(argparse._SubParsersAction):
    """Subparsers that read `decide dec-0002 ...` as `decide decide dec-0002 ...`."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._name_parser_map = self.choices = _Actions()

    def __call__(self, parser, namespace, values, option_string=None) -> None:
        if values and not dict.__contains__(self._name_parser_map, values[0]) and _DEC_ID.fullmatch(values[0]):
            values = ["decide", *values]
        super().__call__(parser, namespace, values, option_string)


def register(subparsers: argparse._SubParsersAction) -> None:
    """Add `decide` and its actions `open`, `decide` (also `decide <id>`) and `list`."""
    from kiln.handoff import add_project_args

    parser = subparsers.add_parser(
        "decide", help=HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Decisions of a project (projects/<slug>/decisions/dec-NNNN.md). Only the person decides:\n"
                    "  kiln decide open \"<title>\" --question \"<q>\" [--option \"<o>\"]...   ask (writes dec-NNNN)\n"
                    "  kiln decide <dec-id> \"<answer>\" [--rationale \"<why>\"]           record their answer\n"
                    "  kiln decide list [--open]                                     show decisions")
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions", action=_DecideActions)

    opener = actions.add_parser(
        "open", help="write the next dec-NNNN with status open: the question for the person",
        description="Write the next free decisions/dec-NNNN.md with status open. Opening only asks; the person "
                    "answers, and `kiln decide <id> \"<answer>\"` records it.")
    opener.add_argument("title", help="a short title, for example \"Holdout size for 0.3.0\"")
    opener.add_argument("--question", required=True, help="the question for the person, in one sentence")
    opener.add_argument("--option", action="append", default=[], metavar="<option>",
                        help="one answer the person can choose; repeat for each")
    opener.add_argument("--supersedes", metavar="<dec-id>", default=None,
                        help="the earlier decision this one replaces; it is marked superseded when this one is "
                             "decided")
    opener.add_argument("--context", metavar="<text>", default=None,
                        help="facts the person needs to answer (commands and their output), for the file's body")
    add_project_args(opener)
    opener.set_defaults(func=_open)

    record = actions.add_parser(
        "decide", help="record the person's answer to an open decision (also: kiln decide <dec-id> \"<answer>\")",
        description="Record the person's answer, verbatim, to an open decision: status decided, decided_by "
                    "operator, decided_at from the clock. A decided record is not rewritten; open a new one with "
                    "--supersedes instead.")
    record.add_argument("dec_id", metavar="<dec-id>", help="the decision, for example dec-0002")
    record.add_argument("answer", help="the person's answer, verbatim, in quotes")
    record.add_argument("--rationale", default=None, help="the person's reason, verbatim, if they gave one")
    add_project_args(record)
    record.set_defaults(func=_decide)

    lister = actions.add_parser(
        "list", help="every decision by id; --open: the open ones, oldest first, with their question",
        description="List the project's decisions. With --open, only the open ones, oldest first, with the "
                    "question each asks.")
    lister.add_argument("--open", action="store_true", help="only the open decisions, oldest first")
    lister.add_argument("--json", action="store_true", help="print the rows as one JSON object")
    add_project_args(lister)
    lister.set_defaults(func=_list)


def _open(args: argparse.Namespace) -> int:
    from kiln import decisions, handoff
    from kiln._common import config, store

    try:
        project = handoff.locate_project(args.tool, args.workspace, args.project)
        try:
            decision = decisions.open_decision(project.folder, project.slug, args.title, args.question,
                                               options=args.option, supersedes=args.supersedes,
                                               context=args.context, tool=args.tool)
        except store.Held as held:
            handoff.journal_held(project, held)
            raise
    except (config.ConfigError, handoff.Refusal) as exc:
        return handoff.fail(exc)
    print(f"{decision.id} opened: {decision.front['title']}")
    print(f"file: {project.rel(decision.path)}")
    print(f"next: ask the person the question; then {args.tool} decide {decision.id} \"<their answer>\"")
    return 0


def _decide(args: argparse.Namespace) -> int:
    from kiln import decisions, handoff
    from kiln._common import config, store

    try:
        project = handoff.locate_project(args.tool, args.workspace, args.project)
        try:
            decision, old = decisions.record_decision(project.folder, args.dec_id, args.answer,
                                                      rationale=args.rationale, tool=args.tool)
        except store.Held as held:
            handoff.journal_held(project, held)
            raise
    except (config.ConfigError, handoff.Refusal) as exc:
        return handoff.fail(exc)
    print(f"{decision.id} decided: {decision.front['decision']}")
    if old is not None:
        print(f"{old.id} superseded by {decision.id}")
    return 0


def _list(args: argparse.Namespace) -> int:
    from kiln import decisions, handoff
    from kiln._common import config

    try:
        project = handoff.locate_project(args.tool, args.workspace, args.project)
    except (config.ConfigError, handoff.Refusal) as exc:
        return handoff.fail(exc)
    found, problems = decisions.load_all(project.folder)
    handoff.print_lines((f"skipped {p}" for p in problems), sys.stderr)
    code = 1 if problems else 0
    rows = decisions.open_rows(found) if args.open else decisions.list_rows(found)
    if args.json:
        print(json.dumps({"project": project.slug, "decisions": rows, "problems": problems}, ensure_ascii=False))
        return code
    if args.open:
        if not rows:
            print(f"no open decision in {project.slug}")
            return code
        handoff.print_lines(handoff.table_lines(decisions.OPEN_COLUMNS, rows))
        print(f"{len(rows)} open decision{'' if len(rows) == 1 else 's'} in {project.slug}")
        print(f"next: ask the person; record each answer verbatim with {args.tool} decide <dec-id> \"<answer>\"")
        return code
    if not rows:
        print(f"no decision in {project.slug} yet")
        return code
    handoff.print_lines(handoff.table_lines(decisions.LIST_COLUMNS, rows))
    by_status = {s: sum(1 for r in rows if r["status"] == s) for s in decisions.STATUSES}
    print(f"{len(rows)} decision{'' if len(rows) == 1 else 's'} in {project.slug}: "
          + ", ".join(f"{n} {s}" for s, n in by_status.items() if n))
    return code
