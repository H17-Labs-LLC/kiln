"""`kiln review sheet|apply --sheet|readout`: triage by sheet, the argparse layer over `kiln.review_sheet`.

`review readout` (the eval scores read back per checkpoint) comes with the review app (session 2); until then it
prints `planned: session-2 (...)` and exits 2.
"""

import argparse
from pathlib import Path

from kiln import review_sheet
from kiln.project import model
from kiln.project.model import add_common_args, handled

HELP = "triage and vet by sheet: review sheet, review apply --sheet"
READOUT_PLANNED = "planned: session-2 (review readout: the eval scores per checkpoint, each judge apart)"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("review", help=HELP, description=(
        "Triage by sheet until the review app lands: write a sheet of the images that wait for a verdict, the "
        "operator fills it, apply records what they wrote. Only a human judges images; never fill a row."))
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")

    sheet = actions.add_parser("sheet", help="write a sheet of the images that wait for a verdict or their checks",
                               description="Write sheets/review-YYYYMMDD-NNN.csv: one row per image without a "
                                           "verdict, and per kept image not frozen yet that lacks a checklist "
                                           "verdict (--all: every image), with a check:<key> column per checklist "
                                           "item. The operator writes the verdict (ACCEPT_TRAIN, ACCEPT_CORE, "
                                           "ACCEPT_REFERENCE_ONLY, REFINE, REJECT, HOLD), the checks (pass, fail, "
                                           "na) and a note.")
    sheet.add_argument("--all", dest="every", action="store_true", help="every image, judged ones too")
    sheet.add_argument("--out", type=Path, default=None, metavar="<file>", help="write the sheet here instead")
    add_common_args(sheet)
    sheet.set_defaults(func=_sheet)

    apply = actions.add_parser("apply", help="record the verdicts, checks and notes of a filled sheet",
                               description="Record what the operator wrote in a review sheet: verdicts (verdict "
                                           "source operator), check labels, corrected viewpoint or framing, notes. "
                                           "Blank cells change nothing; a row with an unknown word, or whose image "
                                           "got another verdict since the sheet was written, is skipped. Safe to "
                                           "run twice.")
    apply.add_argument("--sheet", type=Path, required=True, metavar="<sheet-file>")
    add_common_args(apply)
    apply.set_defaults(func=_apply)

    readout = actions.add_parser("readout", help="the eval readout, judges kept apart [planned: session-2]",
                                 description="Read the eval scores back per checkpoint, the operator's and the "
                                             "assistant's kept apart and never passed off as each other. "
                                             + READOUT_PLANNED)
    add_common_args(readout)
    readout.set_defaults(func=_readout_planned, planned="session-2")


@handled
def _sheet(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    path, n = review_sheet.write_sheet(project, every=args.every, out=args.out)
    print(f"review sheet: {project.shown(path)} ({n} rows)")
    print("  the operator writes a verdict, the checks and a note per row; blank rows change nothing")
    print(f"  then: kiln review apply --sheet {project.shown(path)}")
    return 0


@handled
def _apply(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    result = review_sheet.apply_sheet(project, args.sheet)
    for line in result.skipped:
        print(f"skipped: {line}")
    by_kind = ", ".join(f"{n} {d}" for d, n in sorted(result.verdicts.items()))
    print(f"{result.recorded} verdicts recorded" + (f" ({by_kind})" if by_kind else "")
          + f", {result.other} other changes, {result.unchanged} unchanged, {result.blank} left blank, "
          f"{len(result.skipped)} skipped (sheet {args.sheet.name})")
    print("next: kiln next")
    return 1 if result.skipped else 0


def _readout_planned(args: argparse.Namespace) -> int:
    print(READOUT_PLANNED)
    return 2
