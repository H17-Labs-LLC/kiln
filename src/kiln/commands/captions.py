"""`kiln captions list|status|sheet|approve --sheet|apply|lint|propose`: the argparse layer over `kiln.project.captions`.

`captions propose` (drafts from each image's caption basis) is `kiln.sources.propose`: the basis is what the
image's source asked for, so it lives with the sources.
"""

import argparse
import json
from pathlib import Path

from kiln.project import captions, model
from kiln.project.model import add_common_args, handled

HELP = "propose, approve, apply, lint and track captions"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("captions", help=HELP, description=(
        "One training caption per kept image. Drafts go to a sheet, the operator approves rows there, "
        "`approve --sheet` checks and keeps the sheet, `apply` records the approved rows. Only approved "
        "captions are frozen or trained on."))
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")

    show = actions.add_parser("list", help="the latest caption of every image: status, source, words, text",
                              description="List the latest caption line of every image that has one.")
    show.add_argument("--status", choices=("draft", "approved", "rejected"), default=None)
    show.add_argument("--json", action="store_true", help="one JSON line per caption")
    add_common_args(show)
    show.set_defaults(func=_list)

    state = actions.add_parser("status", help="how many kept images have an approved caption, and who waits",
                               description="Count the kept images by caption state: approved, approved in a "
                                           "sheet but not applied, draft, rejected, none.")
    add_common_args(state)
    state.set_defaults(func=_status)

    sheet = actions.add_parser("sheet", help="write a caption sheet for the operator to edit and approve",
                               description="Write sheets/captions-YYYYMMDD-NNN.csv with one row per kept image "
                                           "still waiting (--all: every kept image). The operator edits `caption` "
                                           "and writes approve or reject in `decision`.")
    sheet.add_argument("--all", dest="every", action="store_true", help="every kept image, approved ones too")
    sheet.add_argument("--out", type=Path, default=None, metavar="<file>", help="write the sheet here instead")
    add_common_args(sheet)
    sheet.set_defaults(func=_sheet)

    approve = actions.add_parser("approve", help="check the operator's filled sheet and keep it for apply",
                                 description="Check a sheet the operator filled: rows marked approve or reject are "
                                             "kept for `kiln captions apply`; a row whose draft changed after the "
                                             "sheet was written is skipped. Never mark a row yourself.")
    approve.add_argument("--sheet", type=Path, required=True, metavar="<sheet-file>")
    add_common_args(approve)
    approve.set_defaults(func=_approve)

    apply = actions.add_parser("apply", help="record the approved sheet rows in captions.jsonl",
                               description="Record every approved or rejected sheet row not recorded yet; lint "
                                           "findings are printed and stored, never refused: the operator's word.")
    add_common_args(apply)
    apply.set_defaults(func=_apply)

    check = actions.add_parser("lint", help="check every kept image's caption against the caption rules",
                               description="Check each kept image's caption: trigger first, word range, style tag, "
                                           "no quality, source or age words, the preferred vocabulary. Exit 1 when "
                                           "any image has a problem.")
    add_common_args(check)
    check.set_defaults(func=_lint)

    propose = actions.add_parser("propose", help="draft captions from each image's caption basis",
                                 description="Print a draft caption per kept image without one (or with a rejected "
                                             "one), built from what its source asked for (the prompt), else the "
                                             "operator's note; nobody looks at the image. --store keeps the clean "
                                             "ones as drafts; an approved caption is never redrafted. The operator "
                                             "approves every caption in a sheet afterwards.")
    propose.add_argument("--store", action="store_true", help="keep the clean proposals as drafts")
    propose.add_argument("--all", dest="every", action="store_true",
                         help="also redraft images that have a draft (never an approved caption)")
    add_common_args(propose)
    propose.set_defaults(func=_propose)


@handled
def _list(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    shown = [line for _, line in sorted(captions.latest(project).items())
             if args.status is None or line.get("status") == args.status]
    for line in shown:
        if args.json:
            print(json.dumps(line, ensure_ascii=False))
        else:
            text = str(line.get("text", ""))
            print(f"{line['image']}  {line.get('status'):<8} {str(line.get('source')):<22} "
                  f"{line.get('word_count', 0):>3}w  {text[:70]}{'...' if len(text) > 70 else ''}")
    if not args.json:
        print(f"{len(shown)} captions")
    return 0


@handled
def _status(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    states = captions.status(project)
    total = sum(len(v) for v in states.values())
    print(f"kept images: {total}")
    for state, members in states.items():
        print(f"{state}: {len(members)}")
    waiting = [i for s, members in states.items() if s != "approved" for i in members]
    if waiting:
        print("waiting: " + " ".join(waiting[:15]) + (" ..." if len(waiting) > 15 else ""))
    return 0


@handled
def _sheet(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    path, n = captions.write_sheet(project, every=args.every, out=args.out)
    print(f"caption sheet: {project.shown(path)} ({n} rows)")
    print("  the operator edits `caption` and writes approve or reject in `decision`; blank rows change nothing")
    print(f"  then: kiln captions approve --sheet {project.shown(path)}")
    return 0


@handled
def _approve(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    result = captions.approve_sheet(project, args.sheet)
    for line in result.skipped:
        print(f"skipped: {line}")
    print(f"{result.approved} rows approved, {result.rejected} rejected, {result.blank} left blank, "
          f"{len(result.skipped)} skipped (sheet {args.sheet.name})")
    if result.kept_at is not None:
        print("next: kiln captions apply")
    return 0


@handled
def _apply(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    result = captions.apply_approved(project)
    for line in result.lint_notes:
        print(f"  {line}")
    print(f"{result.applied} approved captions applied, {result.rejected} rejections recorded "
          f"({result.already} sheet rows applied before)")
    return 0


@handled
def _lint(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    found, rules = captions.lint(project)
    for image_id, problems in found:
        print(f"  {image_id}: " + "; ".join(problems))
    print(f"{len(found)} image(s) with problems (rules: {rules.source})")
    return 1 if found else 0


@handled
def _propose(args: argparse.Namespace) -> int:
    from kiln.sources import propose

    project = model.open_project(args)
    result = propose.propose(project, store=args.store, every=args.every)
    for item in result.proposals:
        words = len(item.text.split())
        mark = "  [stored]" if item.stored else ""
        print(f"{item.image}  {item.basis:<6} {words:>3}w  {item.text or '(nothing to go on)'}{mark}")
        for problem in item.problems:
            print(f"    problem: {problem}")
    clean = sum(p.storable for p in result.proposals)
    print(f"{len(result.proposals)} image(s) proposed: from a prompt {result.count('prompt')}, from a note "
          f"{result.count('note')}, nothing to go on {result.count('none')}; {clean} clean"
          + (f", {result.stored} stored as drafts" if args.store else ""))
    left = [f"{result.approved_left} approved"] + ([f"{result.drafts_left} with a draft (--all redrafts them)"]
                                                   if result.drafts_left else [])
    print("  left alone: " + ", ".join(left))
    if args.store:
        print("next: kiln captions sheet (the operator edits and approves every caption)")
    else:
        print("dry run: nothing written; add --store to keep the clean ones as drafts")
    return 0
