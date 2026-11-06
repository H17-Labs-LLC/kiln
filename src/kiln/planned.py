"""Does a kiln command line name a verb or subverb that is not built yet?

A verb is planned while its module is missing (`kiln <verb>` prints `planned: <task> (<help>)` and exits 2); a
subverb of a built verb is planned when its parser carries `set_defaults(planned="<task>")`. Whatever kiln prints
as a command to run next goes through `mark()`, so a planned step always ends with `# planned: <task>` and an agent
reads it as "not built: report it once and stop at that stage" instead of running it.
"""

from __future__ import annotations

import argparse

from . import cli


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    for action in parser._actions:  # argparse keeps no public list of a parser's subcommands
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def task_of(command: str) -> str | None:
    """The task that builds what `command` names (`kiln <verb> [<subverb> ...] ...`), or None when it is built.

    Words after the verb are followed while they name subcommands; an option, a `<placeholder>` or any other
    word ends the walk. A line that is not a kiln command is never planned.
    """
    words = command.split()
    if len(words) < 2 or words[0] != cli.PKG:
        return None
    cmd = next((c for c in cli.COMMANDS if c.verb == words[1]), None)
    if cmd is None:
        return None
    if not cli.module_exists(cmd.module):
        return cmd.planned
    try:
        _, sub = cli.build_parser(cmd.verb)
    except Exception:  # noqa: BLE001 - a verb that fails to register is reported by its own run, not here
        return None
    node = sub.choices[cmd.verb]
    for word in words[2:]:
        actions = _subparsers(node)
        if actions is None or word not in actions.choices:
            break
        node = actions.choices[word]
    task = node.get_default("planned")
    return task if isinstance(task, str) and task else None


def mark(command: str) -> str:
    """`command`, ending with `  # planned: <task>` when it names something not built yet."""
    task = task_of(command)
    return f"{command}  # planned: {task}" if task else command


def verb_task(verb: str) -> str | None:
    """The task that builds the top-level verb `verb`, or None when its module exists (or no such verb)."""
    cmd = next((c for c in cli.COMMANDS if c.verb == verb), None)
    if cmd is None or cli.module_exists(cmd.module):
        return None
    return cmd.planned
