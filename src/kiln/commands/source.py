"""`kiln source run <jobs.json> [--dry-run]`: the argparse layer over `kiln.sources.jobs`."""

import argparse
from pathlib import Path

from kiln.project import model
from kiln.project.model import add_common_args, handled
from kiln.sources import jobs

HELP = "run an image source from a jobs file: files, codex, comfy, easel-run"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("source", help=HELP, description=(
        "Image sources behind one interface. A jobs file (schemas/jobs-file.schema.json) says per image what is "
        "asked and why; its `source` names who makes it: codex (the Codex CLI's image tool), comfy (planned). "
        "Folders and easel runs are imported with `kiln images import`."))
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")

    go = actions.add_parser("run", help="run a jobs file; --dry-run shows what it would send",
                            description="Resolve every job of <jobs.json> (references, parent, the composed prompt, "
                                        "the count). --dry-run prints it and sends nothing. Without --dry-run, "
                                        "only after the operator's go: each job's images are copied into the jobs "
                                        "file's out_dir and registered as untriaged candidates, with a generation "
                                        "manifest per job. Exit 3 when the source's quota stops the batch, 1 when "
                                        "a job brought no image.")
    go.add_argument("jobs_file", type=Path, metavar="<jobs.json>",
                    help="the jobs file: as given, or relative to the project folder")
    go.add_argument("--dry-run", action="store_true", help="print what would be sent; write nothing")
    add_common_args(go)
    go.set_defaults(func=_run)


@handled
def _run(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    report = jobs.dry_run(project, args.jobs_file) if args.dry_run else jobs.run(project, args.jobs_file)
    for line in report.lines:
        print(line)
    return report.code
