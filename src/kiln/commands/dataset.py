"""`kiln dataset split|freeze|materialize|check|census`: the argparse layer over the dataset modules."""

import argparse
from pathlib import Path

from kiln.project import check, materialize, model, snapshot, split
from kiln.project.model import add_common_args, handled

HELP = "split, freeze, materialize, check and census dataset versions"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("dataset", help=HELP, description=(
        "The dataset from split to a frozen, materialised version. The order is fixed: captions, then split, "
        "then freeze."))
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")

    cut = actions.add_parser("split", help="propose train/holdout for the unassigned kept images (dry run)",
                             description="Place the kept images that have no split, whole lineages at a time, so "
                                         "the holdout comes near <n>. Without --apply nothing is written.")
    cut.add_argument("--holdout", type=int, required=True, metavar="<n>",
                     help="the holdout size the operator chose, counting images already held out")
    cut.add_argument("--apply", action="store_true", help="record the proposal (the operator said apply)")
    add_common_args(cut)
    cut.set_defaults(func=_split)

    freeze = actions.add_parser("freeze", help="freeze a dataset version: --from <old> keeps the old members",
                                description="Freeze an immutable version: --from <old> = the old version's members "
                                            "plus every kept image in no snapshot yet; --only-new = only those. "
                                            "Default: the goal's from_version, the latest snapshot on an adopted "
                                            "project, else --only-new. Refuses on a leak or an uncaptioned train "
                                            "image.")
    freeze.add_argument("version", metavar="<version>")
    group = freeze.add_mutually_exclusive_group()
    group.add_argument("--from", dest="from_version", default=None, metavar="<old-version>")
    group.add_argument("--only-new", action="store_true")
    freeze.add_argument("--note", default=None, help="the operator's words for the version")
    add_common_args(freeze)
    freeze.set_defaults(func=_freeze)

    mat = actions.add_parser("materialize", help="write datasets/<version>/{training,holdout,_meta} from a snapshot",
                             description="Copy each member and its caption into the project's datasets/<version>/ "
                                         "(or --into <dir> inside the instance). Never writes under images_root; "
                                         "refuses a target that already holds files (then use --into <dir>, a "
                                         "fresh folder). `dataset check`, `next` and the preflight read the "
                                         "newest materialisation, wherever it went.")
    mat.add_argument("version", metavar="<version>")
    mat.add_argument("--into", type=Path, default=None, metavar="<dir>", help="a fresh folder inside the instance")
    add_common_args(mat)
    mat.set_defaults(func=_materialize)

    chk = actions.add_parser("check", help="leaks, and the materialised folder against its snapshot",
                             description="Check lineage leaks (the current split and every snapshot), then compare "
                                         "a materialised version with its snapshot: counts and one caption per "
                                         "image on both sides. --leaks checks leaks only.")
    chk.add_argument("version", nargs="?", default=None, metavar="<version>",
                     help="the version to compare; default: the latest snapshot")
    chk.add_argument("--leaks", action="store_true", help="only the lineage-leak check")
    chk.add_argument("--into", type=Path, default=None, metavar="<dir>",
                     help="the folder it was materialised into; default: the newest materialisation of the version")
    add_common_args(chk)
    chk.set_defaults(func=_check)

    cen = actions.add_parser("census", help="images per tier; --split: members per snapshot and split",
                             description="The reference census per tier (count, short side under 768 px, unknown "
                                         "dimensions); with --split the members per snapshot and split, and the "
                                         "current split of the kept images.")
    cen.add_argument("--split", dest="by_split", action="store_true")
    add_common_args(cen)
    cen.set_defaults(func=_census)


@handled
def _split(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    if args.holdout < 0:
        raise model.ProjectError(2, "--holdout is a number of images, 0 or more")
    if args.apply:
        with project.lock():  # the proposal and the lines it writes see the same images
            return _split_report(project, args, split.propose(project, args.holdout))
    return _split_report(project, args, split.propose(project, args.holdout))


def _split_report(project: model.Project, args: argparse.Namespace, proposal: split.Proposal) -> int:
    diff = proposal.holdout - args.holdout
    off = "as asked" if diff == 0 else f"{abs(diff)} {'above' if diff > 0 else 'below'} the asked {args.holdout}"
    print(f"split proposal for {project.slug}: {proposal.train} train, {proposal.holdout} holdout "
          f"({off}; {proposal.lineages} lineages kept whole)")
    print(f"  places {len(proposal.sides)} unassigned image(s); new holdout: "
          + (" ".join(proposal.new_holdout) or "none"))
    if not args.apply:
        print("dry run: nothing written; add --apply after the operator's yes")
        return 0
    n = split.apply(project, proposal)
    print(f"applied: {n} image(s) assigned")
    print("next: kiln dataset check --leaks")
    return 0


@handled
def _freeze(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    plan, record = snapshot.freeze(project, args.version, from_version=args.from_version, only_new=args.only_new,
                                   notes=args.note)
    if record is None:
        for line in plan.refusals():
            print(line)
        return 2
    c = record["counts"]
    origin = f"{len(plan.old)} from {plan.from_version} + {len(plan.new)} new" if plan.from_version \
        else f"{len(plan.new)} new"
    print(f"frozen {record['id']}: {len(record['members'])} members ({origin})")
    print(f"  mode: {plan.why_mode}")
    print(f"  train {c['train']}, holdout {c['holdout']}; members_sha256 {record['members_sha256'][:12]}")
    if plan.joined_train:
        print(f"  note: {len(plan.joined_train)} new kept image(s) had no split (unassigned), so they joined train: "
              + " ".join(plan.joined_train))
        print("    to hold some out, run `kiln dataset split --holdout <n> --apply` before the next freeze")
    skipped = [] if args.only_new else snapshot.left_out(project, plan)
    if skipped:
        print(f"  note: no --from given, so this native project froze only the images in no snapshot yet "
              f"(--only-new); the members of {', '.join(skipped)} are not in {args.version}")
        print("    to keep an old version's members, freeze a new version with --from <old-version>")
    if plan.holdout_uncaptioned:
        print(f"  warn: {len(plan.holdout_uncaptioned)} holdout image(s) without a caption: "
              + " ".join(plan.holdout_uncaptioned))
    if plan.no_longer_kept:
        print(f"  warn: {len(plan.no_longer_kept)} old member(s) are no longer kept, kept as in "
              f"{plan.from_version}: " + " ".join(plan.no_longer_kept))
    print(f"next: kiln dataset materialize {args.version}")
    return 0


@handled
def _materialize(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    written = materialize.materialize(project, args.version, args.into)
    dirs = written.record["directories"]
    print(f"materialised {written.record['snapshot_id']}")
    print(f"  train    {written.counts['train']} images + captions -> {dirs['train']}")
    print(f"  holdout  {written.counts['holdout']} images + captions -> {dirs['holdout']}")
    print(f"  record   {project.shown(written.path)}  sha256 {written.record['record_sha256_12']}")
    print("next: kiln dataset check " + args.version)
    return 0


@handled
def _check(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    found = check.leaks(project)
    for leak in found:
        print(leak.line())
    print(f"leaks: {len(found)}")
    if args.leaks:
        return 1 if found else 0
    version = args.version or snapshot.latest_version(project)
    if version is None:
        print("no frozen version yet: nothing materialised to compare")
        return 1 if found else 0
    folder = materialize.check_folder(project, version, args.into)
    if not folder.found:
        print(f"{project.slug}/{version}: not materialised ({project.shown(folder.base)}); "
              f"next: kiln dataset materialize {version}")
        return 1
    for note in folder.notes:
        print(f"{project.slug}/{version}: note: {note}")
    if folder.foreign:
        return 1 if found else 0
    for problem in folder.problems:
        print(f"{project.slug}/{version}: {problem}")
    c, t = folder.counts, folder.captions
    print(f"{project.slug}/{version}: {c.get('train', 0)} train, {c.get('holdout', 0)} holdout; captions "
          f"{t.get('train', 0) + t.get('holdout', 0)}/{c.get('train', 0) + c.get('holdout', 0)}; "
          + ("matches the snapshot" if folder.ok else f"{len(folder.problems)} problem(s)"))
    return 1 if (found or folder.problems) else 0


@handled
def _census(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    if args.by_split:
        rows = check.split_census(project)
        width = max([len(r.where) for r in rows] + [8])
        print(f"{'snapshot':<{width}}  {'split':<10}  n")
        for row in rows:
            print(f"{row.where:<{width}}  {row.split:<10}  {row.n}")
        return 0
    tiers = check.reference_census(project)
    print(f"{'tier':<10}  {'n':>4}  {'under_768':>9}  {'unknown_dims':>12}")
    for row in tiers:
        print(f"{row.tier:<10}  {row.n:>4}  {row.under_768_short_side:>9}  {row.unknown_dims:>12}")
    print(f"{'total':<10}  {sum(r.n for r in tiers):>4}  {sum(r.under_768_short_side for r in tiers):>9}  "
          f"{sum(r.unknown_dims for r in tiers):>12}")
    return 0
