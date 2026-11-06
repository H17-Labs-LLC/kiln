"""`kiln runs list --family <f> --status ok` on the demo: the finished run whose base `train plan` can copy."""

import json
from pathlib import Path

from kiln_project_helpers import SLUG, run

DEMO_RUN = "run-20300102-001"


def test_lists_the_finished_run_of_a_family(demo_workspace: Path, capsys):
    code, out = run(["runs", "list", "--family", "illustrious", "--status", "ok"], capsys)
    assert code == 0, out
    assert out[0] == (f"{DEMO_RUN} | ok | illustrious | {SLUG}/0.2.0 | base Demo Anime XL (licence unverified) | "
                      "steps 1000/1000 | picked 800")
    assert out[1] == "1 run(s) (family illustrious, status ok)"
    assert out[2] == f"next: kiln train plan <version> --family illustrious --base-from-run {DEMO_RUN} --steps <n>"


def test_no_match_names_the_way_forward(demo_workspace: Path, capsys):
    code, out = run(["runs", "list", "--family", "pony", "--status", "ok"], capsys)
    assert code == 0, out
    assert out[0] == "0 run(s) (family pony, status ok)"
    assert out[1].startswith("next: ask the operator for the base")


def test_json_and_status_filter(demo_workspace: Path, capsys):
    code, out = run(["runs", "list", "--json"], capsys)
    assert code == 0
    data = json.loads("\n".join(out))
    assert [r["id"] for r in data["runs"]] == [DEMO_RUN]
    assert data["runs"][0]["licence"] == "unverified" and data["runs"][0]["selected_checkpoint"] == 800
    code, out = run(["runs", "list", "--status", "planned"], capsys)
    assert code == 0 and out == ["0 run(s) (status planned)"]
    code, out = run(["runs", "list", "--status", "finished"], capsys)
    assert code == 2  # argparse: not a status


def test_a_planned_run_is_listed(demo_workspace: Path, capsys, frozen_clock):
    code, out = run(["train", "plan", "0.2.0", "--family", "illustrious", "--base-from-run", DEMO_RUN,
                     "--steps", "400"], capsys)
    assert code == 0, out
    code, out = run(["runs", "list", "--status", "planned"], capsys)
    assert code == 0, out
    assert out[0].startswith("run-20300101-001 | planned | illustrious | demo_robot/0.2.0 | base Demo Anime XL")
    assert out[0].endswith("steps -/400 | picked -")


def test_an_unreadable_record_is_named_not_listed(demo_workspace: Path, capsys):
    broken = demo_workspace / "projects" / SLUG / "runs" / "run-20300103-001"
    broken.mkdir(parents=True)
    (broken / "run.json").write_text("{ not json", encoding="utf-8")
    code, out = run(["runs", "list"], capsys)
    assert code == 0
    assert any(line.startswith("warn: projects/demo_robot/runs/run-20300103-001/run.json") for line in out), out
    assert out[-1] == "1 run(s)"


def test_session_two_actions_say_planned(capsys):
    for argv in (["runs", "leaderboard", "--family", "illustrious"], ["runs", "unevaluated"]):
        code, out = run(argv, capsys)
        assert code == 2 and out[0].startswith("planned: session-2 ("), (argv, out)
