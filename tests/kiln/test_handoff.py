"""`kiln status --handoff`: the Now block regenerated from the project's files and from `kiln next`."""

import argparse
import json
from pathlib import Path

import pytest

from kiln import cli, handoff
from kiln._common import ids

PROJECT = Path("projects") / "demo_robot"


def fake_next(stage: str = "05-captions", nxt: str = "kiln captions status"):
    """A stand-in for `kiln next` that answers in its printed contract and records what it was asked."""
    calls: list[list[str]] = []

    def asker(argv):
        calls.append(list(argv))
        if "--stage" in argv:
            return 0, f"{stage}\n", ""
        return 0, f"stage: {stage}\nread: docs/runbooks/{stage}.md\nnext: {nxt}\n", ""

    asker.calls = calls
    return asker


def status_args(*argv: str) -> argparse.Namespace:
    """The `status` parser as the project store would build it, with this module's two options added."""
    parser = argparse.ArgumentParser(prog="kiln status")
    parser.set_defaults(tool="kiln")
    parser.add_argument("--project", default=None)
    parser.add_argument("--workspace", default=None)
    handoff.add_handoff_args(parser)
    return parser.parse_args(list(argv))


def project(home: Path) -> handoff.Project:
    return handoff.locate_project("kiln", home, None)


def plant_run(home: Path, run_id: str, status: str) -> None:
    demo = json.loads((home / PROJECT / "runs" / "run-20300102-001" / "run.json").read_text(encoding="utf-8"))
    demo.update({"id": run_id, "status": status})
    folder = home / PROJECT / "runs" / run_id
    folder.mkdir()
    (folder / "run.json").write_text(json.dumps(demo, indent=2), encoding="utf-8")


def test_now_block_on_the_demo(demo_workspace: Path) -> None:
    asker = fake_next()
    block = handoff.now_block(project(demo_workspace), asker=asker)
    assert block.lines() == [
        "## Now",
        "stage: 05-captions",
        "next: kiln captions status",
        "running: nothing",
        "waiting on: operator yes, for dec-0002 (Retrain 0.3.0: continue or start fresh)",
    ]
    assert asker.calls == [["--stage"], []]


def test_workspace_and_project_reach_next(demo_workspace: Path) -> None:
    asker = fake_next()
    handoff.now_block(project(demo_workspace), workspace=demo_workspace, slug="demo_robot", asker=asker)
    assert asker.calls[0] == ["--stage", "--workspace", str(demo_workspace), "--project", "demo_robot"]


def test_running_runs_and_more_open_decisions(demo_workspace: Path, frozen_clock, capsys) -> None:
    plant_run(demo_workspace, "run-20300105-001", "running")
    plant_run(demo_workspace, "run-20300105-002", "planned")
    plant_run(demo_workspace, "run-20300105-003", "failed")
    assert cli.main(["decide", "open", "Steps", "--question", "How many steps?"]) == 0
    block = handoff.now_block(project(demo_workspace), asker=fake_next())
    assert block.running == "run-20300105-001 (running), run-20300105-002 (planned)"
    # the frozen clock opens dec-0003 on 2030-01-01, before dec-0002 (2030-01-04): the oldest open one comes first
    assert block.waiting_on == "operator yes, for dec-0003 (Steps) and 1 more (kiln decide list --open)"


def test_no_open_decision_waits_on_nothing(demo_workspace: Path, capsys) -> None:
    assert cli.main(["decide", "dec-0002", "continue"]) == 0
    assert handoff.now_block(project(demo_workspace), asker=fake_next()).waiting_on == "nothing (no open decision)"
    (demo_workspace / PROJECT / "decisions" / "dec-0003.md").write_text("not a record\n", encoding="utf-8")
    assert handoff.now_block(project(demo_workspace), asker=fake_next()).waiting_on == (
        "nothing (no open decision; 1 decision file unreadable (kiln decide list))")


def test_next_failing_or_planned_shows_unknown(demo_workspace: Path) -> None:
    def broken(argv):
        return 2, "planned: T7 (print the current stage)\n", ""

    block = handoff.now_block(project(demo_workspace), asker=broken)
    assert block.stage == "unknown (`kiln next --stage` exit 2: planned: T7 (print the current stage))"
    assert block.next == "unknown (`kiln next` exit 2: planned: T7 (print the current stage))"

    def garbled(argv):
        return 0, "two\nlines\n", f"clock frozen ({ids.now_var('kiln')} set)\n"  # built at run time: no literal

    block = handoff.now_block(project(demo_workspace), asker=garbled)
    assert block.stage == "unknown (`kiln next --stage` exit 0: two)"


def test_the_real_next_is_asked_in_process(demo_workspace: Path) -> None:
    """Whatever `kiln next` prints today (built or still planned), the block carries it or says unknown."""
    stage, nxt = handoff.next_lines(None, None)
    code, out, _ = handoff.ask_next(["--stage"])
    if code == 0 and len(out.split()) == 1:
        assert stage == out.strip()
    else:
        assert stage.startswith("unknown (`kiln next --stage` exit ")
    assert nxt and "\n" not in nxt


def test_run_handoff_prints_and_writes_into_a_new_file(demo_workspace: Path, frozen_clock, capsys) -> None:
    assert handoff.wants_handoff(status_args("--handoff"))
    assert not handoff.wants_handoff(status_args())
    code = handoff.run_handoff(status_args("--handoff", "--into", "HANDOFF-demo.md"), asker=fake_next())
    out = capsys.readouterr().out
    assert code == 0
    assert out.splitlines()[0] == "## Now"
    assert out.splitlines()[-1] == "Now block written to HANDOFF-demo.md"
    text = (demo_workspace / "HANDOFF-demo.md").read_text(encoding="utf-8")
    assert text.splitlines()[:3] == ["# Hand-off 2030-01-01 00:00 kiln demo_robot", "", "## Now"]
    assert text.endswith("waiting on: operator yes, for dec-0002 (Retrain 0.3.0: continue or start fresh)\n")


def test_run_handoff_replaces_only_the_now_section(demo_workspace: Path, capsys) -> None:
    target = demo_workspace / "HANDOFF-demo.md"
    target.write_text("# Hand-off 2030-01-01 00:00 kiln demo_robot\n\n## Now\nstage: old\nnext: old\n\n"
                      "## Done this session\n- `kiln coverage needs` -> 3 open gaps (exit 0)\n", encoding="utf-8")
    assert handoff.run_handoff(status_args("--into", "HANDOFF-demo.md"), asker=fake_next()) == 0
    capsys.readouterr()
    assert target.read_text(encoding="utf-8").splitlines() == [
        "# Hand-off 2030-01-01 00:00 kiln demo_robot",
        "",
        "## Now",
        "stage: 05-captions",
        "next: kiln captions status",
        "running: nothing",
        "waiting on: operator yes, for dec-0002 (Retrain 0.3.0: continue or start fresh)",
        "",
        "## Done this session",
        "- `kiln coverage needs` -> 3 open gaps (exit 0)",
    ]


@pytest.mark.parametrize("before, after", [
    ("# T\n\nbody\n", "# T\n\n## Now\nstage: s\nnext: n\nrunning: r\nwaiting on: w\n\nbody\n"),
    ("# T\n", "# T\n\n## Now\nstage: s\nnext: n\nrunning: r\nwaiting on: w\n"),
    ("", "## Now\nstage: s\nnext: n\nrunning: r\nwaiting on: w\n"),
    ("## Now\nstage: x\n", "## Now\nstage: s\nnext: n\nrunning: r\nwaiting on: w\n"),
])
def test_replace_now_shapes(before: str, after: str) -> None:
    assert handoff.replace_now(before, handoff.NowBlock("s", "n", "r", "w")) == after


def test_add_handoff_args_keeps_an_existing_option() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--handoff", action="store_true")
    handoff.add_handoff_args(parser)  # no "conflicting option string" error
    assert parser.parse_args(["--handoff", "--into", "x.md"]).into == "x.md"
    assert handoff.run is handoff.run_handoff


def test_run_handoff_without_an_instance_exits_2(tmp_path: Path, capsys) -> None:
    code = handoff.run_handoff(status_args("--handoff", "--workspace", str(tmp_path / "nowhere")), asker=fake_next())
    assert code == 2 and capsys.readouterr().err.strip()
