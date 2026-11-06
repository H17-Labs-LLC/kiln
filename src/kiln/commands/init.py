"""`kiln init <dir>`: the argparse layer over `kiln.init`, which holds the rules and writes the files."""

from __future__ import annotations

import argparse
import sys

HELP = "create a kiln home: kiln.toml, projects/, journal/, runtime/"


def register(subparsers: argparse._SubParsersAction) -> None:
    """Add `init`."""
    parser = subparsers.add_parser(
        "init", help=HELP,
        description="Create a kiln home: kiln.toml (from kiln.example.toml), the projects/, journal/ and runtime/ "
                    "folders, a .gitignore that ignores everything but README.md, and a README. Refuses a folder "
                    "inside a repository checkout, a folder that already holds kiln.toml, and any other non-empty "
                    "folder unless --force-existing. Exit 0 when created, 2 when refused.")
    parser.add_argument("dir", metavar="<dir>", help="the instance folder to create, for example ~/kiln-home")
    parser.add_argument("--force-existing", action="store_true",
                        help="add the instance files to a non-empty folder that holds no kiln.toml yet; no "
                             "existing file is overwritten")
    parser.set_defaults(func=_init)


def _init(args: argparse.Namespace) -> int:
    from kiln import init

    try:
        lines = init.init(args.dir, force_existing=args.force_existing)
    except init.InitError as exc:
        for line in exc.lines:
            print(line, file=sys.stderr)
        return exc.code
    for line in lines:
        print(line)
    return 0
