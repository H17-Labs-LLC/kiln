"""`kiln train plan|preflight`: the argparse layer over `kiln.train` (plan, legacy_emit, preflight).

`train plan` writes `runs/<run-id>/run.json` (status planned) and starts nothing; with `--emit-legacy` it also
renders the host workspace's launcher and dataset config, writes the host queue file and prints the two host
lines the operator's go unlocks. `train preflight` prints one line per gate and `next:`. kiln's own launcher,
queue, smoke and tune verbs come in a later session.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kiln.project.model import TOOL, add_common_args, handled, open_project

HELP = "plan and preflight a training run; launch, queue, smoke and tune come later"


def _positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"a whole number of steps, not {text!r}") from None
    if value < 1:
        raise argparse.ArgumentTypeError("a number of steps, 1 or more")
    return value


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("train", help=HELP, description=(
        "Plan and check a training run. Training is GPU work: these verbs write and check records; they never "
        "start a trainer. On an adopted instance the host lines `train plan --emit-legacy` prints wait for the "
        "operator's go."))
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")

    plan = actions.add_parser(
        "plan", help="write runs/<run-id>/run.json (planned): base and its licence, continue_from, steps",
        description="Plan a run on a frozen dataset version: copies the base's licence block into run.json, "
                    "records the LoRA it continues from (sd-scripts --network_weights on the native path), and "
                    "prints the wall clock and the host RAM it waits for. Starts nothing.")
    plan.add_argument("version", metavar="<version>", help="the frozen dataset version, for example 0.3.0")
    plan.add_argument("--family", required=True, metavar="<family>", help="the model family (families/<id>.yaml)")
    base = plan.add_mutually_exclusive_group(required=True)
    base.add_argument("--base", metavar="<sha16|path>", default=None,
                      help="the base checkpoint: a resource id or the file's path")
    base.add_argument("--base-from-run", metavar="<run-id>", default=None,
                      help="copy the base (sha256, label, licence) from an earlier run (kiln runs list)")
    plan.add_argument("--continue-from", metavar="<lora sha16|path>", default=None,
                      help="start from an existing LoRA's weights instead of fresh (the operator's choice)")
    plan.add_argument("--steps", required=True, type=_positive, metavar="<n>", help="training steps")
    plan.add_argument("--save-every", type=_positive, default=None, metavar="<n>",
                      help="save a checkpoint every n steps (default: the family's)")
    plan.add_argument("--validate-every", type=_positive, default=None, metavar="<n>",
                      help="validation loss every n steps (default: the save interval)")
    plan.add_argument("--eval-suite", default=None, metavar="<id>",
                      help="the eval suite (evals/suites/<id>.yaml; default: the project's only one)")
    plan.add_argument("--label", default=None, help="a short label for the run, for example v3")
    plan.add_argument("--note", default=None, help="the operator's words about the run")
    plan.add_argument("--emit-legacy", action="store_true",
                      help="adopted instance: render the host's launcher and dataset config, write its queue file "
                           "and print the host lines ([legacy] in kiln.toml)")
    plan.add_argument("--legacy-run-id", default=None, metavar="<id>",
                      help="with --emit-legacy: use this host run id instead of the next free one")
    plan.add_argument("--legacy-root", type=Path, default=None, metavar="<dir>",
                      help="with --emit-legacy: the host workspace root instead of [legacy] root (a scratch copy)")
    add_common_args(plan)
    plan.set_defaults(func=_plan)

    pre = actions.add_parser(
        "preflight", help="every gate before a run starts, one line each, then next:",
        description="Check the gates of a training round on <version>: snapshot, triage, captions, split, leaks, "
                    "materialised, disk, RAM, a planned run; on an adopted instance also the launcher and the "
                    "queue (validated by check_queue); last the operator's go (WAIT until they say \"train\"). "
                    "Changes nothing. Exit 0 when every gate but the operator's go is ok.")
    pre.add_argument("version", metavar="<version>")
    pre.add_argument("--family", default=None, metavar="<family>", help="default: the planned run's family")
    pre.add_argument("--run", dest="run_id", default=None, metavar="<run-id>",
                     help="default: the newest planned or running run on <version>")
    pre.add_argument("--legacy-root", type=Path, default=None, metavar="<dir>",
                     help="the host workspace root instead of [legacy] root")
    pre.add_argument("--json", action="store_true", help="the gates as JSON")
    add_common_args(pre)
    pre.set_defaults(func=_preflight)

    add_planned(actions, "launch", "session-2", "start a planned run through the scheduler (native instance)")
    add_planned(actions, "queue", "session-2", "run queued training jobs and their eval renders, one at a time")
    add_planned(actions, "smoke", "session-2", "a 4-step smoke run that proves the trainer starts")
    add_planned(actions, "tune", "session-2", "measure batch and resolution limits on this machine")


def add_planned(actions, name: str, task: str, help_text: str) -> None:
    """A subcommand that is not built yet: it prints `planned: <task> (<help>)` and exits 2 (the CLI convention;
    `qa docs` counts a command that names it as planned, not as a finding)."""
    # No option prefix: every word after the name (`--help` and flags included) is plain text, so any call of a
    # planned subcommand prints the planned line, as an unbuilt top-level verb does.
    sub = actions.add_parser(name, help=f"{help_text} [planned: {task}]", description=f"Not built yet ({task}).",
                             prefix_chars="\x00", add_help=False)
    sub.add_argument("rest", nargs="*", help=argparse.SUPPRESS)
    sub.set_defaults(func=_planned, planned=task, planned_help=help_text)


def _planned(args: argparse.Namespace) -> int:
    print(f"planned: {args.planned} ({args.planned_help})")
    return 2


@handled
def _plan(args: argparse.Namespace) -> int:
    from kiln._common import ids
    from kiln.train import legacy_emit
    from kiln.train import plan as plan_mod
    from kiln.train import runs as runs_mod

    if (args.legacy_run_id or args.legacy_root) and not args.emit_legacy:
        print("--legacy-run-id and --legacy-root go with --emit-legacy")
        return 2
    project = open_project(args)
    # A checkpoint of a run the adopted host trained is recorded relative to the host root.
    host_root = runs_mod.legacy_root_of(project, args.legacy_root)
    prepared = plan_mod.prepare(
        project, args.version, family=args.family, base=args.base, base_from_run=args.base_from_run,
        continue_from=args.continue_from, steps=args.steps, save_every=args.save_every,
        validate_every=args.validate_every, eval_suite=args.eval_suite, label=args.label, note=args.note,
        legacy_root=host_root)
    checked = None
    if args.emit_legacy:
        checked = legacy_emit.precheck(prepared, project.instance.cfg, legacy_root=args.legacy_root,
                                       legacy_run_id=args.legacy_run_id)
    run_id, run_dir = ids.claim_seq_dir(project.runs_dir, "run", TOOL)
    emitted = None
    try:
        bad = plan_mod.problems(plan_mod.record(prepared, run_id))  # before anything reaches the host folders
        if bad:
            raise plan_mod.ProjectError(2, ["REFUSED: the planned record does not match schemas/run.schema.json "
                                            "(a bug in kiln; nothing was written)", *("  " + b for b in bad[:10])])
        if checked is not None:
            emitted = legacy_emit.emit(prepared, checked, kiln_run=run_id, run_dir=run_dir)
        data = plan_mod.record(prepared, run_id, legacy=emitted.record if emitted else None)
        path = plan_mod.write(prepared, run_dir, data)
    except BaseException:
        if emitted is not None:
            legacy_emit.undo(emitted, run_dir)
        _drop_empty(run_dir)
        raise
    for line in plan_mod.summary_lines(prepared, data, path):
        print(line)
    if emitted is not None:
        for line in emitted.lines:
            print(line)
    print(f"next: kiln train preflight {args.version}")
    return 0


def _drop_empty(folder: Path) -> None:
    """Give a claimed run id back when nothing was written into its folder."""
    try:
        folder.rmdir()
    except OSError:
        pass


@handled
def _preflight(args: argparse.Namespace) -> int:
    from kiln.train import preflight

    project = open_project(args)
    ctx = preflight.context(project, args.version, family=args.family, run_id=args.run_id,
                            legacy_root=args.legacy_root)
    results = preflight.gates(ctx)
    if args.json:
        print(json.dumps({"project": project.slug, "version": args.version,
                          "run": (ctx.run or {}).get("id"), "adopted": ctx.adopted,
                          "gates": [g.as_dict() for g in results], "next": preflight.next_line(results)[6:]},
                         indent=1, ensure_ascii=False))
        return preflight.exit_code(results)
    run = (ctx.run or {}).get("id") or "none planned"
    mode = ", adopted: host launcher and queue checked" if ctx.adopted else ""
    print(f"preflight {ctx.snapshot_id} (run {run}, family {ctx.family}{mode})")
    for gate in results:
        print(gate.line())
    print(preflight.next_line(results))
    return preflight.exit_code(results)
