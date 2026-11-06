"""`kiln next` never sends an agent to a file or a verb that is not there: a stage whose runbook is not written
prints `read: <id> is planned`, and a next command that names a verb or subverb not built yet ends with
`# planned: <task>`."""

from pathlib import Path

import pytest
from kiln_project_helpers import REPO, run

from kiln import cli, planned
from kiln import next as next_mod

RUNBOOKS = REPO / "docs" / "runbooks"


def test_the_written_runbooks_list_matches_the_folder():
    on_disk = {p.stem for p in RUNBOOKS.glob("*.md") if p.stem != "README"}
    assert on_disk == set(next_mod.WRITTEN_RUNBOOKS), (
        "docs/runbooks/ changed: update kiln.next.WRITTEN_RUNBOOKS (it answers for an installed package)")
    assert set(next_mod.WRITTEN_RUNBOOKS) <= set(next_mod.FIRST_COMMAND)


def test_a_planned_runbook_is_named_as_planned():
    stage = next_mod.Stage(stage="04-vetting", next="kiln review sheet", why="test")
    if (RUNBOOKS / "04-vetting.md").is_file():
        pytest.skip("runbook 04 is written now")
    assert stage.read == "04-vetting is planned" and stage.runbook is None
    assert stage.lines()[1] == "read: 04-vetting is planned"
    written = next_mod.Stage(stage="05-captions", next="kiln captions status", why="test")
    assert written.lines()[1] == "read: docs/runbooks/05-captions.md"


@pytest.mark.parametrize(("command", "task"), [
    ("kiln eval --help", "session-2"),                        # a verb whose module does not exist
    ("kiln train launch run-20300101-001", "session-2"),      # a placeholder subverb of a built verb
    ("kiln runs leaderboard --family illustrious", "session-2"),
    ("kiln review readout", "session-2"),
    ("kiln review sheet", None),                              # built
    ("kiln images import --from-easel-run run-20300101-001 --keep-only", None),
    ("kiln project intake answer <question-id> \"<words>\"", None),
    ("kiln dataset materialize 0.3.0 --into <dir>", None),
    ("easel runs list", None),                                # not a kiln line
])
def test_task_of_names_what_is_not_built(command: str, task: str | None):
    assert planned.task_of(command) == task
    assert planned.mark(command) == (f"{command}  # planned: {task}" if task else command)


def test_every_first_command_is_built_or_marked():
    for stage, command in next_mod.FIRST_COMMAND.items():
        line = next_mod._stage(stage, "test").next
        task = planned.task_of(command)
        assert line == (f"{command}  # planned: {task}" if task else command), stage
        verb = command.split()[1]
        row = next(c for c in cli.COMMANDS if c.verb == verb)
        if not cli.module_exists(row.module):
            assert line.endswith(f"# planned: {row.planned}"), stage


def test_the_evaluate_stage_marks_its_planned_verb():
    line = next_mod._stage("09-evaluate", "test").lines()
    if not cli.module_exists("kiln.commands.eval"):
        assert line[2] == "next: kiln eval --help  # planned: session-2"
    if not (RUNBOOKS / "09-evaluate.md").is_file():
        assert line[1] == "read: 09-evaluate is planned"


def test_json_carries_the_runbook_and_the_planned_task(demo_workspace: Path, capsys):
    import json

    code, out = run(["next", "--json"], capsys)
    data = json.loads(out[0])
    assert set(data) == {"stage", "read", "runbook", "next", "next_planned", "why", "project"}
    assert data["runbook"] == f"docs/runbooks/{data['stage']}.md" and data["next_planned"] is None


def test_no_instance_line_names_init_as_planned_while_it_is(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KILN_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    code, out = run(["project", "show"], capsys)
    assert code == 2, out
    task = planned.verb_task("init")
    if task:
        assert out[1] == (f"`kiln init <folder>` is planned ({task}): ask the operator for the instance folder, "
                          "then pass --workspace <folder that holds kiln.toml> or set KILN_HOME"), out
    else:
        assert out[1].startswith("create one with `kiln init <folder>`"), out
