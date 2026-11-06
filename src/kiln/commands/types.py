"""`kiln types check [<id>]`: the argparse layer over `kiln.types_check`, which holds the rules."""

import argparse
from pathlib import Path

HELP = "check the LoRA type folders: types check"


def register(subparsers: argparse._SubParsersAction) -> None:
    """Add `types` and its one action, `check`."""
    parser = subparsers.add_parser(
        "types",
        help=HELP,
        description="Check the LoRA type folders under types/: the type files are present and schema-valid.",
    )
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")
    check = actions.add_parser(
        "check",
        help="check every type folder, or one: files present, schema-valid, dataset variables, card slot",
        description=(
            "Check every type folder under types/, or only <id>. Prints one line per problem and exits 1 "
            "when there is any; warnings fail only with --strict."
        ),
    )
    check.add_argument("type_id", nargs="?", metavar="<id>", help="one type id, for example character-anime")
    check.add_argument("--dir", type=Path, default=None, metavar="<folder>",
                       help="the folder that holds the type folders; default: types/ of this checkout")
    check.add_argument("--schemas", type=Path, default=None, metavar="<folder>",
                       help="the folder that holds the schemas; default: schemas/ of this checkout")
    check.add_argument("--strict", action="store_true", help="treat warnings as problems")
    check.set_defaults(func=_check)


def _check(args: argparse.Namespace) -> int:
    from kiln import types_check

    return types_check.run(args.type_id, types_dir=args.dir, schemas_dir=args.schemas, strict=args.strict)
