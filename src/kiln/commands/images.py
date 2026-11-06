"""`kiln images list|label|assign|child|promote|import`: the argparse layer over `kiln.project.images`.

`images import` is the image sources' half that brings existing images in (`kiln.sources.files` for a folder,
`kiln.sources.easel_run` for the kept outputs of an easel run); every image is registered through
`kiln.project.images.register`.
"""

import argparse
import json
from pathlib import Path

from kiln.project import images, model
from kiln.project.model import DISPOSITIONS, ROOTS, SPLITS, TIERS, add_common_args, handled

HELP = "list, label, assign, import and promote dataset images"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("images", help=HELP, description=(
        "The project's images: list them, record the operator's labels, verdicts, splits and tiers, and register "
        "derived images as children of their parent. Images are never opened for content."))
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")

    show = actions.add_parser("list", help="one line per image: id, tier, disposition, split, root:file",
                              description="List the project's images (the latest record of each).")
    show.add_argument("--tier", choices=TIERS, default=None)
    show.add_argument("--disposition", choices=[*DISPOSITIONS, "none"], default=None,
                      help="only this disposition; none = no verdict yet")
    show.add_argument("--split", choices=SPLITS, default=None)
    show.add_argument("--root", choices=ROOTS, default=None)
    show.add_argument("--untriaged", action="store_true", help="only images without a verdict (legacy rows too)")
    show.add_argument("--json", action="store_true", help="one JSON record per line")
    add_common_args(show)
    show.set_defaults(func=_list)

    lab = actions.add_parser("label", help="record the operator's labels or verdict on one image",
                             description="Record what the operator says an image shows (labels, viewpoint, "
                                         "framing) or their verdict (--disposition). Never your own judgement.")
    lab.add_argument("image", metavar="<img-id>")
    lab.add_argument("--label", dest="add", action="append", default=[], metavar="<ns:value>",
                     help="add a namespaced label, for example outfit:default (repeatable)")
    lab.add_argument("--remove-label", dest="remove", action="append", default=[], metavar="<ns:value>")
    lab.add_argument("--viewpoint", choices=images.VIEWPOINTS, default=None)
    lab.add_argument("--framing", choices=images.FRAMINGS, default=None)
    lab.add_argument("--disposition", choices=DISPOSITIONS, default=None,
                     help="the operator's verdict; recorded with verdict_source operator")
    lab.add_argument("--note", default=None, help="the operator's words, verbatim")
    add_common_args(lab)
    lab.set_defaults(func=_label)

    put = actions.add_parser("assign", help="put images on a split: train, holdout, reference_only, unassigned",
                             description="Assign images to a split. A rejected image is never assigned to train or "
                                         "holdout; a lineage on both sides is named (`dataset check --leaks`).")
    put.add_argument("image_ids", nargs="+", metavar="<img-id>")
    put.add_argument("--split", choices=SPLITS, required=True)
    add_common_args(put)
    put.set_defaults(func=_assign)

    kid = actions.add_parser("child", help="register a derived image (crop, flip, edit, upscale) under its parent",
                             description="Register a file that lies in one of the project's image roots as a child "
                                         "of <parent-id>: it joins the parent's lineage, so a split keeps them on "
                                         "one side. edit and enhance mark synthetic_detail_added.")
    kid.add_argument("parent", metavar="<parent-id>")
    kid.add_argument("file", type=Path, metavar="<file>")
    kid.add_argument("--transform", choices=images.TRANSFORMS, required=True)
    kid.add_argument("--grid-cell", default=None, metavar="<x0,y0,x1,y1>", help="the cell, when cut from a grid")
    kid.add_argument("--note", default=None)
    add_common_args(kid)
    kid.set_defaults(func=_child)

    up = actions.add_parser("promote", help="change an image's trust tier",
                            description="Change an image's tier (CORE, SECONDARY, DIAGNOSTIC, CANDIDATE, REJECTED); "
                                        "the reason is kept in its notes.")
    up.add_argument("image", metavar="<img-id>")
    up.add_argument("--tier", choices=TIERS, required=True)
    up.add_argument("--reason", default=None, help="the operator's words")
    add_common_args(up)
    up.set_defaults(func=_promote)

    imp = actions.add_parser("import", help="register images from a folder or the kept outputs of an easel run",
                             description="--from-folder <dir>: register the folder's images as untriaged candidates "
                                         "(copied into the imports root unless they lie in a project root). "
                                         "--from-easel-run <run-id> --keep-only: copy the outputs the operator kept "
                                         "in that easel run (sha256 checked) into images/<run-id>/NNN.png with "
                                         "disposition ACCEPT_TRAIN, split unassigned and verdict_source "
                                         "easel-review:<run-id>. The same bytes twice are one image.")
    imp.add_argument("--from-folder", type=Path, default=None, metavar="<dir>")
    imp.add_argument("--notes", type=Path, default=None, metavar="<notes.csv>",
                     help="with --from-folder: a CSV with columns file, note (and optionally viewpoint, framing, "
                          "labels) in the operator's words")
    imp.add_argument("--kind", default="unknown", metavar="<kind>",
                     help="with --from-folder: where the files came from (source.kind): unknown, upload, scan, "
                          "local-gen, api, chatgpt, codex, generated; default unknown")
    imp.add_argument("--from-easel-run", default=None, metavar="<run-id>")
    imp.add_argument("--keep-only", action="store_true",
                     help="with --from-easel-run: only the outputs whose current verdict is keep (required)")
    imp.add_argument("--easel-home", type=Path, default=None, metavar="<dir>",
                     help="the easel instance; default [sources] easel_home, else $EASEL_HOME")
    add_common_args(imp)
    imp.set_defaults(func=_import)


def _row(record: dict) -> str:
    return (f"{record['id']}  {record.get('tier', ''):<10} {str(record.get('disposition') or '-'):<22} "
            f"{record.get('split', ''):<14} {record['root']}:{record['file']}"
            + ("  (legacy, untriaged)" if images.is_legacy_untriaged(record) else ""))


@handled
def _list(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    shown = []
    for record in images.records(project).values():
        if args.tier and record.get("tier") != args.tier:
            continue
        if args.disposition and (record.get("disposition") or "none") != args.disposition:
            continue
        if args.split and record.get("split") != args.split:
            continue
        if args.root and record.get("root") != args.root:
            continue
        if args.untriaged and not images.is_untriaged(record):
            continue
        shown.append(record)
    for record in sorted(shown, key=lambda r: (r["root"], r["file"])):
        print(json.dumps(record, ensure_ascii=False) if args.json else _row(record))
    if not args.json:
        kept = sum(images.is_kept(r) for r in shown)
        untriaged = sum(images.is_untriaged(r) for r in shown)
        print(f"{len(shown)} images ({kept} kept, {untriaged} without a verdict)")
    return 0


@handled
def _label(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    line = images.label(project, args.image, by="kiln images label", add=args.add, remove=args.remove,
                        viewpoint=args.viewpoint, framing=args.framing, disposition=args.disposition,
                        note=args.note)
    said = [f"{k} {line[k]}" for k in ("disposition", "viewpoint", "framing") if getattr(args, k) is not None]
    if args.add or args.remove:
        said.append("labels " + (" ".join(line.get("labels") or []) or "none"))
    if args.note is not None:
        said.append("note")
    print(f"{args.image}: {', '.join(said)}")
    return 0


@handled
def _assign(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    for image_id in args.image_ids:
        with project.lock():  # the read and the assign line belong together
            recs = images.records(project)
            old = images.get(recs, image_id).get("split")
            _, warnings = images.assign(project, image_id, args.split, by="kiln images assign", images=recs)
        print(f"{image_id}: split {old} -> {args.split}")
        for line in warnings:
            print(f"  {line}")
    return 0


@handled
def _child(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    cell = None
    if args.grid_cell:
        try:
            cell = [int(x) for x in args.grid_cell.split(",")]
        except ValueError:
            cell = []
        if len(cell) != 4 or min(cell) < 0:
            raise model.ProjectError(2, f"--grid-cell is four whole numbers x0,y0,x1,y1: {args.grid_cell!r}")
    record, created = images.child(project, args.parent, args.file, args.transform, by="kiln images child",
                                   grid_cell=cell, notes=args.note)
    if not created:
        print(f"already registered: {record['id']} ({record['root']}:{record['file']}); nothing written")
        return 0
    lin = record["lineage"]
    print(f"{record['id']}  {record.get('width')}x{record.get('height')}  <- {args.parent}  ({args.transform})")
    print(f"  lineage root {lin['root']}" + ("  synthetic_detail_added 1" if lin["synthetic_detail_added"] else ""))
    print("  needs the operator's verdict: kiln images label " + record["id"] + " --disposition <verdict>")
    return 0


@handled
def _promote(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    old, _ = images.promote(project, args.image, args.tier, by="kiln images promote", reason=args.reason)
    print(f"{args.image}: tier {old} -> {args.tier}")
    return 0


@handled
def _import(args: argparse.Namespace) -> int:
    if (args.from_folder is None) == (args.from_easel_run is None):
        raise model.ProjectError(2, "name one source: --from-folder <dir> or --from-easel-run <run-id> --keep-only")
    if args.from_folder is not None and (args.keep_only or args.easel_home is not None):
        raise model.ProjectError(2, "--keep-only and --easel-home go with --from-easel-run")
    if args.from_easel_run is not None and (args.notes is not None or args.kind != "unknown"):
        raise model.ProjectError(2, "--notes and --kind go with --from-folder")
    project = model.open_project(args)
    if args.from_folder is not None:
        return _import_folder(project, args)
    return _import_easel(project, args)


def _import_folder(project: model.Project, args: argparse.Namespace) -> int:
    from kiln.sources import files

    result = files.import_folder(project, args.from_folder, notes=args.notes, kind=args.kind)
    for line in result.registered:
        print(f"{line['id']}  {line.get('width')}x{line.get('height')}  {line['root']}:{line['file']}"
              + ("  (note)" if line.get("notes") else ""))
    for name, record in result.already:
        print(f"already registered: {name} is {record['id']}; nothing written")
    for name in result.notes_unused:
        print(f"note unused: {name} is not an image of the folder")
    for problem in result.problems:
        print(f"problem: {problem}")
    print(f"{len(result.registered)} images registered from {args.from_folder.name} (untriaged); "
          f"{len(result.already)} already registered, {len(result.problems)} problem(s)")
    if result.registered:
        print("next: kiln review sheet (the operator gives each image a verdict)")
    return 1 if result.problems else 0


def _import_easel(project: model.Project, args: argparse.Namespace) -> int:
    from kiln.sources import easel_run

    run_id = args.from_easel_run
    result = easel_run.import_run(project, run_id, home=args.easel_home, keep_only=args.keep_only)
    for line in result.registered:
        src = line["source"]
        checks = sum(1 for lab in line.get("labels") or [] if lab.startswith("check:"))
        print(f"{line['id']}  {line['root']}:{line['file']}  output {src.get('n')}  {checks} check(s) passed"
              + ("  (note)" if line.get("notes") else ""))
    for n, record in result.already:
        print(f"already registered: output {n} is {record['id']} (disposition {record.get('disposition')}); "
              "left as it is")
    for problem in result.problems:
        print(f"problem: {problem}")
    print(f"{len(result.registered)} images registered as ACCEPT_TRAIN (verdicts from easel run {run_id})")
    print(f"  the run's verdicts: {result.kept} keep, {result.rejected} reject, {result.undecided} undecided; "
          "only kept outputs are imported")
    if result.kept == 0:
        print("  no kept output: check `easel runs reviews --current` in the easel instance; on an adopted host "
              "the reviews come from `easel runs import-legacy <id> --with-reviews`")
        return 1
    if result.registered:
        print("next: kiln next")
    return 1 if result.problems else 0
