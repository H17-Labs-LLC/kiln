"""`kiln doctor`: check the install and the instance; one ok|warn|FAIL line per check, then `next:`.

Exit 0 when no line is a FAIL (warn lines are reported, they do not block), 1 otherwise. It only reads: it never
runs the trainer, starts a process, writes a record or changes the configuration. The checks are in
`kiln.doctor`.
"""

from __future__ import annotations

import argparse

from kiln._common.config import add_workspace_arg

HELP = "check the install and the instance: ok|warn|FAIL per line, then next:"


def register(subparsers: argparse._SubParsersAction) -> None:
    """Add `doctor`."""
    parser = subparsers.add_parser(
        "doctor", help=HELP,
        description="Check the install (version, shipped families and types), the instance file (every ${VAR} "
                    "set), its folders, the trainer runtime ([train] python or venv_dir, sd_scripts), the "
                    "scheduler, the free disk against limits.disk_floor_gb, the [legacy] keys of an adopted "
                    "instance, the policy and the projects. On an adopted instance a missing host trainer, models "
                    "folder or image folder is a warn, never a FAIL. Exit 0 without a FAIL line, 1 with one.")
    add_workspace_arg(parser)
    parser.set_defaults(func=_doctor)


def _doctor(args: argparse.Namespace) -> int:
    from kiln import doctor

    code, lines = doctor.report(args.tool, args.workspace)
    for line in lines:
        print(line)
    return code
