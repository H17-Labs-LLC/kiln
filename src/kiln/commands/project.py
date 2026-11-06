"""`kiln project new|lint|show|goal set|goal clear|intake show|intake answer`: the argparse layer over
`kiln.project`."""

import argparse
from pathlib import Path

from kiln._common import config
from kiln.project import captions, goal, images, intake, lint, model, new, snapshot, status
from kiln.project.model import add_common_args, handled

HELP = "create and inspect a project: new, lint, show, goal set|clear, intake show|answer"


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("project", help=HELP, description=(
        "Create a project from a LoRA type, check it, show it, and set or clear the goal `kiln next` routes by. "
        "Every action works on --project <slug>, or on the instance's only project."))
    actions = parser.add_subparsers(dest="action", metavar="<action>", title="actions")

    make = actions.add_parser("new", help="create projects/<slug>/ from types/<type>/",
                              description="Create projects/<slug>/ from a shipped LoRA type: the intake, caption "
                                          "rules, checklist, eval suite, dataset template, thresholds, card template "
                                          "and spec skeleton are copied; project.yaml and state.json are written.")
    make.add_argument("slug", help="lowercase letters, digits and underscores, for example demo_robot")
    make.add_argument("--type", dest="type_id", required=True, metavar="<type>",
                      help="character-anime, character-human or style")
    make.add_argument("--title", default=None, help="the project's title; default: from the slug")
    make.add_argument("--family", dest="families", action="append", default=None, metavar="<id>",
                      help="a model family to train for (repeatable); default: the intake's families default")
    make.add_argument("--types-dir", type=Path, default=None, metavar="<folder>",
                      help="the folder that holds the type folders; default: types/ of this checkout")
    config.add_workspace_arg(make)
    make.set_defaults(func=_new)

    check = actions.add_parser("lint", help="check the project's files: schemas, image files, intake answers",
                               description="Check the project folder: every record and log line against its schema, "
                                           "every image file present, every required intake question answered. "
                                           "Exit 1 when a problem is found.")
    add_common_args(check)
    check.set_defaults(func=_lint)

    show = actions.add_parser("show", help="print the project: type, trigger, roots, goal, counts, snapshots, runs",
                              description="Print the project's summary: type, trigger, families, roots, the goal, "
                                          "image and caption counts, the frozen snapshots and the runs.")
    add_common_args(show)
    show.set_defaults(func=_show)

    aim = actions.add_parser("goal", help="set or clear the goal kiln next routes by",
                             description="The goal is the person's intent for the next dataset version; with a "
                                         "goal set, kiln next routes 03 -> 05 -> 06 -> 07 -> 08 for it.")
    aims = aim.add_subparsers(dest="goal_action", metavar="<action>", title="actions")
    put = aims.add_parser("set", help="set the goal: a new version, from an old one and an easel run",
                          description="Set the goal. --from-version defaults to the latest snapshot; --only-new "
                                      "sets none.")
    put.add_argument("--new-version", required=True, metavar="<version>", help="the version to build, e.g. 0.3.0")
    put.add_argument("--from-version", default=None, metavar="<version>",
                     help="the version whose members the new one keeps; default: the latest snapshot")
    put.add_argument("--only-new", action="store_true", help="keep no earlier version's members")
    put.add_argument("--from-easel-run", default=None, metavar="<run-id>",
                     help="the easel run whose kept outputs the version adds")
    put.add_argument("--note", default=None, help="the operator's words for the goal")
    add_common_args(put)
    put.set_defaults(func=_goal_set)
    clear = aims.add_parser("clear", help="clear the goal",
                            description="Clear the goal; kiln next then derives the stage from the files.")
    add_common_args(clear)
    clear.set_defaults(func=_goal_clear)

    ask = actions.add_parser("intake", help="show the intake questions; record the operator's dictated answers",
                             description="The intake is the operator's. `show` lists every question with its id and "
                                         "state; `answer` records the operator's words verbatim (never your own) "
                                         "with a note of who dictated them. Never edit intake.yaml by hand.")
    asks = ask.add_subparsers(dest="intake_action", metavar="<action>", title="actions")
    see = asks.add_parser("show", help="every question: id, state, the question; --open: only unanswered ones",
                          description="List the intake questions in order: number, id, state (answered, MISSING "
                                      "(required), open (the default applies)) and the question to ask.")
    see.add_argument("--open", dest="only_open", action="store_true", help="only the questions without an answer")
    add_common_args(see)
    see.set_defaults(func=_intake_show)
    say = asks.add_parser("answer", help='record the operator\'s answer: answer <question-id> "<words>"',
                          description="Write the operator's dictated answer into intake.yaml verbatim, through the "
                                      "store, and mark the question's note with who dictated it and when. A list "
                                      "question (families) takes one item per argument; a question with choices "
                                      "takes one of them.")
    say.add_argument("question", metavar="<question-id>", help="the question's id (or its number), from `show`")
    say.add_argument("words", nargs="+", metavar="<words>", help="the operator's words, quoted as one argument")
    say.add_argument("--by", default="operator", metavar="<who>",
                     help="who dictated the answer (default: operator); never the assistant")
    add_common_args(say)
    say.set_defaults(func=_intake_answer)


@handled
def _new(args: argparse.Namespace) -> int:
    instance = model.Instance.load(args.workspace)
    folder, data, copied = new.create(instance, args.slug, args.type_id, title=args.title,
                                      families=args.families, types_dir=args.types_dir)
    print(f"created project {args.slug} ({args.type_id}) in {instance.shown(folder)}")
    print(f"  copied: {' '.join(copied)}")
    print(f"  families: {', '.join(data['families'])}")
    print(f"  roots: images {data['roots']['images'] or 'none'}, imports {data['roots']['imports']}")
    print("next: ask the operator each question of `kiln project intake show`; record each reply with "
          'kiln project intake answer <question-id> "<words>"; then kiln project lint')
    return 0


@handled
def _lint(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    report = lint.lint(project)
    for line in report.problems:
        print(line)
    for line in report.warnings:
        print(f"warn: {line}")
    for line in report.notes:
        print(f"note: {line}")
    n, w = len(report.problems), len(report.warnings)
    print(f"lint {project.slug}: {n} problem{'' if n == 1 else 's'}, {w} warning{'' if w == 1 else 's'}")
    return 1 if report.problems else 0


@handled
def _show(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    data = project.data
    state = project.state()
    recs = images.records(project)
    kept = captions.training_images(recs)
    approved = captions.status(project)["approved"]
    roots = data.get("roots") or {}
    print(f"project: {project.slug} ({data.get('title')})")
    print(f"type: {data.get('type')}")
    print(f"trigger: {data.get('trigger') or 'none yet'}")
    print(f"families: {', '.join(data.get('families') or [])}")
    print(f"adopted: {'yes' if project.adopted else 'no'}")
    print(f"status: {state.get('status')}")
    print(f"goal: {status.goal_text(state.get('goal'))}")
    print("roots: " + ", ".join(f"{k} {roots.get(k) or 'none'}" for k in model.ROOTS))
    untriaged = sum(1 for r in recs.values() if images.is_untriaged(r) and not images.is_legacy_untriaged(r))
    legacy = sum(1 for r in recs.values() if images.is_legacy_untriaged(r))
    print(f"images: {len(recs)} (kept {len(kept)}, untriaged {untriaged}, legacy untriaged {legacy})")
    print(f"captions: {len(approved)} approved of {len(kept)} kept")
    found = snapshot.versions(project)
    print(f"snapshots: {len(found)}")
    for version in found:
        snap = snapshot.load(project, version)
        c = snap["counts"]
        origin = f", from {snap['from_version']}" if snap.get("from_version") else ""
        print(f"  {version}  {len(snap['members'])} members ({c['train']} train, {c['holdout']} holdout{origin})")
    runs = status.runs(project)
    print(f"runs: {len(runs)}")
    for r in runs:
        print(f"  {r['id']}  {r['status']}  {r.get('family')}  on {r.get('snapshot') or 'no snapshot'}")
    return 0


@handled
def _goal_set(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    value = goal.set_goal(project, args.new_version, from_version=args.from_version,
                          from_easel_run=args.from_easel_run, note=args.note, only_new=args.only_new)
    print(f"goal set for {project.slug}: {status.goal_text(value)}")
    print("next: kiln next")
    return 0


@handled
def _intake_show(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    found = intake.questions(project)
    shown = [q for q in found if not (args.only_open and q.answer is not None)]
    for q in shown:
        print(f"{q.n}  {q.id}  {q.state}")
        print(f"    {q.text}")
        if q.choices:
            print(f"    choices: {', '.join(q.choices)}")
        elif q.takes_list:
            print("    a list: one item per argument")
    missing = sum(q.answer is None and q.required and q.applies for q in found)
    answered = sum(q.answer is not None for q in found)
    print(f"intake {project.slug}: {answered} of {len(found)} answered, {missing} required still missing")
    if missing or (args.only_open and shown):
        print('next: ask the operator; then kiln project intake answer <question-id> "<words>"')
    else:
        print("next: kiln project lint")
    return 0


@handled
def _intake_answer(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    question, value = intake.answer(project, args.question, args.words, by=args.by)
    shown = ", ".join(value) if isinstance(value, list) else value
    print(f"intake {question.n} {question.id}: answered ({shown}), dictated by {args.by.strip()}")
    print("next: kiln project intake show --open")
    return 0


@handled
def _goal_clear(args: argparse.Namespace) -> int:
    project = model.open_project(args)
    old = goal.clear_goal(project)
    print(f"goal cleared for {project.slug} (was: {status.goal_text(old)})")
    print("next: kiln next")
    return 0
