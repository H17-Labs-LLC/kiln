"""`kiln coverage board | needs`: the argparse layer over `kiln.coverage`, which holds the counting rules."""

from __future__ import annotations

import argparse
import json
import sys

HELP = "show the coverage board and the open gaps"


def register(subparsers: argparse._SubParsersAction) -> None:
    """Add `coverage` and its two actions, `board` and `needs`."""
    from kiln.handoff import add_project_args

    parser = subparsers.add_parser(
        "coverage", help=HELP,
        description="The coverage board of a project (projects/<slug>/coverage.yaml): the person's target per "
                    "dimension and bucket, and how many accepted images fill it.")
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")
    board = actions.add_parser(
        "board", help="refresh `current` and `status` from the images, write coverage.yaml, print every bucket",
        description="Count the accepted images per bucket (tier not REJECTED; disposition ACCEPT_TRAIN or "
                    "ACCEPT_CORE, or split train or holdout), write the counts and statuses into coverage.yaml "
                    "(a waived bucket is left as it is) and print the whole board.")
    board.add_argument("--dry-run", action="store_true", help="count and print, but do not write coverage.yaml")
    board.add_argument("--json", action="store_true", help="print the board as one JSON object")
    add_project_args(board)
    board.set_defaults(func=_board)
    needs = actions.add_parser(
        "needs", help="the open gaps, most urgent first: what the dataset still needs",
        description="List the buckets still below target (status open or filling), blockers first, then by the "
                    "most remaining. Reads coverage.yaml as stored; run `kiln coverage board` to refresh it.")
    needs.add_argument("--json", action="store_true", help="print the gaps as one JSON object")
    add_project_args(needs)
    needs.set_defaults(func=_needs)


def _board(args: argparse.Namespace) -> int:
    from kiln import coverage, handoff
    from kiln._common import config, store

    try:
        project = handoff.locate_project(args.tool, args.workspace, args.project)
        if args.dry_run:
            board = coverage.load_board(project.folder)
            images, warnings = coverage.load_images(project.folder)
            changes, dropped = coverage.refresh(board, images), False
        else:
            try:
                with store.lock(project.folder, args.tool):
                    board = coverage.load_board(project.folder)
                    images, warnings = coverage.load_images(project.folder)
                    changes = coverage.refresh(board, images)
                    dropped = coverage.write_board(board) if changes else False
            except store.Held as held:
                handoff.journal_held(project, held)
                raise
    except (config.ConfigError, handoff.Refusal) as exc:
        return handoff.fail(exc)
    handoff.print_lines(warnings, sys.stderr)
    rows = coverage.board_rows(board)
    unlabelled, labels = coverage.unlabelled_dimensions(board, images)
    notes = [f"note: no image carries a `{dim}:<bucket>` label, so its buckets count 0; labels in use: "
             f"{', '.join(labels) or 'none'}" for dim in unlabelled]
    if dropped:
        notes.append(f"note: comments below the header of {coverage.FILE} were not kept")
    if args.json:
        print(json.dumps({"project": project.slug, "gaps": rows, "changed": [vars(c) for c in changes],
                          "written": bool(changes) and not args.dry_run, "notes": notes}, ensure_ascii=False))
        return 0
    handoff.print_lines(handoff.table_lines(coverage.COLUMNS, rows))
    filled = sum(min(r["current"], r["target"]) for r in rows)
    wanted = sum(r["target"] for r in rows)
    met = sum(1 for r in rows if r["status"] == "met")
    waived = sum(1 for r in rows if r["status"] == "waived")
    print(f"{filled}/{wanted} placements filled across {len(rows)} buckets: {met} met, "
          f"{len(rows) - met - waived} open or filling, {waived} waived")
    handoff.print_lines(c.line() for c in changes)
    handoff.print_lines(notes)
    n = len(changes)
    if args.dry_run:
        print(f"dry run: {n} bucket{'' if n == 1 else 's'} would change; {coverage.FILE} not written")
    elif n:
        print(f"{coverage.FILE} updated: {n} bucket{'' if n == 1 else 's'} changed")
    else:
        print(f"{coverage.FILE} unchanged")
    print(f"next: {args.tool} coverage needs")
    return 0


def _needs(args: argparse.Namespace) -> int:
    from kiln import coverage, handoff
    from kiln._common import config

    try:
        project = handoff.locate_project(args.tool, args.workspace, args.project)
        board = coverage.load_board(project.folder)
        images, warnings = coverage.load_images(project.folder)
    except (config.ConfigError, handoff.Refusal) as exc:
        return handoff.fail(exc)
    handoff.print_lines(warnings, sys.stderr)
    rows = coverage.open_gaps(board)
    behind = coverage.stale(board, images)
    notes = []
    if behind:
        shown = ", ".join(f"{c.dimension}/{c.bucket} {c.old_current} -> {c.new_current}" for c in behind[:5])
        more = f" and {len(behind) - 5} more" if len(behind) > 5 else ""
        notes.append(f"note: {coverage.FILE} is behind the images in {len(behind)} bucket"
                     f"{'' if len(behind) == 1 else 's'} ({shown}{more}); run `{args.tool} coverage board` to "
                     "refresh it")
    if args.json:
        print(json.dumps({"project": project.slug, "gaps": rows, "notes": notes}, ensure_ascii=False))
        return 0
    if not rows:
        print(f"no open gaps in {project.slug}")
    else:
        handoff.print_lines(handoff.table_lines(coverage.COLUMNS, rows))
        needed = sum(r["remaining"] for r in rows)
        print(f"{len(rows)} open gap{'' if len(rows) == 1 else 's'} in {project.slug}: {needed} placement"
              f"{'' if needed == 1 else 's'} still needed (one image fills one bucket in each dimension it carries)")
    handoff.print_lines(notes)
    return 0
